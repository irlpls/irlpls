"""ALERTCalifornia (UC San Diego): latest frame from the nearest wildfire-watch PTZ cameras. Free for non-commercial use, no key.

Docs: https://www.arcgis.com/home/item.html?id=0c1c1166688f4f2db938194799337be4 (public ArcGIS feature service; standard
FeatureServer /query parameters)."""
from datetime import datetime

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

FEED = "https://services8.arcgis.com/X84q166Srnyl4JMV/arcgis/rest/services/ALERTCalifornia_Camera_Feed/FeatureServer/0/query"


def _iso(t):
    """'2026-10-25 16:38:49-07:00' -> ISO 8601."""
    try:
        return datetime.fromisoformat(str(t)).isoformat()
    except ValueError:
        return None


class ALERTCalifornia(Adapter):
    card_id = "alertcalifornia"
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=30):
        """The online cameras within radius_km, with their latest-frame URL. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        q = query({"where": "isOnline='online'", "outFields": "*", "geometry": f"{req['lon']},{req['lat']}",
                   "geometryType": "esriGeometryPoint", "inSR": 4326, "outSR": 4326, "distance": radius_km,
                   "units": "esriSRUnit_Kilometer", "f": "json"})
        return prepared_call(
            self.card_id, "read", "GET", f"{FEED}?{q}", docs_url="https://www.arcgis.com/home/item.html?id=0c1c1166688f4f2db938194799337be4",
            notes="No key; non-commercial use. features[].attributes carry cameraName, imageURL and viewTime (when the view "
                  "was last recorded; the frame itself is usually newer). California and neighbours only.")

    def live(self, lat, lon, radius_km=30, limit=1, **kw):
        r = http.get(FEED, params={"where": "isOnline='online'", "outFields": "*", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                                   "inSR": 4326, "outSR": 4326, "distance": radius_km, "units": "esriSRUnit_Kilometer", "f": "json"}) or {}
        cams = [f for f in r.get("features", []) if f.get("geometry")]
        dist = lambda f: geo.distance_km(lat, lon, f["geometry"]["y"], f["geometry"]["x"])
        out = []
        for f in sorted(cams, key=dist)[:limit]:
            a = f["attributes"]
            out.append(observation("alertcalifornia", "photo", _iso(a.get("viewTime")), f["geometry"]["y"], f["geometry"]["x"],
                                   label="wildfire camera, latest frame", url=a.get("imageURL"), station=a.get("cameraName"),
                                   distance_km=round(dist(f), 1), pan=a.get("positionPan"), tilt=a.get("positionTilt"),
                                   note="phenomenon_time is when the camera's view was last recorded; the frame itself is usually newer"))
        return out
