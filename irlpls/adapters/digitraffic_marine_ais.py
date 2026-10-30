"""Fintraffic Digitraffic marine AIS (Finnish waters): the latest positions of vessels near a point, with name and type
for the nearest few. Free, no key; CC BY 4.0.

Sources (public docs, read 2026-10-28):
- Marine traffic: https://www.digitraffic.fi/en/marine-traffic/
- OpenAPI: https://meri.digitraffic.fi/swagger/openapi.json. GET /api/ais/v1/locations takes latitude, longitude, radius
  (km, haversine), from/to (epoch ms; from defaults to 24 h back), mmsi. Properties: sog (knots, 102.3 = n/a), cog
  (360 = n/a), heading (511 = n/a), navStat (0-15), timestampExternal (record time, epoch ms). GET
  /api/ais/v1/vessels/{mmsi}: name, callSign, shipType, destination.
- Instructions: https://www.digitraffic.fi/en/support/instructions/ (gzip mandatory, 406 otherwise; send a
  Digitraffic-User header naming the app, no personal data; 60 requests/min per IP without it; 429 on excess).
Ships, not people; but crew can be traced, so a request about a specific person is refused upstream (agent rule 9).
"""
from datetime import datetime, timedelta, timezone
import urllib.parse

from .. import geo, http, ratelimit
from .base import Adapter, Results, observation, prepared_call, warning

BASE = "https://meri.digitraffic.fi/api/ais/v1"
DOCS = "https://www.digitraffic.fi/en/marine-traffic/"
USER = "irlpls/0.1"
HEADERS = {"Digitraffic-User": USER, "Accept-Encoding": "gzip"}
LOOKBACK_MIN = 30
NAV_STATUS = {0: "under way using engine", 1: "at anchor", 2: "not under command", 3: "restricted manoeuvrability",
              4: "constrained by draught", 5: "moored", 6: "aground", 7: "engaged in fishing", 8: "under way sailing",
              11: "towing astern", 12: "pushing ahead or towing alongside", 14: "AIS-SART / MOB / EPIRB active"}


def _from_ms(now=None, minutes=LOOKBACK_MIN):
    t = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    return int(t.replace(second=0, microsecond=0).timestamp()) * 1000   # minute-rounded, so repeats hit the cache


def _params(lat, lon, radius_km, frm):
    return {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "radius": radius_km, "from": frm}


def _iso_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_locations(fc):
    """Vessel positions from a /locations FeatureCollection, with the documented 'not available' codes turned to None."""
    out = []
    for f in (fc or {}).get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if len(g) < 2:
            continue
        sog, cog, hd = p.get("sog"), p.get("cog"), p.get("heading")
        out.append({"mmsi": p.get("mmsi") or f.get("mmsi"), "lat": g[1], "lon": g[0],
                    "time": _iso_ms(p.get("timestampExternal")),
                    "sog": None if sog is None or sog >= 102.3 else sog, "cog": None if cog is None or cog >= 360 else cog,
                    "heading": None if hd is None or hd == 511 else hd, "nav_status": NAV_STATUS.get(p.get("navStat")),
                    "position_accuracy_high": p.get("posAcc")})
    return out


def _warn(card_id, what, err):
    ra = getattr(err, "retry_after", None)
    if ra is None:
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    code = {401: "refused", 403: "refused", 404: "not_found", 429: "rate_limited"}.get(err.status, "provider_error")
    return warning(card_id, code, f"{what}: HTTP {err.status}", status=err.status,
                   **({"retry_after_s": int(ra)} if ra is not None else {}))


class DigitrafficMarineAIS(Adapter):
    card_id = "digitraffic-marine-ais"
    can_live = True

    def prepare(self, req, route, radius_km=10):
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        q = urllib.parse.urlencode(_params(req["lat"], req["lon"], radius_km, _from_ms()))
        return prepared_call(self.card_id, "read", "GET", f"{BASE}/locations?{q}", headers=dict(HEADERS), docs_url=DOCS,
                             notes="GeoJSON FeatureCollection of vessels within radius km (haversine) reported since `from` "
                                   "(epoch ms). sog is knots (102.3 = n/a), timestampExternal is the record time in epoch ms. "
                                   "gzip is mandatory. Name and type: GET /api/ais/v1/vessels/{mmsi}. Finnish waters only; "
                                   "do not use it to follow a specific person.")

    def live(self, lat, lon, radius_km=10, limit=5, names=True, **kw):
        fc = http.get(f"{BASE}/locations", params=_params(lat, lon, radius_km, _from_ms()), headers=HEADERS)
        ships = sorted(parse_locations(fc), key=lambda v: geo.distance_km(lat, lon, v["lat"], v["lon"]))[:limit]
        out, warns = [], []
        for v in ships:
            meta = {}
            if names and v["mmsi"]:
                try:
                    meta = http.get(f"{BASE}/vessels/{int(v['mmsi'])}", headers=HEADERS) or {}
                except http.HTTPError as err:
                    if err.status != 404:   # 404: no metadata for this MMSI yet; the position still stands
                        warns.append(_warn(self.card_id, f"vessel {v['mmsi']} metadata unavailable", err))
                        if err.status == 429:
                            names = False
            name = (meta.get("name") or "").strip() or f"MMSI {v['mmsi']}"
            out.append(observation(self.card_id, "sensor", v["time"], v["lat"], v["lon"], value=v["sog"], unit="kn",
                                   label=f"{name}: speed over ground", mmsi=v["mmsi"], vessel=name,
                                   ship_type=meta.get("shipType"), destination=meta.get("destination") or None,
                                   course_deg=v["cog"], heading_deg=v["heading"], nav_status=v["nav_status"],
                                   position_accuracy_high=v["position_accuracy_high"],
                                   url=f"{BASE}/locations?mmsi={v['mmsi']}",
                                   distance_km=round(geo.distance_km(lat, lon, v["lat"], v["lon"]), 2)))
        if fc and fc.get("dataUpdatedTime"):
            for o in out:
                o["result_time"] = fc["dataUpdatedTime"]
        return Results(out, warns)
