"""Capella Space: task a SAR satellite. Contract only: an API key needs an active Capella Console account (Account
Profile > API Keys), which follows a signed order form. Key in CAPELLA_API_KEY, sent as "Authorization: ApiKey <key>".

Capella has no quote endpoint. POST /task creates a tasking request; unless it is pre-approved (by the request's
preApproval flag or the account's set-up) it waits in "review" with a cost estimate until the customer approves it.
On a pre-approved account the same call schedules at once, so it is never used as a quote: commission() without
confirm sends nothing, and with confirm it always sends preApproval: false. Approving the estimate is a second,
explicit step: approve(order, confirm=True).

Sources (read 2026-10-28):
- POST https://api.capellaspace.com/task: GeoJSON Feature, properties windowOpen, windowClose, collectionTier
  (urgent | priority | standard | flexible), collectionType, collectConstraints, preApproval ("will skip the tasking
  request cost review step if set to true"), customAttribute1/2  https://docs.capellaspace.com/constellation-tasking/tasking-requests
- Estimate: GET /task/{id}, transactionStatus "pending-customer-approval", order.summary.total ("$1,000.00");
  PATCH /task/{id}/status {"status": "approved" | "rejected"}  https://docs.capellaspace.com/constellation-tasking/cost-review-and-approval
- Status: statusHistory newest first, codes received, review, submitted, active, accepted, rejected, expired,
  completed, canceled, error, anomaly, retrying  https://docs.capellaspace.com/constellation-tasking/tasking-request-status/
- API key: "Authorization: ApiKey {apiKey}", valid 90 days from creation or last use
  https://docs.capellaspace.com/authentication/requesting-api-key
The docs show statusHistory and the estimate without their enclosing object; the API returns the request as a GeoJSON
Feature, so both the Feature's properties and the top level are read. Fixtures in tests/fixtures/adapters/capella/
follow the documented fields (fixture: documented-shape)."""
from .. import http
from . import _a9
from .base import Adapter, prepared_call

BASE = "https://api.capellaspace.com"
DOCS = "https://docs.capellaspace.com/constellation-tasking/tasking-requests"
GET_KEY = "https://docs.capellaspace.com/authentication/requesting-api-key"
NO_QUOTE = ("Capella has no quote call: this creates a tasking request. With preApproval false it waits in 'review' "
            "with a cost estimate (GET /task/{taskingRequestId}: order.summary.total) and is scheduled only after PATCH "
            "/task/{id}/status {\"status\": \"approved\"}. On a pre-approved account it is scheduled at once, so treat "
            "it as an order.")


def _props(resp):
    p = resp.get("properties") if isinstance(resp, dict) else None
    return p if isinstance(p, dict) else {}


def _field(resp, k):
    """A field from the Feature's properties, else from the top level."""
    v = _props(resp).get(k)
    return v if v is not None else (resp.get(k) if isinstance(resp, dict) else None)


