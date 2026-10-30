"""SatVu: task the HotSat thermal satellites (day or night). Contract-only; prices are set per contract in GBP or EUR.

Sources (public docs, read 2026-10-28):
- OpenAPI (server https://api.satellitevu.com/otm/v2, contract id in every path, price request/response with
  price.currency and price.total in minor units, order POST, order status enum staged|in progress|fulfilled|failed|
  cancelled|expired at properties.status, POST .../cancel -> 204, GET .../download):
  https://api.satellitevu.com/otm/v2/openapi.json
- Token: client_credentials with audience https://api.satellitevu.com/ at https://auth.satellitevu.com/oauth/token,
  60-minute tokens: https://docs.satellitevu.com/developers/api/overview ,
  https://auth.satellitevu.com/.well-known/openid-configuration
- Integration environment: same client id and secret, a separate integration contract id, simulated "NotSat" data,
  not billed, every order fulfils: https://docs.satellitevu.com/developer-guides/integration-environment
- Feasibility is free and places no order: https://docs.satellitevu.com/guides/tasking/overview

A price is firm only when SatVu states it in USD; GBP/EUR prices stay firm: false and are never converted.
Keys: SATVU_CLIENT_ID, SATVU_CLIENT_SECRET, SATVU_CONTRACT_ID (point it at the integration contract to test for free).
"""
import os
import re
from decimal import Decimal

from .. import http
from . import _a8, _oauth
from .base import Adapter, MissingCredentials, minor_to_major, observation, prepared_call

BASE = "https://api.satellitevu.com/otm/v2"
TOKEN_URL = "https://auth.satellitevu.com/oauth/token"
AUDIENCE = "https://api.satellitevu.com/"
DOCS = "https://docs.satellitevu.com/developers/api/overview"
GET_KEY = "https://docs.satellitevu.com/developer-guides/integration-environment"
TOKEN = "SATVU_ACCESS_TOKEN"


def _usd(price):
    """Decimal USD from a SatVu price object (minor units), or None when it is not in USD."""
    if not isinstance(price, dict) or str(price.get("currency") or "").upper() != "USD":
        return None
    t = price.get("total")
    return (Decimal(t) / 100) if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None


