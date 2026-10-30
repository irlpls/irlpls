"""ICEYE Tasking API v2: task a SAR satellite (sees through cloud and at night). Contract only: ICEYE issues a token URL,
client id and secret during onboarding (https://www.iceye.com/contact/sar-data). Set ICEYE_CLIENT_ID,
ICEYE_CLIENT_SECRET and ICEYE_TOKEN_URL; ICEYE_CONTRACT_ID (or spec["contract_id"]) names the contract every call needs.

Sources (read 2026-10-28):
- Authentication: client-credentials grant, client id and secret as HTTP Basic, token lasts 3600 s
  https://docs.iceye.com/constellation/api/getting-started/authentication/
- Price: GET /tasking/v2/price, query contractID, pointOfInterest[lat|lon], imagingMode, optional acquisitionWindow,
  exclusivity, priority, sla, eula -> {amount, currency}, amount in minor units; 403 when the contract has no pricing
  plan or does not allow the parameters  https://docs.iceye.com/constellation/api/tasking/get-task-price/
- Create: POST /tasking/v2/tasks {contractID, pointOfInterest, acquisitionWindow (>= 24 h wide, end <= 14 days),
  imagingMode, reference ...} -> 201 status RECEIVED; 400 ERR_INSUFFICIENT_FUNDS
  https://docs.iceye.com/constellation/api/tasking/create-task/
- Feasibility (array of tasks -> {feasibility: [{id, result: FEASIBLE|INFEASIBLE}]}), statuses, scene, products
  (signed URLs, 1 h), cancel (PATCH {"status": "CANCELED"} while RECEIVED or ACTIVE)
  https://docs.iceye.com/constellation/api/how-to/build-tasking-application/
Fixtures in tests/fixtures/adapters/iceye/ follow these documented shapes (fixture: documented-shape)."""
import re
import urllib.parse
from decimal import Decimal

from .. import http
from . import _a9
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, minor_to_major, observation

BASE = "https://platform.iceye.com/api"
DOCS = "https://docs.iceye.com/constellation/api/tasking/"
ONBOARD = "https://www.iceye.com/contact/sar-data"
TOKEN_NOTES = ("Token: POST your ICEYE token URL (ICEYE_TOKEN_URL, issued at onboarding) with grant_type=client_credentials "
               "and HTTP Basic ICEYE_CLIENT_ID:ICEYE_CLIENT_SECRET; use access_token (1 h) as the bearer token.")
CONTRACT = _a9.ph("ICEYE_CONTRACT_ID", "your ICEYE contract id (every tasking call needs one)",
                  "GET https://platform.iceye.com/api/company/v1/contracts (field data[].id)")


