"""Satellogic Aleph API v2: task a NewSat optical capture. Contract only: an Aleph user with the Imagery User role
creates an API credential (role "M2M Imagery Integrator v2", tied to a contract). Set SATELLOGIC_CLIENT_ID,
SATELLOGIC_CLIENT_SECRET and SATELLOGIC_CONTRACT_ID (sent as x-satellogic-contract-id on every call); the tasking SKU
comes from GET /v2/products (spec["sku"] or SATELLOGIC_SKU).

Sources (read 2026-10-28):
- OpenAPI 2.0.0-beta.3, server https://api.satellogic.com: POST /v2/orders (GeoJSON Feature, properties order_name,
  sku, parameters {start, end, processing_level, ...}); GET /v2/orders/{order_id} (Order Feature, properties.status
  received | in_progress | closed, properties.latest_event); DELETE /v2/orders/{order_id} (cancellation request, 204;
  409 when not allowed); GET /v2/orders/{order_id}/deliverables (status PROCESSING | DELIVERED | NOT_DELIVERED,
  assets.*.href); POST /v2/analysis/opportunities (same body as an order; "without actually creating an order";
  201 {analysis_id}); GET /v2/products  https://developers.satellogic.com/aleph-v2/api/satellogic_api_v2.yml
- Order states and events (Closed covers rejection, completion and cancellation; OrderClosed = "closed successfully",
  OrderAnalysisFailed, OrderForceClosed, OrderCancellationAccepted)
  https://developers.satellogic.com/aleph-v2/api/introduction.html
- Token: POST https://auth.platform.satellogic.com/oauth/token, client_credentials, audience
  https://api.satellogic.com/, 24 h token  https://developers.satellogic.com/api-authentication/get_access_token.html
No price endpoint exists, so a quote is capture opportunities only and is never firm.
Fixtures in tests/fixtures/adapters/satellogic/ are the OpenAPI examples (fixture: documented-shape)."""
from .. import http
from . import _a9
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, observation

BASE = "https://api.satellogic.com/v2"
TOKEN_URL = "https://auth.platform.satellogic.com/oauth/token"
AUDIENCE = "https://api.satellogic.com/"
DOCS = "https://developers.satellogic.com/aleph-v2/api/introduction.html"
GET_KEY = "https://developers.satellogic.com/api-authentication/create_credentials.html"
TOKEN_NOTES = (f"Token: POST {TOKEN_URL} with grant_type=client_credentials, client_id=SATELLOGIC_CLIENT_ID, "
               f"client_secret=SATELLOGIC_CLIENT_SECRET, audience={AUDIENCE}; use access_token (24 h) as the bearer token.")
CONTRACT = _a9.ph("SATELLOGIC_CONTRACT_ID", "your Satellogic contract id (header x-satellogic-contract-id)",
                  "Aleph Web, or GET https://api.satellogic.com/v2/my/contracts")
SKU = _a9.ph("SATELLOGIC_SKU", "a tasking SKU your contract holds (e.g. TSKPOI-M...)", "GET https://api.satellogic.com/v2/products")


