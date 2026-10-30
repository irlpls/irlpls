"""Caltrans highway cameras: live still images (refreshed every 1-60 min, mostly 1-5) from ~3,400 California cameras.
Free, no key. Capture time comes from each image's Last-Modified header; the camera list itself has none.

Docs: https://cwwp2.dot.ca.gov/closed-circuit-television-cameras.html (per-district static files
/data/d{d}/cctv/cctvStatusD{dd}.json)."""
import urllib.request
from email.utils import parsedate_to_datetime

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call

LIST = "https://cwwp2.dot.ca.gov/data/d{d}/cctv/cctvStatusD{d:02d}.json"
# rough district centres, to fetch only the 1-2 nearest district files instead of all twelve
DISTRICTS = {1: (40.8, -124.0), 2: (40.6, -122.4), 3: (38.6, -121.5), 4: (37.8, -122.3), 5: (35.3, -120.7), 6: (36.7, -119.8),
             7: (34.05, -118.25), 8: (34.1, -117.3), 9: (37.4, -118.4), 10: (37.9, -121.3), 11: (32.7, -117.15), 12: (33.7, -117.85)}
_cache = {}


def cameras(d):
    if d not in _cache:
        _cache[d] = [x["cctv"] for x in http.get(LIST.format(d=d))["data"]]
    return _cache[d]


def captured(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": http.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            lm = r.headers.get("Last-Modified")
        return parsedate_to_datetime(lm).isoformat() if lm else None
    except Exception:
        return None


class CaltransCCTV(Adapter):
    card_id = "caltrans-cctv"
    can_live = True

    def prepare(self, req, route, operation=None):
        """The camera list of the nearest district (whole-district static file). Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        d = min(DISTRICTS, key=lambda d: geo.distance_km(req["lat"], req["lon"], *DISTRICTS[d]))
        return prepared_call(
            self.card_id, "read", "GET", LIST.format(d=d), docs_url="https://cwwp2.dot.ca.gov/closed-circuit-television-cameras.html",
            notes="No key. Static file (district %d, ~1-3 MB): data[].cctv with location {latitude, longitude, locationName}, "
                  "inService and imageData.static.currentImageURL. Keep in-service cameras near the point; the image's "
                  "Last-Modified header is its capture time. California only." % d)

    def live(self, lat, lon, radius_km=5, limit=3, **kw):
        ds = sorted(DISTRICTS, key=lambda d: geo.distance_km(lat, lon, *DISTRICTS[d]))[:2]
        found = []
        for d in ds:
            for c in cameras(d):
                loc = c["location"]
                try:
                    clat, clon = float(loc["latitude"]), float(loc["longitude"])
                except ValueError:
                    continue
                if c.get("inService") != "true":
                    continue
                dist = geo.distance_km(lat, lon, clat, clon)
                if dist <= radius_km:
                    found.append((dist, clat, clon, c))
        out = []
        for dist, clat, clon, c in sorted(found, key=lambda x: x[0])[:limit]:
            url = c["imageData"]["static"]["currentImageURL"]
            out.append(observation("caltrans-cctv", "photo", captured(url), clat, clon, url=url, label="highway camera",
                                   camera=c["location"]["locationName"], route=c["location"].get("route"),
                                   distance_km=round(dist, 2)))
            # streamingVideoURL is not returned: the stream playlists answered 404 when checked (2026-10-29 full pass);
            # the still above is what Caltrans serves reliably.
        return out
