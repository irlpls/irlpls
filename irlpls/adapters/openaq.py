"""OpenAQ v3: air quality from government reference monitors and low-cost sensors. Key in OPENAQ_API_KEY."""
from .. import http
import urllib.parse

from .base import Adapter, observation, prepared_call

BASE = "https://api.openaq.org/v3"
DOCS = "https://docs.openaq.org"


class OpenAQ(Adapter):
    card_id = "openaq"
    env = "OPENAQ_API_KEY"
    how_to_get = "Sign up at https://explore.openaq.org and copy the key from account settings."
    can_live = True

    def prepare(self, req, route, radius_km=10, limit=3):
        """Step 1 of live(): monitoring locations near the point. Free (API quota only), so safe without approval."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        q = urllib.parse.urlencode({"coordinates": f"{req['lat']:.4f},{req['lon']:.4f}", "radius": int(min(radius_km, 25) * 1000),
                                    "limit": limit})
        return prepared_call(self.card_id, "read", "GET", f"{BASE}/locations?{q}", headers={"X-API-Key": "{" + self.env + "}"},
                             auth_kind="api_key_header", key_name=self.env, key_in="header X-API-Key",
                             get_key_url="https://explore.openaq.org", docs_url=DOCS,
                             notes="Then GET /v3/locations/{id}/latest for each result's id; match its sensorsId to the "
                                   "location's sensors for the parameter and units. The radius is capped at 25 km.")

    def live(self, lat, lon, radius_km=10, limit=3, reference_only=False, **kw):
        h = {"X-API-Key": self.key()}
        locs = http.get(f"{BASE}/locations", headers=h, params={"coordinates": f"{lat:.4f},{lon:.4f}",
                        "radius": int(min(radius_km, 25) * 1000), "limit": limit, "monitor": "true" if reference_only else None})["results"]
        out = []
        for loc in locs:
            sensors = {s["id"]: s for s in loc.get("sensors", [])}
            for m in http.get(f"{BASE}/locations/{loc['id']}/latest", headers=h)["results"]:
                s = sensors.get(m.get("sensorsId"), {})
                par = s.get("parameter", {})
                c = m.get("coordinates") or loc.get("coordinates") or {}
                out.append(observation("openaq", "sensor", (m.get("datetime") or {}).get("utc"), c.get("latitude"), c.get("longitude"),
                                       value=m.get("value"), unit=par.get("units"), label=par.get("displayName") or par.get("name"),
                                       site=loc.get("name"), reference_monitor=loc.get("isMonitor"), distance_km=round((loc.get("distance") or 0) / 1000, 1)))
        return out
