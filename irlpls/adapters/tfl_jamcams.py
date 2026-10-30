"""Transport for London JamCams: still images (and ~10 s clips) from about 900 London traffic cameras, refreshed about
every three minutes. Free; no key needed (anonymous limit 50 requests/min). An optional free app_key from the TfL API
portal raises it to 500/min; set TFL_APP_KEY and it is sent as the app_key query parameter.

Sources (public, checked 2026-10-28):
- Unified API Swagger, Place endpoints and app_key auth: https://api.tfl.gov.uk/swagger/docs/v1
- API portal and products (anonymous 50/min, keyed 500/min): https://api-portal.tfl.gov.uk/ , https://api-portal.tfl.gov.uk/products
- Cameras near a point: GET https://api.tfl.gov.uk/Place?lat=..&lon=..&radius=<metres>&type=JamCam (recorded 2026-10-28,
  tests/fixtures/adapters/tfl-jamcams/place_near.json); each place has additionalProperties available, imageUrl, videoUrl, view.
- Capture time: the S3 image's Last-Modified header (HEAD https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/<id>.jpg);
  the camera record's `modified` is when the record changed, not the image, so it is never used as the capture time.
"""
import os
import urllib.parse

from .. import http
from . import _a3
from .base import Adapter, Results, observation, prepared_call, warning

BASE = "https://api.tfl.gov.uk"
DOCS = "https://api-portal.tfl.gov.uk/"
KEY_ENV = "TFL_APP_KEY"   # optional; the adapter works without it, so it is not the Adapter's required `env`


def _radius_m(radius_km):
    return max(50, int(float(radius_km) * 1000))


class TfLJamCams(Adapter):
    card_id = "tfl-jamcams"
    can_live = True
    how_to_get = ("Optional: register on https://api-portal.tfl.gov.uk/, subscribe to the free '500 requests a minute' "
                  "product and set TFL_APP_KEY.")

    def prepare(self, req, route, radius_km=1, limit=3):
        """Step 1 of live(): JamCams near the point. Keyless and free, so safe without approval."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        q = urllib.parse.urlencode({"lat": f"{float(req['lat']):.5f}", "lon": f"{float(req['lon']):.5f}",
                                    "radius": _radius_m(radius_km), "type": "JamCam"})
        return prepared_call(self.card_id, "read", "GET", f"{BASE}/Place?{q}", docs_url=DOCS,
                             notes="Keyless (50 requests/min). For 500/min append &app_key={TFL_APP_KEY} (free key from "
                                   "https://api-portal.tfl.gov.uk/). Each place's additionalProperties hold available, "
                                   "imageUrl (JPG), videoUrl (MP4) and view; skip available=false. The capture time is the "
                                   f"image's Last-Modified header: HEAD each imageUrl. Take the {limit} nearest by distance.")

    def live(self, lat, lon, radius_km=1, limit=3, **kw):
        key = os.environ.get(KEY_ENV)
        params = {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "radius": _radius_m(radius_km), "type": "JamCam", "app_key": key}
        try:
            data = http.get(f"{BASE}/Place", params=params) or {}
        except http.HTTPError as e:
            return Results([], [_a3.http_warning(self.card_id, e, "cameras near the point", keyed=bool(key), env=KEY_ENV,
                                                 how_to_get=self.how_to_get)])
        places = data.get("places") if isinstance(data, dict) else data
        cams = []
        for p in places or []:
            props = {a.get("key"): a.get("value") for a in p.get("additionalProperties") or [] if isinstance(a, dict)}
            if str(props.get("available", "")).lower() != "true" or not props.get("imageUrl"):
                continue
            cams.append((p.get("distance") or 0, p, props))
        out, warns = [], []
        for dist, p, props in sorted(cams, key=lambda c: c[0])[:limit]:
            t, why = _a3.last_modified(props["imageUrl"])
            if why:
                warns.append(warning(self.card_id, "no_capture_time", f"{p.get('commonName')}: image time unknown ({why})"))
            out.append(observation(self.card_id, "photo", t, p.get("lat"), p.get("lon"), url=props["imageUrl"],
                                   label="traffic camera", camera=p.get("commonName"), camera_id=p.get("id"),
                                   view=props.get("view"), video=props.get("videoUrl"), distance_km=round(dist / 1000, 2)))
        return Results(out, warns)
