"""GeoNet (New Zealand): recent quakes near a point, and volcanic alert levels. Free, no key.

Docs: https://api.geonet.org.nz/ (GET /quake?MMI=<-1..8> returns recent quakes as GeoJSON with publicID, time, depth,
magnitude, mmi, locality, quality; GET /volcano/val returns each volcano's Volcanic Alert Level; versioning by the
Accept header, here application/vnd.geo+json;version=2). The quake list has no place or radius filter, so distance is
filtered client-side. Quake page: https://www.geonet.org.nz/earthquake/<publicID>.
"""
from .. import http
from . import _fdsn
from .base import Adapter, Results, observation, prepared_call

BASE = "https://api.geonet.org.nz"
DOCS = "https://api.geonet.org.nz/"
ACCEPT = {"Accept": "application/vnd.geo+json;version=2"}
QUAKE_PAGE = "https://www.geonet.org.nz/earthquake/"


class GeoNetNZ(Adapter):
    card_id = "geonet-nz"
    can_live = True

    def prepare(self, req, route, mmi=3):
        """The quake list live() reads (newest first); distance is applied afterwards, client-side."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(self.card_id, "read", "GET", f"{BASE}/quake?MMI={int(mmi)}", headers=dict(ACCEPT), docs_url=DOCS,
                             notes=f"No place filter: keep features whose geometry [lon, lat] lies within your radius of "
                                   f"{req['lat']:.4f}, {req['lon']:.4f}. MMI is the minimum modelled shaking intensity in "
                                   "New Zealand (-1 to 8; -1 returns all). For volcanic alert levels GET /volcano/val "
                                   "with the same Accept header.")

    def live(self, lat, lon, radius_km=300, limit=10, mmi=3, volcanoes=False, **kw):
        def run():
            out, warnings = [], []
            doc = http.get(f"{BASE}/quake", params={"MMI": int(mmi)}, headers=dict(ACCEPT))
            near = [(d, f) for f in _fdsn.features(doc) if (d := _fdsn.distance(lat, lon, f)) <= radius_km]
            out += [self._quake(f, d) for d, f in near[:limit]]
            if volcanoes:
                v = _fdsn.guarded(self.card_id, lambda: sorted(
                    (self._volcano(f, d) for f in _fdsn.features(http.get(f"{BASE}/volcano/val", headers=dict(ACCEPT)))
                     if (d := _fdsn.distance(lat, lon, f)) <= radius_km), key=lambda o: o["distance_km"])[:limit])
                if isinstance(v, Results):
                    warnings += v.warnings
                out += list(v)
            return Results(out, warnings)
        return _fdsn.guarded(self.card_id, run)

    def _quake(self, f, d):
        p = f.get("properties") or {}
        c = f["geometry"]["coordinates"]
        mag, loc, pid = p.get("magnitude"), p.get("locality"), p.get("publicID")
        mag_r = round(mag, 1) if isinstance(mag, (int, float)) else None
        return observation(self.card_id, "sensor", p.get("time"), c[1], c[0], value=mag_r, unit="magnitude",
                           label=f"M {mag_r} - {loc}" if mag_r is not None and loc else loc, url=(QUAKE_PAGE + pid) if pid else None,
                           event_id=pid, place=loc, depth_km=p.get("depth"), mmi=p.get("mmi"), quality=p.get("quality"),
                           distance_km=d)

    def _volcano(self, f, d):
        p = f.get("properties") or {}
        c = f["geometry"]["coordinates"]
        # GeoNet gives no time for an alert level; phenomenon_time stays None rather than being made up.
        return observation(self.card_id, "sensor", None, c[1], c[0], value=p.get("level"), unit="volcanic alert level",
                           label=f"{p.get('volcanoTitle') or p.get('volcanoID')}: {p.get('activity') or ''}".strip(" :"),
                           volcano_id=p.get("volcanoID"), hazards=p.get("hazards"), aviation_colour=p.get("acc"), distance_km=d)