class SatVu(Adapter):
    card_id = "satvu"
    env = "SATVU_CLIENT_ID"
    env_secret = "SATVU_CLIENT_SECRET"
    env_contract = "SATVU_CONTRACT_ID"
    how_to_get = ("SatVu accounts come with a contract (ask SatVu). Copy the Client ID and Client Secret from your user profile "
                  "in the SatVu web app and set SATVU_CLIENT_ID, SATVU_CLIENT_SECRET and SATVU_CONTRACT_ID; an integration "
                  "contract id gives simulated, unbilled orders.")
    can_commission = True
    can_cancel = True
    tokens = _oauth.TokenCache()

    @staticmethod
    def _env(name):
        return os.environ.get(name) or None

    def configured(self):
        return all(self._env(n) for n in (self.env, self.env_secret, self.env_contract))

    def _secrets(self):
        return [self._env(self.env), self._env(self.env_secret)]

    def _creds(self):
        return f"{self.env}/{self.env_secret}/{self.env_contract}"

    def _h(self):
        if not self.configured():
            raise MissingCredentials(f"satvu: set {self._creds()}. {self.how_to_get}")
        try:
            tok = self.tokens.client_credentials(TOKEN_URL, self._env(self.env), self._env(self.env_secret), audience=AUDIENCE)
        except http.HTTPError as e:
            raise _a8.mapped_error(self.card_id, e, self._creds(), self.how_to_get, self._secrets()) from None
        return {"Authorization": "Bearer " + tok}

    def _call(self, method, path, **kw):
        return _a8.call(self.card_id, method, BASE + path, self._creds(), self.how_to_get,
                        self._secrets() + [kw.get("headers", {}).get("Authorization")], **kw)

    def spec_from_request(self, req, ext_id, deliver_by):
        return _a8.spec_from_request(req, ext_id, deliver_by)

    @staticmethod
    def _body(spec):
        start, end = _a8.window(spec, lead_h=1, default_days=7)
        props = {"product": "standard", "datetime": f"{_a8.iso(start)}/{_a8.iso(end)}",
                 "max_cloud_cover": spec.get("max_cloud_cover", 25), "max_off_nadir": spec.get("max_off_nadir", 30)}
        if spec.get("day_night_mode"):
            props["satvu:day_night_mode"] = spec["day_night_mode"]
        for k in ("licence_level", "addon:withhold"):
            if spec.get(k):
                props[k] = spec[k]
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [spec["lon"], spec["lat"]]}, "properties": props}

    def feasibility(self, spec):
        """Free; places no order. Status pending|processing|failed|feasible|not feasible."""
        return self._call("POST", f"/{self._env(self.env_contract)}/tasking/feasibilities/", body=self._body(spec), headers=self._h())

    def commission(self, spec, confirm=False):
        """Standard tasking at spec lat/lon (a point), window_days (default 7). Without confirm: the order it would place
        plus, with credentials, SatVu's price (firm only if in USD). With confirm: places the order against the contract's
        credit limit."""
        missing = _a8.missing_point(spec)
        if missing:
            return {"dry_run": True, "missing": missing, "note": "Nothing was sent: the place is needed."}
        body = self._body(spec)
        contract = self._env(self.env_contract)
        if not confirm:
            out = self._dry("POST", f"{BASE}/{contract or '{' + self.env_contract + '}'}/tasking/orders/", body,
                            {"Authorization": "Bearer {" + TOKEN + "}"})
            if not self.configured():
                return out
            q = self._call("POST", f"/{contract}/tasking/price/", body=body, headers=self._h())
            p = (q or {}).get("price") or {}
            usd = _usd(p)
            if usd is not None:
                return _a8.put_quote(out, float(usd), "USD", q)
            cur = str(p.get("currency") or "").upper()
            major = minor_to_major(p.get("total"), cur) if re.fullmatch(r"[A-Z]{3}", cur) else None
            return _a8.put_quote(out, major if major is not None else p.get("total"),
                                 cur if major is not None else (cur or "unknown") + "_MINOR_UNITS", q,
                                 f"SatVu priced this in {p.get('currency') or 'an unstated currency'}, not USD, "
                                 "so the offer is not firm; no converted figure is given.")
        if not self.configured():
            raise MissingCredentials(f"satvu: set {self._creds()}. {self.how_to_get}")
        return self._call("POST", f"/{contract}/tasking/orders/", body=body, headers=self._h())

    def order_total(self, resp):
        return _usd((resp or {}).get("price")) if isinstance(resp, dict) else None

    def _ids(self, order):
        if not isinstance(order, dict) or not order.get("id"):
            raise ValueError("satvu: the order has no id")
        return order.get("contract_id") or self._env(self.env_contract), order["id"]

    def status(self, order):
        c, oid = self._ids(order)
        return self._call("GET", f"/{c}/tasking/orders/{oid}", headers=self._h())

    def cancel(self, order):
        """POST .../cancel (204). SatVu documents cancel while the order is staged; otherwise it answers 409."""
        c, oid = self._ids(order)
        self._call("POST", f"/{c}/tasking/orders/{oid}/cancel", headers=self._h())
        return {"id": oid, "cancel_requested": True}

    status_path = ("properties", "status")
    status_map = {"staged": "in_flight", "in progress": "in_flight", "fulfilled": "delivered",
                  "failed": "failed", "cancelled": "failed", "expired": "failed"}

    def deliverables(self, resp):
        if not isinstance(resp, dict) or str((resp.get("properties") or {}).get("status")).lower() != "fulfilled":
            return []
        c = resp.get("contract_id") or self._env(self.env_contract) or "{" + self.env_contract + "}"
        lon, lat = ((resp.get("geometry") or {}).get("coordinates") or [None, None])[:2]
        return [observation(self.card_id, "satellite-tasking", None, lat, lon, label=f"SatVu order {resp.get('id')} fulfilled",
                            order_id=resp.get("id"), url=f"{BASE}/{c}/tasking/orders/{resp.get('id')}/download",
                            window=(resp.get("properties") or {}).get("datetime"))]

    # ---- ready calls
    def prepare(self, req, route, operation=None):
        """quote: POST .../tasking/price/. order: POST .../tasking/orders/. status: one order. No keyless read exists."""
        op = operation or ("quote" if route == "commission" else None)
        auth = dict(auth_kind="oauth2", key_name=TOKEN, key_in="header Authorization: Bearer", get_key_url=GET_KEY, docs_url=DOCS)
        tok = (f"Token: POST {TOKEN_URL} form grant_type=client_credentials, client_id={{SATVU_CLIENT_ID}}, "
               f"client_secret={{SATVU_CLIENT_SECRET}}, audience={AUDIENCE}; lasts 60 minutes. ")
        h = {"Authorization": "Bearer {" + TOKEN + "}"}
        ph = [{"name": self.env_contract, "what": "SatVu contract id (an integration contract is free and simulated)",
               "where": "your SatVu contract, or the Policy API"}]
        if op == "status":
            oid = req.get("order_id") or "{ORDER_ID}"
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/{{{self.env_contract}}}/tasking/orders/{oid}", headers=h,
                                 **auth, placeholders=ph + ([] if req.get("order_id") else [{"name": "ORDER_ID", "what": "SatVu order id", "where": "id of the order response"}]),
                                 notes=tok + "properties.status: staged, in progress, fulfilled, failed, cancelled or expired.")
        if op not in ("quote", "order") or req.get("lat") is None or req.get("lon") is None:
            return None
        body = self._body({"lat": req["lat"], "lon": req["lon"]})
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/{{{self.env_contract}}}/tasking/price/", headers=h, body=body,
                                 **auth, placeholders=ph,
                                 notes=tok + "Orders nothing. price.total is in minor units of price.currency (GBP or EUR in "
                                       "most contracts, so not a USD price). Feasibility (free): POST "
                                       ".../tasking/feasibilities/ with the same body.")
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/{{{self.env_contract}}}/tasking/orders/", headers=h, body=body,
                             **auth, placeholders=ph,
                             notes=tok + "Deducts the price from the contract's credit limit (unbilled on an integration "
                                   "contract). No idempotency key is documented. Cancel while staged: POST .../{order_id}/cancel.")