class Capella(Adapter):
    card_id = "capella"
    env = "CAPELLA_API_KEY"
    how_to_get = ("Needs an active Capella Console account (after a signed order form): Account Profile > API Keys > "
                  "Create. Keys last 90 days from creation or last use.")
    can_commission = True

    def _h(self):
        return {"Authorization": "ApiKey " + self.key()}

    def _task(self, spec):
        start, end = _a9.window(spec, lead_h=0.25, min_width_h=24, max_end_h=None, default_width_h=7 * 24)
        props = {"taskingrequestName": str(spec.get("name") or "irlpls tasking")[:120], "windowOpen": _a9.iso(start),
                 "windowClose": _a9.iso(end), "collectionTier": spec.get("collection_tier", "standard"),
                 "collectionType": spec.get("collection_type", "spotlight"), "preApproval": False}
        if spec.get("description"):
            props["taskingrequestDescription"] = str(spec["description"])[:500]
        if spec.get("collect_constraints"):
            props["collectConstraints"] = spec["collect_constraints"]
        ref = spec.get("reference") or spec.get("idempotency_key") or spec.get("external_id")
        if ref:
            props["customAttribute1"] = str(ref)[:120]
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [spec["lon"], spec["lat"]]}, "properties": props}

    def prepare(self, req, route, operation=None, order_id=None):
        """Default: the order call (POST /task with preApproval false): Capella's only route to a price. "status"
        is GET /task/{id}, which also carries the estimate once it is ready."""
        if route != "commission" or not _a9.has_point(req):
            return None
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header Authorization (ApiKey <key>)",
                    get_key_url=GET_KEY, docs_url=DOCS)
        h = {"Authorization": "ApiKey {" + self.env + "}"}
        op = operation or "order"
        if op == "order":
            return prepared_call(self.card_id, "order", "POST", f"{BASE}/task", headers=h, body=self._task(req), notes=NO_QUOTE,
                                 **auth)
        if op == "status":
            tid = order_id or "{CAPELLA_TASKING_REQUEST_ID}"
            pl = None if order_id else [_a9.ph("CAPELLA_TASKING_REQUEST_ID", "the tasking request id",
                                               "properties.taskingrequestId of the POST /task response")]
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/task/{tid}", headers=h, placeholders=pl,
                                 notes="statusHistory[0].code is the current status. At 'review', transactionStatus is "
                                       "'pending-customer-approval' and order.summary.total is the estimate.", **auth)
        return None

    def spec_from_request(self, req, ext_id, deliver_by):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "within_minutes": req.get("within_minutes"),
             "deliver_by": deliver_by, "reference": ext_id, "description": req.get("need")}
        return {k: v for k, v in s.items() if v is not None}

    def commission(self, spec, confirm=False):
        """Tasking request over a point. spec: lat, lon, collection_type ("spotlight"; see GET /collectiontypes),
        collection_tier ("standard"), collect_constraints, within_minutes / deliver_by / window_days, name, description.
        Without confirm nothing is sent, ever (POST /task is not a safe quote). With confirm, the request is created
        with preApproval false, so it waits for review unless the account itself is pre-approved."""
        missing = [m for m in ("lat", "lon") if spec.get(m) is None]
        if missing:
            return _a9.dry("POST", f"{BASE}/task", None, missing=missing)
        body = self._task(spec)
        if not confirm:
            return _a9.dry("POST", f"{BASE}/task", body, note="Nothing was sent: Capella has no quote call, and POST /task "
                           "may schedule at once on a pre-approved account. Pass confirm=True to create the request; the "
                           "estimate then appears in status() and approve(order, confirm=True) accepts it.")
        return _a9.call(self.card_id, http.post, f"{BASE}/task", body=body, headers=self._h(), creds=self.how_to_get)

    def pending_approvals(self, resp):
        """The cost estimate waiting for the customer (transactionStatus pending-customer-approval), as one option."""
        if _field(resp, "transactionStatus") != "pending-customer-approval":
            return []
        amt = self.estimate(resp)
        return [{"id": "estimate", "amount": amt, "currency": "USD" if amt is not None else None,
                 "label": "Capella cost estimate (order.summary.total); approving schedules the collection and is billed"}]

    def approve(self, order, confirm=False, approval_id=None):
        """Accept the cost estimate of a request in review (PATCH /task/{id}/status approved). Spends money: dry run
        unless confirm=True. approval_id is ignored (one estimate per request)."""
        url, body = f"{BASE}/task/{self._tid(order)}/status", {"status": "approved"}
        if not confirm:
            return _a9.dry("PATCH", url, body, note="Nothing was sent. Pass confirm=True to approve the estimate; it costs money.")
        return _a9.call(self.card_id, http.request, "PATCH", url, body=body, headers=self._h(), creds=self.how_to_get)

    @staticmethod
    def _tid(order):
        return _field(order, "taskingrequestId") or (order or {}).get("id")

    def status(self, order):
        return _a9.call(self.card_id, http.get, f"{BASE}/task/{self._tid(order)}", headers=self._h(), creds=self.how_to_get)

    def provider_status(self, resp):
        hist = _field(resp, "statusHistory")
        if isinstance(hist, list) and hist:
            newest = max((h for h in hist if isinstance(h, dict)), key=lambda h: str(h.get("time") or ""), default=None)
            code = (newest or {}).get("code")
            return code if isinstance(code, str) else None
        return None

    # The documented status codes. "review" (estimate ready, waiting for the customer) and the others before a final
    # code are in_flight; completed is delivered; rejected, expired, canceled, error and anomaly are failed.
    status_map = {**{s: "in_flight" for s in ("received", "review", "submitted", "active", "accepted", "retrying")},
                  "completed": "delivered", **{s: "failed" for s in ("rejected", "expired", "canceled", "error", "anomaly")}}

    def estimate(self, resp):
        """The documented cost estimate (USD, order.summary.total) once transactionStatus is pending-customer-approval."""
        if _field(resp, "transactionStatus") != "pending-customer-approval":
            return None
        o = _field(resp, "order") or {}
        return _a9.usd(((o.get("summary") or {}) if isinstance(o, dict) else {}).get("total"))

    def order_total(self, resp):
        return self.estimate(resp)
