"""WSDOT highway cameras (Washington State, plus Portland-area Oregon cameras): still images refreshed about every five
minutes. Free, no key for the camera list (ArcGIS query) and the images; the REST JSON services need a free AccessCode.

Sources (public, checked 2026-10-28):
- Traveler Information API index (RSS, KML, REST; Access Code by email): https://wsdot.wa.gov/traffic/api/
- REST camera operations (GetCamerasAsJson, SearchCamerasAsJson, AccessCode query parameter):
  https://wsdot.wa.gov/traffic/api/HighwayCameras/HighwayCamerasREST.svc/Help
- Keyless ArcGIS camera layer (fields OBJECTID=CameraID, CameraTitle, ImageURL, CompassDirection; 'low volume' use):
  https://data.wsdot.wa.gov/arcgis/rest/services/TravelInformation/TravelInfoCamerasWeather/FeatureServer/0
  A point + distance query was recorded on 2026-10-28 (tests/fixtures/adapters/wsdot-cameras/arcgis_near.json).
- Capture time: the image's Last-Modified header (HEAD on ImageURL, e.g. https://images.wsdot.wa.gov/nw/005vc16511.jpg).
  Some ImageURLs are on other hosts (Oregon's tripcheck.com); if their header is missing the time stays unknown.
"""
import urllib.parse

from .. import geo, http
from . import _a3
from .base import Adapter, Results, observation, prepared_call, warning

QUERY = ("https://data.wsdot.wa.gov/arcgis/rest/services/TravelInformation/TravelInfoCamerasWeather/FeatureServer/0/query")
REST = "https://wsdot.wa.gov/Traffic/api/HighwayCameras/HighwayCamerasREST.svc"
DOCS = "https://wsdot.wa.gov/traffic/api/"
MAX_RECORDS = 200   # the layer's maxRecordCount is 2000; a few km around a point holds far fewer cameras


def _params(lat, lon, radius_km, n=MAX_RECORDS):
    return {"where": "1=1", "geometry": f"{lon:.5f},{lat:.5f}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "distance": max(50, int(float(radius_km) * 1000)),
            "units": "esriSRUnit_Meter", "outFields": "OBJECTID,CameraTitle,ImageURL,CompassDirection", "outSR": 4326,
            "resultRecordCount": n, "f": "json"}


class WSDOTCameras(Adapter):
    card_id = "wsdot-cameras"
    can_live = True

    def prepare(self, req, route, radius_km=5, limit=3):
        """Step 1 of live(): cameras within the radius, from the keyless ArcGIS layer. Free, so safe without approval."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        q = urllib.parse.urlencode(_params(float(req["lat"]), float(req["lon"]), radius_km))
        return prepared_call(self.card_id, "read", "GET", f"{QUERY}?{q}", docs_url=DOCS,
                             notes="Keyless; WSDOT asks for low-volume use. Results are not sorted by distance: sort "
                                   f"features by distance to the point and keep the {limit} nearest. The capture time is "
                                   "each ImageURL's Last-Modified header (HEAD it). With a free AccessCode "
                                   f"(https://wsdot.wa.gov/traffic/api/), {REST}/GetCamerasAsJson?AccessCode="
                                   "{WSDOT_ACCESS_CODE} also gives IsActive, Region and CameraOwner.")

    def live(self, lat, lon, radius_km=5, limit=3, **kw):
        try:
            data = http.get(QUERY, params=_params(lat, lon, radius_km)) or {}
        except http.HTTPError as e:
            return Results([], [_a3.http_warning(self.card_id, e, "cameras near the point")])
        if isinstance(data, dict) and data.get("error"):   # ArcGIS reports errors in a 200 body
            code = (data["error"] or {}).get("code") if isinstance(data["error"], dict) else None
            return Results([], [warning(self.card_id, "provider_error", f"WSDOT ArcGIS query failed (code {code})", status=code)])
        cams = []
        for f in data.get("features") or []:
            a, g = f.get("attributes") or {}, f.get("geometry") or {}
            try:
                clat, clon = float(g["y"]), float(g["x"])
            except (KeyError, TypeError, ValueError):
                continue
            if not a.get("ImageURL"):
                continue
            cams.append((geo.distance_km(lat, lon, clat, clon), clat, clon, a))
        out, warns = [], []
        for dist, clat, clon, a in sorted(cams, key=lambda c: c[0])[:limit]:
            t, why = _a3.last_modified(a["ImageURL"])
            if why:
                warns.append(warning(self.card_id, "no_capture_time", f"{a.get('CameraTitle')}: image time unknown ({why})"))
            out.append(observation(self.card_id, "photo", t, round(clat, 6), round(clon, 6), url=a["ImageURL"],
                                   label="highway camera", camera=a.get("CameraTitle"), camera_id=a.get("OBJECTID"),
                                   direction=a.get("CompassDirection"), distance_km=round(dist, 2)))
        return Results(out, warns)
