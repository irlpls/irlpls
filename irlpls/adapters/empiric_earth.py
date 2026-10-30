"""Empiric Earth (Nexar CityStream VirtualCam): recent anonymized road frames from Nexar's dashcam network near a point,
each with its capture time, GPS and heading. Contract only: an approved account in the VirtualCam product group logs in
to the Nexar Developers Portal (OAuth 2.0 authorization code + PKCE on Nexar's Okta) and gets a token; set it as
NEXAR_ACCESS_TOKEN. Targeted collection goes through sales and has no API, so this adapter only reads.

Built from the public OpenAPI files only; never called live (fixture: documented-shape).
- Docs: https://developer.getnexar.com/documentation
- VirtualCam spec: https://developer.getnexar.com/specs/virtualcamera.yaml (POST /api/virtualcam/v5/frames with a
  bounding_box {south_west, north_east} of {latitude, longitude}, filters {start_time, end_time (epoch ms strings),
  frames_per_h3, min_frame_quality, ...}, sort_by TIMESTAMP|QUALITY; response frames[] with frame_id, frame_url,
  thumbnail_url, captured_at (epoch ms), timezone_offset, gps_info, direction, camera_heading, frame_quality,
  frame_context, osm_info; 403 when the area is not authorized, 429 'API rate limit exceeded').
  Coverage first: POST /api/virtualcam/v4/coverage with h3_coverage_bounding_box {bounding_box, h3_resolution}.
- Live Feed spec (detections, not used here): https://developer.getnexar.com/specs/livefeed.yaml
- Token refresh (named, not called): POST https://external.getnexar.com/dev-portal/refresh-token, form field refresh_token
  (from the card's api.auth, sourced from the portal).
"""
import math
from datetime import datetime, timezone

from .. import http
from . import _a3
from .base import Adapter, Results, observation, prepared_call

BASE = "https://external.getnexar.com"
FRAMES = f"{BASE}/api/virtualcam/v5/frames"
COVERAGE = f"{BASE}/api/virtualcam/v4/coverage"
DOCS = "https://developer.getnexar.com/documentation"
MAX_AGE_H = 72   # irlpls reads what is current; older frames are archive, not "now"


def bbox(lat, lon, radius_km):
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    r = lambda v: round(v, 6)
    return {"south_west": {"latitude": r(lat - dlat), "longitude": r(lon - dlon)},
            "north_east": {"latitude": r(lat + dlat), "longitude": r(lon + dlon)}}


def frames_body(lat, lon, radius_km, now_ms=None, max_age_h=MAX_AGE_H):
    now_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
    return {"bounding_box": bbox(lat, lon, radius_km),
            "filters": {"start_time": str(now_ms - int(max_age_h * 3600 * 1000)), "end_time": str(now_ms), "frames_per_h3": 1},
            "sort_by": "TIMESTAMP"}


def _iso_ms(v):
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class EmpiricEarth(Adapter):
    card_id = "empiric-earth"
    env = "NEXAR_ACCESS_TOKEN"
    how_to_get = ("Contract only: an account approved for CityStream VirtualCam logs in at https://developer.getnexar.com "
                  "and copies its access token.")
    can_live = True

    def _auth(self):
        return {"Authorization": "Bearer " + self.key()}

    def prepare(self, req, route, radius_km=0.5, now_ms=None):
        """Recent frames near the point. A read under the requester's contract (API quota only)."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        body = frames_body(float(req["lat"]), float(req["lon"]), radius_km, now_ms)
        return prepared_call(self.card_id, "read", "POST", FRAMES, headers={"Authorization": "Bearer {NEXAR_ACCESS_TOKEN}",
                             "Content-Type": "application/json"}, body=body, auth_kind="bearer", key_name=self.env,
                             key_in="header Authorization", get_key_url="https://developer.getnexar.com", docs_url=DOCS,
                             notes="Contract only (VirtualCam product group). Token: portal login (OAuth 2.0 authorization "
                                   "code + PKCE on https://nexar.okta.com/oauth2/aus3qkg89t55hJZsT4x7), or exchange the "
                                   "portal's refresh token at POST https://external.getnexar.com/dev-portal/refresh-token "
                                   "(form field refresh_token). Optional first step: POST "
                                   f"{COVERAGE} with {{\"h3_coverage_bounding_box\": {{\"bounding_box\": <same box>, "
                                   "\"h3_resolution\": 9}} to see whether frames exist. frame_url needs the same bearer "
                                   "(scope image:get). captured_at is epoch milliseconds. Built from the public OpenAPI; "
                                   "not live-tested.")

    def live(self, lat, lon, radius_km=0.5, limit=3, **kw):
        h = self._auth()
        try:
            data = http.post(FRAMES, body=frames_body(lat, lon, radius_km), headers=h) or {}
        except http.HTTPError as e:
            return Results([], [_a3.http_warning(self.card_id, e, "VirtualCam frames", keyed=e.status == 401, env=self.env,
                                                 how_to_get=self.how_to_get)])
        frames = [f for f in data.get("frames") or [] if isinstance(f, dict) and f.get("frame_url")]
        frames.sort(key=lambda f: int(f.get("captured_at") or 0), reverse=True)
        out = []
        for f in frames[:limit]:
            g = f.get("gps_info") or {}
            out.append(observation(self.card_id, "photo", _iso_ms(f.get("captured_at")), g.get("latitude"), g.get("longitude"),
                                   url=f["frame_url"], label="road frame (dashcam)", frame_id=f.get("frame_id"),
                                   thumbnail=f.get("thumbnail_url"), direction=f.get("direction"),
                                   heading=f.get("camera_heading"), frame_quality=f.get("frame_quality"),
                                   light=f.get("frame_context"), road_type=(f.get("osm_info") or {}).get("road_type"),
                                   timezone_offset_s=f.get("timezone_offset")))
        return Results(out, [])
