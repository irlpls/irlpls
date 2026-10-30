"""Fintraffic Digitraffic road weather cameras (Finland): the latest image from the nearest stations. Free, no key; CC BY 4.0.

Docs: https://www.digitraffic.fi/en/road-traffic/ and https://tie.digitraffic.fi/swagger/openapi.json (GET /stations,
/stations/{id}/data; JSON needs Accept-Encoding: gzip; a Digitraffic-User header naming the app is asked for)."""
from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call

BASE = "https://tie.digitraffic.fi/api/weathercam/v1"


class Fintraffic(Adapter):
    card_id = "fintraffic-weathercams"
    can_live = True

    def prepare(self, req, route, operation=None):
        """Step 1 of live(): every camera station (GeoJSON), to pick the nearest. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None:
            return None
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/stations", headers={"Accept-Encoding": "gzip", "Digitraffic-User": "{YOUR_APP_NAME}"},
            docs_url="https://www.digitraffic.fi/en/road-traffic/",
            placeholders=[{"name": "YOUR_APP_NAME", "what": "your app's name (no personal data)", "where": "you choose it (not a secret)"}],
            notes="No key. Keep features with properties.collectionStatus GATHERING, nearest first, then GET "
                  "/stations/{id}/data: presets[] {id, measuredTime}; the image is https://weathercam.digitraffic.fi/{preset id}.jpg. "
                  "Finland only. JSON responses need Accept-Encoding: gzip (406 without it).")

    def live(self, lat, lon, radius_km=25, limit=1, **kw):
        st = [f for f in (http.get(f"{BASE}/stations") or {}).get("features", []) if (f.get("properties") or {}).get("collectionStatus") == "GATHERING"]
        dist = lambda f: geo.distance_km(lat, lon, f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
        out = []
        for f in sorted(st, key=dist)[:limit]:
            d = dist(f)
            if d > radius_km:
                continue
            slon, slat = f["geometry"]["coordinates"][:2]
            data = http.get(f"{BASE}/stations/{f['id']}/data") or {}
            for p in data.get("presets") or []:
                out.append(observation("fintraffic-weathercams", "photo", p.get("measuredTime"), slat, slon, label="road weather camera",
                                       url=f"https://weathercam.digitraffic.fi/{p['id']}.jpg", station=f["properties"].get("name"),
                                       distance_km=round(d, 1)))
        return out
