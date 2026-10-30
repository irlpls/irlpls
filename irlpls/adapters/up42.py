"""UP42: task a new satellite or aerial capture from many constellations through one API (orders spend UP42 credits).

Sources (public docs, read 2026-10-28):
- Tasking flow, validate/estimate/opportunities/feasibility, order statuses and substatuses, cancel rule:
  https://docs.up42.com/developers/api-tasking
- Token: OAuth2 password grant, client_id up42-api, 5-minute tokens: https://docs.up42.com/developers/authentication
- Paths and bodies (OpenAPI fragments): https://developer.up42.com/reference/estimatev2order,
  https://developer.up42.com/reference/createv2orderunderworkspace (POST /v2/orders?workspaceId=, 202 {results[{id}]}),
  https://developer.up42.com/reference/getorderv2, https://developer.up42.com/reference/cancelorder
  (POST /v2/orders/{order-id}/cancellation), https://developer.up42.com/reference/listcollectionsv2 (keyless)
- Credits: 100 credits = EUR 1, so an estimate is never a firm USD offer: https://docs.up42.com/getting-started/credits

Creating a tasking order deducts nothing: UP42 then runs feasibility and issues a quotation, and credits are deducted
only when that quotation is accepted (PATCH /v2/tasking/quotation/{id}), which this adapter never does. The estimate
(POST /v2/orders/estimate) is free with a token. Keys: UP42_USERNAME + UP42_PASSWORD (console login), UP42_WORKSPACE_ID
(to order), UP42_DATA_PRODUCT_ID (or spec["data_product_id"]): a tasking data product from GET /v2/collections.
"""
import os

from .. import http
from . import _a8, _oauth
from .base import Adapter, MissingCredentials, observation, prepared_call

BASE = "https://api.up42.com"
TOKEN_URL = "https://auth.up42.com/realms/public/protocol/openid-connect/token"
CLIENT_ID = "up42-api"
DOCS = "https://docs.up42.com/developers/api-tasking"
SIGNUP = "https://console.up42.com"
TOKEN = "UP42_ACCESS_TOKEN"   # what a prepared call names; the SDK gets it from the password grant


