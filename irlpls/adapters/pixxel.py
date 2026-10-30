"""Pixxel Aurora: task Pixxel's hyperspectral satellites. Prices in tokens, and Pixxel defines 1 token = 1 USD.

Sources (public docs, read 2026-10-28):
- OpenAPI (server https://api.pixxel.space, Authorization header, POST /v0/orders with dry_run and order_items, 201
  response with total_amount and order_items[].status DRAFT|ACTIVE|FULFILLED|CANCELLED|FAILED|PARTIALLY_FULFILLED and
  feasibility_status; order-level status is the payment status PAID|REFUND|UNPAID|PAYMENT_IN_PROGRESS):
  https://docs.pixxel.space/openapi.json
- Tasking order guide: dry_run "validates all the parameters ... checking that you have sufficient tokens ... and running
  feasibility checks ... The only difference is that the order itself is not actually placed"; bandset, single-polygon
  FeatureCollection, under 100 km2 billed as 100 km2, start at least 24 h out, project_id, recurrence:
  https://docs.pixxel.space/developer/orders/tasking.md
- Personal access token sent as Authorization: Bearer: https://docs.pixxel.space/developer/gettingstarted/authentication.md
- Tokens: "Each token is equivalent to $1 USD":
  https://docs.pixxel.space/documentation/getting-started/pricing-and-refunds/tokens.md
- KYB/KYC before the Order Desk: https://docs.pixxel.space/documentation/tasking/access-order-desk/kyb.md

No cancel endpoint is documented, so can_cancel stays False. Keys: PIXXEL_API_TOKEN, PIXXEL_PROJECT_ID (GET
/v0/projects), PIXXEL_BANDSET_ID (GET /v0/bandsets); spec["project_id"] / spec["bandset_id"] override them.
"""
import os
from decimal import Decimal

from . import _a8
from .base import Adapter, MissingCredentials, observation, prepared_call

BASE = "https://api.pixxel.space"
DOCS = "https://docs.pixxel.space/developer/orders/tasking"
GET_KEY = "https://aurora.pixxel.space"


