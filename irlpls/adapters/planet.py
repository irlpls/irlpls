"""Planet: new-capture satellite tasking (Tasking API; keys via Planet sales).
Key in PLANET_API_KEY, sent as HTTP Basic (key as username).

Docs: https://docs.planet.com/develop/apis/tasking/ (POST /tasking/v2/pricing/, POST /tasking/v2/orders/, GET
/tasking/v2/orders/{id}; the Tasking docs send the key as 'Authorization: api-key <key>') and
https://docs.planet.com/develop/authentication/."""
import base64
from datetime import datetime, timedelta, timezone

from .. import http
from .base import Adapter, pick_operation, prepared_call

BASE = "https://api.planet.com"


class Planet(Adapter):
    card_id = "planet"
    env = "PLANET_API_KEY"
    how_to_get = "Planet account → My Settings → API key. Tasking keys come from Planet sales."
    can_commission = True

    def _h(self, required=True):
        return {"Authorization": "Basic " + base64.b64encode(f"{self.key(required)}:".encode()).decode()}

    def prepare(self, req, route, operation=None):
        """quote: POST /tasking/v2/pricing/ (default; nothing ordered); order: POST /tasking/v2/orders/; status: one order."""
        op = pick_operation(route, operation, "commission", ("quote", "order", "status"), "quote")
        if not op:
            return None
        h = {"Authorization": "api-key {" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header Authorization: api-key <key>",
                    get_key_url="https://docs.planet.com/develop/authentication/", docs_url="https://docs.planet.com/develop/apis/tasking/")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/tasking/v2/orders/{{ORDER_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "ORDER_ID", "what": "the tasking order id", "where": "POST /tasking/v2/orders/'s response"}],
                                 notes="Captures: GET /tasking/v2/captures/?order_id={id}.")
        if req.get("lat") is None or req.get("lon") is None:
            return None
        mins = req.get("within_minutes")
        days = max(1, -(-int(mins) // 1440)) if isinstance(mins, (int, float)) and mins > 0 else 7
        body = self._body({"lat": req["lat"], "lon": req["lon"], "days": days})
        h = dict(h, **{"Content-Type": "application/json"})
        note = " Tasking keys come from Planet sales; product and pl_number default to those tied to the key."
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/tasking/v2/pricing/", headers=h,
                                 body={"geometry": body["geometry"]}, **auth,
                                 notes="Estimates the tasking price for a point (a 5x5 km square); orders nothing." + note)
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/tasking/v2/orders/", headers=h, body=body, **auth,
                             notes="Creates a tasking order under your contract. Needs the person's approval of the price "
                                   "first; cancellation over the contract limit is charged." + note)

    @staticmethod
    def _body(spec):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        body = {"name": spec.get("name", "irlpls tasking"), "geometry": {"type": "Point", "coordinates": [spec["lon"], spec["lat"]]},
                "start_time": start.isoformat(), "end_time": (start + timedelta(days=spec.get("days", 7))).isoformat()}
        if spec.get("pl_number"):
            body["pl_number"] = spec["pl_number"]
        return body

    def commission(self, spec, confirm=False):
        """New capture over a point (Planet tasks a 5x5 km square). spec: lat, lon, days, name, pl_number (from your contract)."""
        body = self._body(spec)
        if not confirm:
            if not spec.get("quote", True):
                return self._dry("POST", f"{BASE}/tasking/v2/orders/", body, self._h(required=False))
            quote = http.post(f"{BASE}/tasking/v2/pricing/", body={"geometry": body["geometry"]}, headers=self._h())
            return {"dry_run": True, "quote": quote, "would_send": {"method": "POST", "url": f"{BASE}/tasking/v2/orders/", "body": body}}
        return http.post(f"{BASE}/tasking/v2/orders/", body=body, headers=self._h())

    def status(self, order):
        oid = order["id"]
        out = http.get(f"{BASE}/tasking/v2/orders/{oid}", headers=self._h())
        out["captures"] = http.get(f"{BASE}/tasking/v2/captures/", headers=self._h(), params={"order_id": oid}).get("results", [])
        return out
