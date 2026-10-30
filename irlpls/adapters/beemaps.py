"""Bee Maps (Hivemapper): request a fresh dashcam video of a street segment, captured after you ask.
Key in BEEMAPS_API_KEY.

Docs: https://beemaps.com/docs/api-reference and https://beemaps.com/openapi.json (POST /requestVideo with geojson,
validFrom, validUntil and query idempotencyKey; GET /requestVideo/{id}; 'Authorization: Basic <API_KEY>', the key itself).
No price is published for a video request, so there is no quote call."""
from datetime import datetime, timedelta, timezone

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

BASE = "https://beemaps.com/api/developer"
DOCS = "https://beemaps.com/docs/api-reference"


def polygon(lat, lon, radius_km):
    w, s, e, n = geo.bbox(lat, lon, radius_km)
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


class BeeMaps(Adapter):
    card_id = "hivemapper"
    env = "BEEMAPS_API_KEY"
    how_to_get = "Create a key in the Bee Maps Developer Portal (beemaps.com/developers, API Key tab)."
    can_commission = True

    def _h(self, required=True):
        return {"Authorization": f"Basic {self.key(required)}"}

    def _body(self, spec):
        r_km = min(spec.get("radius_m", 60), 110) / 1000
        start = datetime.now(timezone.utc)
        return {"geojson": polygon(spec["lat"], spec["lon"], r_km), "validFrom": start.isoformat(),
                "validUntil": (start + timedelta(days=spec.get("days", 14))).isoformat()}

    def prepare(self, req, route, operation=None):
        """order: POST /requestVideo for the point (default); status: GET /requestVideo/{REQUEST_ID}."""
        op = pick_operation(route, operation, "commission", ("order", "status"), "order")
        if not op:
            return None
        h = {"Authorization": "Basic {" + self.env + "}"}
        auth = dict(auth_kind="basic", key_name=self.env, key_in="header Authorization: Basic <key> (the key itself, not base64)",
                    get_key_url="https://beemaps.com/developers", docs_url=DOCS)
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/requestVideo/{{REQUEST_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "REQUEST_ID", "what": "the id POST /requestVideo returned", "where": "its response"}],
                                 notes="status is pending, fulfilled, expired or cancelled; videos[] (signed URLs, 24 h) appear when fulfilled.")
        if req.get("lat") is None or req.get("lon") is None:
            return None
        mins = req.get("within_minutes")
        days = max(1, -(-int(mins) // 1440)) if isinstance(mins, (int, float)) and mins > 0 else 14
        body = self._body({"lat": req["lat"], "lon": req["lon"], "days": days})
        return prepared_call(
            self.card_id, "order", "POST", f"{BASE}/requestVideo?" + query({"idempotencyKey": "{IDEMPOTENCY_KEY}"}),
            headers=dict(h, **{"Content-Type": "application/json"}), body=body, **auth,
            placeholders=[{"name": "IDEMPOTENCY_KEY", "what": "a unique id for this request (a retry with it returns the original id)",
                           "where": "you choose it"}],
            notes="Requests fresh video (1080p, 15 fps or better) recorded inside the polygon during the window. The price "
                  "is not published; a 402 means insufficient credits. Needs the person's approval. Cancel with "
                  "DELETE /requestVideo/{id}.")

    def commission(self, spec, confirm=False):
        """Request fresh video of a street segment. spec: lat, lon, radius_m (polygon, 10 to 50,000 m²), days (validity)."""
        body = self._body(spec)
        if not confirm:
            return self._dry("POST", f"{BASE}/requestVideo", body, self._h(required=False))
        return http.post(f"{BASE}/requestVideo", body=body, headers=self._h(), params={"idempotencyKey": spec.get("idempotency_key")})

    def status(self, order):
        return http.get(f"{BASE}/requestVideo/{order['id']}", headers=self._h())

    # GET /requestVideo/{id}: pending, fulfilled, expired or cancelled; a videos array appears when fulfilled.
    status_map = {"pending": "in_flight", "fulfilled": "delivered", "expired": "failed", "cancelled": "failed"}

    def deliverables(self, resp):
        out = []
        for v in (resp or {}).get("videos") or []:
            v = v if isinstance(v, dict) else {"url": v}
            out.append(observation(self.card_id, "video", v.get("timestamp") or v.get("capturedAt"), v.get("lat"), v.get("lon"),
                                   url=v.get("url") or v.get("signedUrl"), label="requested street video"))
        return out
