"""PurpleAir: crowd-hosted air quality sensors. READ key in PURPLEAIR_API_KEY; calls spend API points.

Docs: https://api.purpleair.com/ (GET /v1/sensors with fields and a nwlat/nwlng/selat/selng box; header X-API-Key)."""
from datetime import datetime, timezone

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

BASE = "https://api.purpleair.com/v1"
FIELDS = "name,latitude,longitude,last_seen,pm2.5_10minute,humidity,temperature,location_type,confidence"


class PurpleAir(Adapter):
    card_id = "purpleair"
    env = "PURPLEAIR_API_KEY"
    how_to_get = "Create a READ key on the PurpleAir Developer Dashboard (develop.purpleair.com)."
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=3, max_age=3600):
        """The outdoor sensors in a box around the point. Spends API points, nothing else; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        w, s, e, n = geo.bbox(req["lat"], req["lon"], radius_km)
        q = query({"fields": FIELDS, "nwlat": f"{n:.5f}", "nwlng": f"{w:.5f}", "selat": f"{s:.5f}", "selng": f"{e:.5f}",
                   "location_type": 0, "max_age": max_age})
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/sensors?{q}", headers={"X-API-Key": "{" + self.env + "}"},
            auth_kind="api_key_header", key_name=self.env, key_in="header X-API-Key", get_key_url="https://develop.purpleair.com",
            docs_url="https://api.purpleair.com/",
            notes="A READ key. The response is {fields: [...], data: [[...], ...]}: zip each row with fields. pm2.5_10minute "
                  "is in µg/m³; last_seen is a Unix time. Each call spends API points.")

    def live(self, lat, lon, radius_km=3, limit=5, max_age=3600, **kw):
        w, s, e, n = geo.bbox(lat, lon, radius_km)
        data = http.get(f"{BASE}/sensors", headers={"X-API-Key": self.key()},
                        params={"fields": FIELDS, "nwlat": n, "nwlng": w, "selat": s, "selng": e, "location_type": 0, "max_age": max_age})
        cols = data["fields"]
        rows = [dict(zip(cols, r)) for r in data.get("data", [])]
        for r in rows:
            r["_d"] = geo.distance_km(lat, lon, r["latitude"], r["longitude"])
        out = []
        for r in sorted(rows, key=lambda r: r["_d"])[:limit]:
            ts = datetime.fromtimestamp(r["last_seen"], timezone.utc).isoformat() if r.get("last_seen") else None
            out.append(observation("purpleair", "sensor", ts, r["latitude"], r["longitude"], value=r.get("pm2.5_10minute"), unit="µg/m³",
                                   label="PM2.5, 10-minute average", site=r.get("name"), confidence=r.get("confidence"),
                                   distance_km=round(r["_d"], 2)))
        return out
