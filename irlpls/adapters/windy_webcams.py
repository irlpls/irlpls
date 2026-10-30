"""Windy Webcams: live images from tens of thousands of public webcams worldwide. Key in WINDY_WEBCAMS_API_KEY.
Free tier: low-res images whose links expire in minutes. Images must be credited and linked to Windy, and may only be
shown through the links the API returns.

Docs: https://api.windy.com/webcams/docs (GET /webcams, nearby=lat,lon,radius_km up to 250, header x-windy-api-key)."""
from datetime import datetime, timezone

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

BASE = "https://api.windy.com/webcams/api/v3"


class WindyWebcams(Adapter):
    card_id = "windy-webcams"
    env = "WINDY_WEBCAMS_API_KEY"
    how_to_get = "Create a Windy account and generate a Webcams API key at https://api.windy.com/keys."
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=10, limit=5):
        """The webcams near the point, with their current images. Free tier (API quota only); safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        q = query({"nearby": f"{req['lat']:.4f},{req['lon']:.4f},{min(radius_km, 250):g}", "limit": limit,
                   "include": "images,location,urls,player"})
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/webcams?{q}", headers={"x-windy-api-key": "{" + self.env + "}"},
            auth_kind="api_key_header", key_name=self.env, key_in="header x-windy-api-key", get_key_url="https://api.windy.com/keys",
            docs_url="https://api.windy.com/webcams/docs",
            notes="webcams[] carry images.current.preview, lastUpdatedOn and location. Free-tier image links expire in "
                  "minutes; credit and link Windy wherever an image is shown.")

    def live(self, lat, lon, radius_km=10, limit=5, **kw):
        data = http.get(f"{BASE}/webcams", headers={"x-windy-api-key": self.key()},
                        params={"nearby": f"{lat:.4f},{lon:.4f},{min(radius_km, 250):g}", "limit": limit, "include": "images,location,urls,player"})
        out = []
        for w in data.get("webcams", []):
            loc, img = w.get("location") or {}, (w.get("images") or {}).get("current") or {}
            ts = w.get("lastUpdatedOn")
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, timezone.utc).isoformat()
            wl, wn = loc.get("latitude"), loc.get("longitude")
            out.append(observation("windy-webcams", "photo", ts, wl, wn, url=img.get("preview") or img.get("thumbnail"), label="webcam",
                                   camera=w.get("title"), page=(w.get("urls") or {}).get("detail"),
                                   distance_km=round(geo.distance_km(lat, lon, wl, wn), 2) if wl is not None else None,
                                   attribution="Webcams provided by windy.com"))
        return out