class Iceye(Adapter):
    card_id = "iceye"
    env = "ICEYE_CLIENT_ID"
    how_to_get = ("ICEYE issues ICEYE_TOKEN_URL, ICEYE_CLIENT_ID and ICEYE_CLIENT_SECRET on a contract; start at "
                  "https://www.iceye.com/contact/sar-data. Set all three, and ICEYE_CONTRACT_ID.")
    can_commission = True
    can_cancel = True
    _tokens = TokenCache()

    def configured(self):
        return all(_a9.env(n) for n in ("ICEYE_CLIENT_ID", "ICEYE_CLIENT_SECRET", "ICEYE_TOKEN_URL"))

    def _h(self):
        cid, secret, url = self.key(), _a9.env("ICEYE_CLIENT_SECRET"), _a9.env("ICEYE_TOKEN_URL")
        if not (secret and url):
            raise MissingCredentials(f"iceye: set ICEYE_CLIENT_SECRET and ICEYE_TOKEN_URL too. {self.how_to_get}")
        return {"Authorization": "Bearer " + self._tokens.client_credentials(url, cid, secret, basic=True)}

    # ---- building the task
    def _task(self, spec, contract):
        start, end = _a9.window(spec, lead_h=24, min_width_h=24, max_end_h=14 * 24 - 1, default_width_h=72)
        t = {"contractID": contract, "pointOfInterest": {"lat": spec["lat"], "lon": spec["lon"]},
             "acquisitionWindow": {"start": _a9.iso(start), "end": _a9.iso(end)},
             "imagingMode": spec.get("imaging_mode", "SPOTLIGHT")}
        for k, f in (("priority", "priority"), ("sla", "sla"), ("exclusivity", "exclusivity"), ("eula", "eula")):
            if spec.get(k):
                t[f] = spec[k]
        ref = spec.get("reference") or spec.get("idempotency_key") or spec.get("external_id")
        if ref:
            t["reference"] = str(ref)[:256]
        return t

    @staticmethod
    def _price_query(task):
        """Query string for GET /tasking/v2/price in the documented form (the contract id is left as given, so a
        "{ICEYE_CONTRACT_ID}" placeholder stays readable)."""
        w = task["acquisitionWindow"]
        pairs = [("pointOfInterest[lat]", task["pointOfInterest"]["lat"]), ("pointOfInterest[lon]", task["pointOfInterest"]["lon"]),
                 ("imagingMode", task["imagingMode"]), ("acquisitionWindow", f"{{'start': '{w['start']}', 'end': '{w['end']}'}}")]
        pairs += [(k, task[k]) for k in ("priority", "sla", "exclusivity", "eula") if task.get(k)]
        q = "&".join(urllib.parse.quote(k, safe="[]") + "=" + urllib.parse.quote(str(v), safe="") for k, v in pairs)
        return "contractID=" + task["contractID"] + "&" + q

    # ---- ready calls
    def prepare(self, req, route, operation=None, order_id=None):
        """Default: the price quotation (GET, books nothing; needs a contract with a pricing plan). operation="order" is
        createTask (costs money), "status" is getTask."""
        if route != "commission" or not _a9.has_point(req):
            return None
        op = operation or "quote"
        task = self._task(req, "{ICEYE_CONTRACT_ID}")
        common = dict(token_name="ICEYE_ACCESS_TOKEN", token_notes=TOKEN_NOTES, get_key_url=ONBOARD, docs_url=DOCS)
        if op == "quote":
            return _a9.oauth_call(self.card_id, "quote", "GET", f"{BASE}/tasking/v2/price?{self._price_query(task)}",
                                  placeholders=[CONTRACT], notes="Returns {amount, currency} with amount in minor units "
                                  "(529199 USD = $5,291.99); 403 if the contract has no pricing plan or does not allow "
                                  "these parameters. The imaging mode must be one your contract allows. POST "
                                  "/tasking/v2/feasibility with [task] checks the schedule without booking.", **common)
        if op == "order":
            return _a9.oauth_call(self.card_id, "order", "POST", f"{BASE}/tasking/v2/tasks", body=task, placeholders=[CONTRACT],
                                  notes="Creates the task (201, status RECEIVED) and commits contract budget; get the "
                                        "price first. Cancelling is free only within 1 h of submission or > 72 h "
                                        "before the window.", **common)
        if op == "status":
            tid = order_id or "{ICEYE_TASK_ID}"
            pl = None if order_id else [_a9.ph("ICEYE_TASK_ID", "the task id createTask returned", "the order response's id")]
            return _a9.oauth_call(self.card_id, "status", "GET", f"{BASE}/tasking/v2/tasks/{tid}", placeholders=pl,
                                  notes="RECEIVED, ACTIVE, FULFILLED (SLA products ready), DONE; or REJECTED, CANCELED, "
                                        "FAILED. Products: GET /tasking/v2/tasks/{id}/products once FULFILLED.", **common)
        return None

    def spec_from_request(self, req, ext_id, deliver_by):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "within_minutes": req.get("within_minutes"),
             "deliver_by": deliver_by, "reference": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    # ---- order
    def commission(self, spec, confirm=False):
        """New SAR task over a point. spec: lat, lon, contract_id (or ICEYE_CONTRACT_ID), imaging_mode ("SPOTLIGHT"),
        optional priority, sla, exclusivity, eula, within_minutes / deliver_by / window_days, reference.
        Without confirm: with credentials set, asks ICEYE for the price (and feasibility) and books nothing."""
        contract = spec.get("contract_id") or _a9.env("ICEYE_CONTRACT_ID")
        missing = [m for m, ok in (("lat", spec.get("lat") is not None), ("lon", spec.get("lon") is not None),
                                   ("contract_id", contract)) if not ok]
        if missing:
            return _a9.dry("POST", f"{BASE}/tasking/v2/tasks", None, missing=missing)
        task = self._task(spec, contract)
        if confirm:
            return _a9.call(self.card_id, http.post, f"{BASE}/tasking/v2/tasks", body=task, headers=self._h(),
                            creds=self.how_to_get)
        out = _a9.dry("POST", f"{BASE}/tasking/v2/tasks", task)
        if not self.configured() or spec.get("quote") is False:
            return out
        h, warns = self._h(), []
        try:
            p = http.get(f"{BASE}/tasking/v2/price?{self._price_query(task)}", headers=h)
            out["quote"] = self._quote(p)
        except http.HTTPError as e:
            warns.append(_a9.http_warning(self.card_id, e, "a price (403: the contract has no pricing plan or does not allow these parameters)"))
        if spec.get("feasibility", True):
            try:
                out["feasibility"] = http.post(f"{BASE}/tasking/v2/feasibility", body=[task], headers=h)
            except http.HTTPError as e:
                warns.append(_a9.http_warning(self.card_id, e, "feasibility"))
        if warns:
            out["warnings"] = warns
        return out

    @staticmethod
    def _quote(p):
        """ICEYE's price, kept as sent. Only a USD amount becomes a total (minor units / 100, a unit change, not a
        currency conversion); any other currency goes in provider_total (major units, its own currency), so the
        offer shows it but is never firm."""
        q = {"amount_minor": p.get("amount"), "currency": p.get("currency"), "source": "GET /tasking/v2/price"}
        a = p.get("amount")
        if p.get("currency") == "USD" and isinstance(a, int) and not isinstance(a, bool) and a >= 0:
            q["total"] = float(Decimal(a) / 100)
        elif isinstance(p.get("currency"), str) and re.fullmatch(r"[A-Z]{3}", p["currency"]):
            q["provider_total"] = minor_to_major(a, p["currency"])   # shown in its own currency, never firm
            if q["provider_total"] is None:
                del q["provider_total"]
        return q

    def status(self, order):
        tid = order.get("id") or order.get("taskID")
        h = self._h()
        resp = _a9.call(self.card_id, http.get, f"{BASE}/tasking/v2/tasks/{tid}", headers=h, creds=self.how_to_get)
        if isinstance(resp, dict) and str(resp.get("status", "")).upper() in ("FULFILLED", "DONE"):
            for key, path in (("scene", "scene"), ("products", "products")):
                try:
                    resp[key] = http.get(f"{BASE}/tasking/v2/tasks/{tid}/{path}", headers=h)
                except http.HTTPError as e:
                    resp.setdefault("warnings", []).append(_a9.http_warning(self.card_id, e, path))
        return resp

    # Statuses from the tasking guide: RECEIVED, ACTIVE, FULFILLED (SLA products ready), DONE (all products);
    # terminal REJECTED, CANCELED, FAILED.
    status_map = {"received": "in_flight", "active": "in_flight", "fulfilled": "delivered", "done": "delivered",
                  "rejected": "failed", "canceled": "failed", "failed": "failed"}

    def deliverables(self, resp):
        resp = resp or {}
        poi = resp.get("pointOfInterest") or {}
        t = ((resp.get("scene") or {}).get("imagingTime") or {}).get("start")
        out = []
        for p in ((resp.get("products") or {}).get("data") or []):
            out.append(observation(self.card_id, "sar-product", t, poi.get("lat"), poi.get("lon"), label=p.get("productType"),
                                   url=p.get("url"), url_expires_at=p.get("expiresAt"), product_id=p.get("id"),
                                   task_id=resp.get("id")))
        return out

    def cancel(self, order):
        """PATCH the task to CANCELED (allowed while RECEIVED or ACTIVE; fees per the card's pricing notes)."""
        tid = order.get("id") or order.get("taskID")
        return _a9.call(self.card_id, http.request, "PATCH", f"{BASE}/tasking/v2/tasks/{tid}", body={"status": "CANCELED"},
                        headers=self._h(), creds=self.how_to_get)