class UP42(Adapter):
    card_id = "up42"
    env = "UP42_USERNAME"
    env_password = "UP42_PASSWORD"
    env_workspace = "UP42_WORKSPACE_ID"
    env_product = "UP42_DATA_PRODUCT_ID"
    how_to_get = ("Sign up free at https://console.up42.com, then set UP42_USERNAME and UP42_PASSWORD (your console login), "
                  "UP42_WORKSPACE_ID and a tasking UP42_DATA_PRODUCT_ID. Ordering needs credits, accepted EULAs and, for "
                  "restricted collections, approved access.")
    can_commission = True
    can_cancel = True
    tokens = _oauth.TokenCache()

    @staticmethod
    def _env(name):
        return os.environ.get(name) or None

    def configured(self):
        return bool(self._env(self.env) and self._env(self.env_password))

    def _secrets(self):
        return [self._env(self.env), self._env(self.env_password)]

    def _h(self):
        if not self.configured():
            raise MissingCredentials(f"up42: set {self.env} and {self.env_password}. {self.how_to_get}")
        try:
            tok = self.tokens.password(TOKEN_URL, CLIENT_ID, self._env(self.env), self._env(self.env_password))
        except http.HTTPError as e:
            raise _a8.mapped_error(self.card_id, e, f"{self.env}/{self.env_password}", self.how_to_get, self._secrets()) from None
        return {"Authorization": "Bearer " + tok}

    def _call(self, method, path, **kw):
        return _a8.call(self.card_id, method, BASE + path, f"{self.env}/{self.env_password}", self.how_to_get,
                        self._secrets() + [kw.get("headers", {}).get("Authorization")], **kw)

    # ---- request building
    def _body(self, spec, product):
        start, end = _a8.window(spec, lead_h=24, default_days=7)
        params = {"acquisitionStart": _a8.iso(start), "acquisitionEnd": _a8.iso(end), **(spec.get("params") or {})}
        return {"dataProduct": product, "displayName": (spec.get("name") or "irlpls tasking")[:200],
                "featureCollection": _a8.feature_collection(_a8.polygon(spec["lat"], spec["lon"], spec.get("radius_km", 2.5))),
                "params": params}

    def spec_from_request(self, req, ext_id, deliver_by):
        return _a8.spec_from_request(req, ext_id, deliver_by)

    def collections(self, type_="TASKING", size=50):
        """Keyless: tasking (or ARCHIVE) collections with their data product ids and integrations (e.g.
        ACCESS_APPROVAL_REQUIRED, FEASIBILITY_STUDY_REQUIRED, PRICE_ESTIMATION_AVAILABLE)."""
        resp = self._call("GET", "/v2/collections", params={"type": type_, "size": size})
        out = []
        for c in (resp or {}).get("content") or []:
            out.append({"name": c.get("name"), "title": c.get("title"), "type": c.get("type"),
                        "integrations": c.get("integrations") or [], "product_type": (c.get("metadata") or {}).get("productType"),
                        "resolution_m": ((c.get("metadata") or {}).get("resolutionValue") or {}).get("minimum"),
                        "providers": [p.get("name") for p in c.get("providers") or []],
                        "data_products": [{"id": p.get("id"), "name": p.get("name"), "title": p.get("title")} for p in c.get("dataProducts") or []]})
        return out

    def commission(self, spec, confirm=False):
        """Tasking order over a square AOI around spec lat/lon (radius_km, default 2.5). spec: data_product_id (else
        UP42_DATA_PRODUCT_ID), params (product-specific fields from GET /orders/schema/{id}), window_days, deliver_by.
        Without confirm: the order it would create plus, when credentials are set, UP42's credit estimate (not firm:
        credits are euros). With confirm: creates the order, which then waits for feasibility and a quotation."""
        missing = _a8.missing_point(spec)
        if missing:
            return {"dry_run": True, "missing": missing, "note": "Nothing was sent: the place is needed."}
        product = spec.get("data_product_id") or self._env(self.env_product)
        workspace = spec.get("workspace_id") or self._env(self.env_workspace)
        body = self._body(spec, product or "{" + self.env_product + "}")
        url = f"{BASE}/v2/orders?workspaceId={workspace or '{' + self.env_workspace + '}'}"
        if not confirm:
            out = self._dry("POST", url, body, {"Authorization": "Bearer {" + TOKEN + "}"})
            if not self.configured():
                return out
            if not product:
                out["missing"] = [f"data_product_id: a UP42 tasking data product id (GET {BASE}/v2/collections?type=TASKING)"]
                return out
            est = self._call("POST", "/v2/orders/estimate", body=body, headers=self._h())
            credits = ((est or {}).get("summary") or {}).get("totalCredits")
            return _a8.put_quote(out, credits, "UP42_CREDITS", est,
                                 "UP42 estimates in credits (100 credits = EUR 1), not USD, so the offer is not firm. The final "
                                 "price is the quotation UP42 issues after the order is created.")
        if not product or not workspace:
            raise MissingCredentials(f"up42: set {self.env_product} and {self.env_workspace} (or pass them in spec) to order.")
        return self._call("POST", "/v2/orders", params={"workspaceId": workspace}, body=body, headers=self._h())

    @staticmethod
    def _order_id(order):
        if not isinstance(order, dict):
            return None
        rs = order.get("results") or []
        return order.get("id") or order.get("orderId") or (rs[0].get("id") if rs and isinstance(rs[0], dict) else None)

    def status(self, order):
        oid = self._order_id(order)
        if not oid:
            raise ValueError("up42: the order has no id")
        return self._call("GET", f"/v2/orders/{oid}", headers=self._h())

    def cancel(self, order):
        """Only orders in CREATED or PLACEMENT_FAILED can be cancelled; UP42 answers 400 otherwise."""
        oid = self._order_id(order)
        if not oid:
            raise ValueError("up42: the order has no id")
        return self._call("POST", f"/v2/orders/{oid}/cancellation", headers=self._h())

    # GET /v2/orders/{id} status: CREATED, BEING_PLACED, PLACED, PLACEMENT_FAILED ("wait until it's resolved"),
    # BEING_FULFILLED, FULFILLED, FAILED_PERMANENTLY, CANCELED. orderDetails.subStatus says when the user must act.
    status_map = {**{s: "in_flight" for s in ("created", "being_placed", "placed", "placement_failed", "being_fulfilled")},
                  "fulfilled": "delivered", "failed_permanently": "failed", "canceled": "failed"}

    def deliverables(self, resp):
        if not isinstance(resp, dict) or str(resp.get("status")).upper() != "FULFILLED":
            return []
        d = resp.get("orderDetails") or {}
        return [observation(self.card_id, "satellite-tasking", None, None, None, label=f"UP42 order {resp.get('id')} fulfilled",
                            order_id=resp.get("id"), acquisition_start=d.get("acquisitionStart"),
                            acquisition_end=d.get("acquisitionEnd"),
                            assets=f"POST {BASE}/v2/assets/stac/search, then POST /v2/assets/{{asset-id}}/download-url")]

    # ---- ready calls
    def prepare(self, req, route, operation=None):
        """read: keyless tasking collections. quote: credit estimate. order: create the tasking order. status: one order."""
        op = operation or ("quote" if route == "commission" else None)
        if op == "read":
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/v2/collections?type=TASKING&size=50", docs_url=DOCS,
                                 notes="Keyless. Pick a collection with the -tasking suffix and a data product id from "
                                       "dataProducts; integrations lists ACCESS_APPROVAL_REQUIRED and similar steps.")
        auth = dict(auth_kind="oauth2", key_name=TOKEN, key_in="header Authorization: Bearer", get_key_url=SIGNUP, docs_url=DOCS)
        tok_note = (f"Token: POST {TOKEN_URL} form grant_type=password, client_id={CLIENT_ID}, username={{UP42_USERNAME}}, "
                    "password={UP42_PASSWORD}; access_token lasts 5 minutes. ")
        h = {"Authorization": "Bearer {" + TOKEN + "}"}
        if op == "status":
            oid = req.get("order_id") or "{ORDER_ID}"
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/v2/orders/{oid}", headers=h, **auth,
                                 placeholders=None if req.get("order_id") else [{"name": "ORDER_ID", "what": "UP42 order id",
                                                                                "where": "results[0].id of the create-order response"}],
                                 notes=tok_note + "status and orderDetails.subStatus (e.g. QUOTATION_WAITING_RESPONSE: decide on the quotation).")
        if op not in ("quote", "order") or req.get("lat") is None or req.get("lon") is None:
            return None
        body = self._body({"lat": req["lat"], "lon": req["lon"], "name": (req.get("need") or "irlpls tasking")[:200]},
                          "{" + self.env_product + "}")
        ph = [{"name": self.env_product, "what": "tasking data product id", "where": f"GET {BASE}/v2/collections?type=TASKING (dataProducts[].id)"}]
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/v2/orders/estimate", headers=h, body=body, **auth,
                                 placeholders=ph,
                                 notes=tok_note + "Free; returns summary.totalCredits (100 credits = EUR 1, so not a USD price). "
                                       "Fill params from GET /orders/schema/{data-product-id}; POST /v2/orders/validation "
                                       "and /v2/tasking/orders/feasibility take the same body.")
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/v2/orders?workspaceId={{{self.env_workspace}}}", headers=h,
                             body=body, **auth,
                             placeholders=ph + [{"name": self.env_workspace, "what": "UP42 workspace id", "where": "console settings"}],
                             notes=tok_note + "Creates the order without deducting credits; UP42 then issues a quotation, and "
                                   "credits are deducted only when it is accepted (PATCH /v2/tasking/quotation/{id}). "
                                   "No idempotency key is documented: do not retry blindly.")