class Satellogic(Adapter):
    card_id = "satellogic"
    env = "SATELLOGIC_CLIENT_ID"
    how_to_get = ("In Aleph Web (aleph.satellogic.com) create an API credential with role 'M2M Imagery Integrator v2' "
                  "(needs a contract; trial accounts cannot). Set SATELLOGIC_CLIENT_ID, SATELLOGIC_CLIENT_SECRET and "
                  "SATELLOGIC_CONTRACT_ID.")
    can_commission = True
    can_cancel = True
    _tokens = TokenCache()

    def configured(self):
        return all(_a9.env(n) for n in ("SATELLOGIC_CLIENT_ID", "SATELLOGIC_CLIENT_SECRET", "SATELLOGIC_CONTRACT_ID"))

    def _h(self, contract=None):
        cid, secret = self.key(), _a9.env("SATELLOGIC_CLIENT_SECRET")
        contract = contract or _a9.env("SATELLOGIC_CONTRACT_ID")
        if not (secret and contract):
            raise MissingCredentials(f"satellogic: set SATELLOGIC_CLIENT_SECRET and SATELLOGIC_CONTRACT_ID too. {self.how_to_get}")
        tok = self._tokens.client_credentials(TOKEN_URL, cid, secret, audience=AUDIENCE)
        return {"Authorization": "Bearer " + tok, "x-satellogic-contract-id": contract}

    def _order(self, spec, sku):
        start, end = _a9.window(spec, lead_h=1, min_width_h=24, max_end_h=None, default_width_h=7 * 24)
        params = {"start": _a9.iso(start), "end": _a9.iso(end)}
        for k in ("processing_level", "revisit_period", "min_ona", "max_ona", "min_sun_elevation", "max_sun_elevation"):
            if spec.get(k) is not None:
                params[k] = spec[k]
        name = spec.get("order_name") or spec.get("reference") or spec.get("idempotency_key") or "irlpls tasking"
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [spec["lon"], spec["lat"]]},
                "properties": {"order_name": str(name)[:120], "sku": sku, "parameters": params}}

    def prepare(self, req, route, operation=None, order_id=None):
        """Default: capture opportunities (POST /v2/analysis/opportunities: no order, no price). operation="order" is
        POST /v2/orders (costs money), "status" is GET /v2/orders/{id}."""
        if route != "commission" or not _a9.has_point(req):
            return None
        op = operation or "quote"
        hdr = {"x-satellogic-contract-id": "{SATELLOGIC_CONTRACT_ID}"}
        common = dict(token_name="SATELLOGIC_ACCESS_TOKEN", token_notes=TOKEN_NOTES, get_key_url=GET_KEY, docs_url=DOCS, headers=hdr)
        body = self._order(req, "{SATELLOGIC_SKU}")
        if op == "quote":
            return _a9.oauth_call(self.card_id, "quote", "POST", f"{BASE}/analysis/opportunities", body=body,
                                  placeholders=[CONTRACT, SKU], notes="Returns {analysis_id}; GET "
                                  "/v2/analysis/opportunities/{analysis_id} for the capture opportunities. Creates no "
                                  "order and gives no price (Satellogic has no price endpoint; the contract sets it).", **common)
        if op == "order":
            return _a9.oauth_call(self.card_id, "order", "POST", f"{BASE}/orders", body=body, placeholders=[CONTRACT, SKU],
                                  notes="Creates the tasking order (charged per the contract). Cancel with DELETE "
                                        "/v2/orders/{order_id} while received or in_progress.", **common)
        if op == "status":
            oid = order_id or "{SATELLOGIC_ORDER_ID}"
            pl = [CONTRACT] + ([] if order_id else [_a9.ph("SATELLOGIC_ORDER_ID", "the order id", "properties.order_id of the order response")])
            return _a9.oauth_call(self.card_id, "status", "GET", f"{BASE}/orders/{oid}", placeholders=pl,
                                  notes="properties.status received | in_progress | closed; properties.latest_event.type "
                                        "says why it closed. Files: GET /v2/orders/{order_id}/deliverables.", **common)
        return None

    def spec_from_request(self, req, ext_id, deliver_by):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "within_minutes": req.get("within_minutes"),
             "deliver_by": deliver_by, "reference": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    def commission(self, spec, confirm=False):
        """New tasking order over a point. spec: lat, lon, sku (or SATELLOGIC_SKU), optional contract_id, order_name,
        processing_level, revisit_period, min_ona/max_ona, sun elevations, within_minutes / deliver_by / window_days.
        Without confirm: with credentials set, runs an opportunities analysis (no order, no price) and places nothing."""
        sku = spec.get("sku") or _a9.env("SATELLOGIC_SKU")
        missing = [m for m, ok in (("lat", spec.get("lat") is not None), ("lon", spec.get("lon") is not None), ("sku", sku)) if not ok]
        if missing:
            return _a9.dry("POST", f"{BASE}/orders", None, missing=missing)
        body = self._order(spec, sku)
        if confirm:
            return _a9.call(self.card_id, http.post, f"{BASE}/orders", body=body, headers=self._h(spec.get("contract_id")),
                            creds=self.how_to_get)
        out = _a9.dry("POST", f"{BASE}/orders", body,
                      firm_price="none: Satellogic publishes no price endpoint; the contract sets the price")
        if self.configured() and spec.get("quote") is not False:
            try:
                out["opportunities"] = http.post(f"{BASE}/analysis/opportunities", body=body, headers=self._h(spec.get("contract_id")))
            except http.HTTPError as e:
                out["warnings"] = [_a9.http_warning(self.card_id, e, "capture opportunities")]
        return out

    def status(self, order):
        oid = self._oid(order)
        h = self._h()
        resp = _a9.call(self.card_id, http.get, f"{BASE}/orders/{oid}", headers=h, creds=self.how_to_get)
        if isinstance(resp, dict) and (self.provider_status(resp) or "").lower() in ("in_progress", "orderclosed"):
            try:
                resp["deliverables"] = http.get(f"{BASE}/orders/{oid}/deliverables", headers=h)
            except http.HTTPError as e:
                resp.setdefault("warnings", []).append(_a9.http_warning(self.card_id, e, "deliverables"))
        return resp

    @staticmethod
    def _oid(order):
        return order.get("order_id") or (order.get("properties") or {}).get("order_id") or order.get("id")

    def provider_status(self, resp):
        """properties.status; for a closed order, the latest event type, because Closed covers completion, rejection
        and cancellation alike (the docs tell them apart by event)."""
        p = (resp or {}).get("properties") if isinstance(resp, dict) else None
        st = p.get("status") if isinstance(p, dict) else None
        if st == "closed":
            ev = (p.get("latest_event") or {}).get("type")
            return ev if isinstance(ev, str) and ev else st
        return st if isinstance(st, str) else None

    # Order states received / in_progress; a closed order maps by its event (OrderClosed = "closed successfully").
    # A bare "closed" or any other event is left unmapped (in_flight, reported verbatim).
    status_map = {"received": "in_flight", "in_progress": "in_flight", "orderclosed": "delivered",
                  "orderanalysisfailed": "failed", "orderforceclosed": "failed", "ordercancellationaccepted": "failed",
                  "orderfailed": "failed"}

    def deliverables(self, resp):
        out = []
        for d in (((resp or {}).get("deliverables") or {}).get("results") or []):
            if d.get("status") != "DELIVERED":
                continue
            for name, a in (d.get("assets") or {}).items():
                if isinstance(a, dict) and a.get("href"):
                    out.append(observation(self.card_id, "optical-product", None, None, None, label=f"{name} ({d.get('processing_level')})",
                                           url=a["href"], result_time=d.get("delivered_at"), deliverable_id=d.get("deliverable_id"),
                                           media_type=a.get("type")))
        return out

    def cancel(self, order):
        """DELETE /v2/orders/{id}: a cancellation request, processed asynchronously (204; 409 if not allowed now)."""
        return _a9.call(self.card_id, http.request, "DELETE", f"{BASE}/orders/{self._oid(order)}", headers=self._h(),
                        creds=self.how_to_get)