class Pixxel(Adapter):
    card_id = "pixxel"
    env = "PIXXEL_API_TOKEN"
    env_project = "PIXXEL_PROJECT_ID"
    env_bandset = "PIXXEL_BANDSET_ID"
    how_to_get = ("In Aurora (https://aurora.pixxel.space): Settings > Personal Access Tokens. Ordering needs KYB or KYC "
                  "approval and tokens in the wallet; set PIXXEL_PROJECT_ID and PIXXEL_BANDSET_ID too.")
    can_commission = True

    def _h(self, required=True):
        return {"Authorization": f"Bearer {self.key(required)}"}

    def _call(self, method, path, **kw):
        return _a8.call(self.card_id, method, BASE + path, self.env, self.how_to_get, [os.environ.get(self.env)], **kw)

    def spec_from_request(self, req, ext_id, deliver_by):
        return _a8.spec_from_request(req, ext_id, deliver_by)

    def _body(self, spec, project, bandset, dry_run):
        start, end = _a8.window(spec, lead_h=24, default_days=14)
        item = {"name": (spec.get("name") or "irlpls tasking")[:100], "project_id": project,
                "geometry": _a8.feature_collection(_a8.polygon(spec["lat"], spec["lon"], spec.get("radius_km", 5))),
                "bandset": {"bandset_id": bandset}, "recurrence": "ONCE", "start_date": _a8.iso(start), "end_date": _a8.iso(end),
                "cloud_cover": spec.get("cloud_cover", 20), "delivery_speed": "STANDARD", "usecase": spec.get("usecase", "other")}
        return {"dry_run": bool(dry_run), "order_items": [item]}

    def commission(self, spec, confirm=False):
        """Single tasking capture over a square AOI (radius_km, default 5: 100 km2, Pixxel's billing minimum).
        Without confirm: the order it would place plus, with a token, Pixxel's own dry run (feasibility + wallet check +
        total_amount, firm because 1 token = 1 USD). With confirm: places the order and deducts tokens."""
        missing = _a8.missing_point(spec)
        if missing:
            return {"dry_run": True, "missing": missing, "note": "Nothing was sent: the place is needed."}
        project = spec.get("project_id") or os.environ.get(self.env_project)
        bandset = spec.get("bandset_id") or os.environ.get(self.env_bandset)
        if not confirm:
            body = self._body(spec, project or "{" + self.env_project + "}", bandset or "{" + self.env_bandset + "}", False)
            out = self._dry("POST", f"{BASE}/v0/orders", body, self._h(required=False))
            if not self.configured():
                return out
            gaps = [m for m, v in (("project_id: a Pixxel project id (GET /v0/projects)", project),
                                   ("bandset_id: a Pixxel bandset id (GET /v0/bandsets)", bandset)) if not v]
            if gaps:
                out["missing"] = gaps
                return out
            q = self._call("POST", "/v0/orders", body=self._body(spec, project, bandset, True), headers=self._h())
            out["feasibility"] = [i.get("feasibility_status") for i in (q or {}).get("order_items") or [] if isinstance(i, dict)]
            return _a8.put_quote(out, (q or {}).get("total_amount"), "USD", q)
        if not project or not bandset:
            raise MissingCredentials(f"pixxel: set {self.env_project} and {self.env_bandset} (or pass them in spec) to order.")
        return self._call("POST", "/v0/orders", body=self._body(spec, project, bandset, False), headers=self._h())

    def order_total(self, resp):
        """total_amount is in tokens; Pixxel states 1 token = 1 USD."""
        v = (resp or {}).get("total_amount") if isinstance(resp, dict) else None
        return Decimal(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None

    def status(self, order):
        oid = order.get("id") if isinstance(order, dict) else None
        if not oid:
            raise ValueError("pixxel: the order has no id")
        return self._call("GET", f"/v0/orders/{oid}", headers=self._h())

    # Fulfilment lives on order_items[].status (the order's own status is its payment state). One word for the order:
    # a failed or cancelled item wins, then anything still open, then FULFILLED when every item is.
    status_map = {"draft": "in_flight", "active": "in_flight", "partially_fulfilled": "in_flight", "fulfilled": "delivered",
                  "cancelled": "failed", "failed": "failed"}

    def provider_status(self, resp):
        items = [i for i in (resp or {}).get("order_items") or [] if isinstance(i, dict)] if isinstance(resp, dict) else []
        ss = [str(i.get("status")).upper() for i in items if isinstance(i.get("status"), str)]
        if not ss:
            return None
        for s in ("FAILED", "CANCELLED"):
            if s in ss:
                return s
        open_ = [s for s in ss if s != "FULFILLED"]
        return open_[0] if open_ else "FULFILLED"

    def deliverables(self, resp):
        out = []
        for it in (resp or {}).get("order_items") or [] if isinstance(resp, dict) else []:
            if not isinstance(it, dict) or str(it.get("status")).upper() != "FULFILLED":
                continue
            for img in it.get("images") or []:
                if isinstance(img, dict) and img.get("item_id"):
                    out.append(observation(self.card_id, "satellite-tasking", None, None, None,
                                           label=f"Pixxel image {img['item_id']}", order_id=resp.get("id"),
                                           order_item_id=it.get("id"), stac_item_id=img["item_id"]))
        return out

    # ---- ready calls
    def prepare(self, req, route, operation=None):
        """read: bandsets. quote: POST /v0/orders with dry_run true. order: the same with dry_run false. status: one order."""
        op = operation or ("quote" if route == "commission" else None)
        auth = dict(auth_kind="bearer", key_name=self.env, key_in="header Authorization: Bearer", get_key_url=GET_KEY, docs_url=DOCS)
        h = {"Authorization": "Bearer {" + self.env + "}"}
        if op == "read":
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/v0/bandsets", headers=h, **auth,
                                 notes="Bandsets for a tasking item; GET /v0/projects lists the project ids an order needs.")
        if op == "status":
            oid = req.get("order_id") or "{ORDER_ID}"
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/v0/orders/{oid}", headers=h, **auth,
                                 placeholders=None if req.get("order_id") else [{"name": "ORDER_ID", "what": "Pixxel order id", "where": "id of the order response"}],
                                 notes="Fulfilment is order_items[].status (DRAFT, ACTIVE, PARTIALLY_FULFILLED, FULFILLED, "
                                       "CANCELLED, FAILED); the order's own status is its payment state.")
        if op not in ("quote", "order") or req.get("lat") is None or req.get("lon") is None:
            return None
        spec = {"lat": req["lat"], "lon": req["lon"], "name": req.get("need")}
        body = self._body(spec, "{" + self.env_project + "}", "{" + self.env_bandset + "}", op == "quote")
        ph = [{"name": self.env_project, "what": "Pixxel project id", "where": "GET /v0/projects"},
              {"name": self.env_bandset, "what": "bandset id", "where": "GET /v0/bandsets"}]
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/v0/orders", headers=h, body=body, **auth, placeholders=ph,
                                 notes="dry_run true: validates, runs feasibility and checks the wallet without placing the "
                                       "order. total_amount is in tokens (1 token = 1 USD). AOIs under 100 km2 bill as 100 km2.")
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/v0/orders", headers=h, body=body, **auth, placeholders=ph,
                             notes="dry_run false places the order and deducts tokens. Needs KYB/KYC approval. No idempotency "
                                   "key or cancel endpoint is documented.")
