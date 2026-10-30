"""England's Environment Agency flood-monitoring API: river, tide and rain gauges. Free, no key; OGL v3.

Docs: https://environment.data.gov.uk/flood-monitoring/doc/reference (GET /id/stations?lat&long&dist, then
/id/stations/{id}/readings?latest)."""
from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

BASE = "https://environment.data.gov.uk/flood-monitoring"
PARAM = {"level": "water level", "flow": "flow", "rainfall": "rainfall", "temperature": "water temperature", "wind": "wind"}


class UKEAFlood(Adapter):
    card_id = "uk-ea-flood"
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=15):
        """Step 1 of live(): the gauges within radius_km. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        q = query({"lat": f"{req['lat']:.4f}", "long": f"{req['lon']:.4f}", "dist": radius_km, "_limit": 20})
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/id/stations?{q}", docs_url="https://environment.data.gov.uk/flood-monitoring/doc/reference",
            notes="No key. Then GET /id/stations/{stationReference}/readings?latest for the nearest items[]; match each "
                  "reading's measure to the station's measures[] for parameter and unitName. England only.")

    def live(self, lat, lon, radius_km=15, limit=2, **kw):
        st = http.get(f"{BASE}/id/stations", params={"lat": lat, "long": lon, "dist": radius_km, "_limit": 20}).get("items", [])
        st = sorted((s for s in st if s.get("lat") is not None), key=lambda s: geo.distance_km(lat, lon, s["lat"], s["long"]))[:limit]
        out = []
        for s in st:
            units = {m["@id"]: m for m in (s.get("measures") or []) if isinstance(m, dict)}
            sid = s["@id"].rsplit("/", 1)[-1]
            d = round(geo.distance_km(lat, lon, s["lat"], s["long"]), 1)
            for r in http.get(f"{BASE}/id/stations/{sid}/readings", params={"latest": ""}).get("items", []):
                m = units.get(r.get("measure"), {})
                v = r.get("value")
                if not isinstance(v, (int, float)):
                    continue
                label = PARAM.get(m.get("parameter"), m.get("parameterName") or "reading") + (f' ({m["qualifier"].lower()})' if m.get("qualifier") else "")
                out.append(observation("uk-ea-flood", "sensor", r.get("dateTime"), s["lat"], s["long"], value=v, unit=m.get("unitName"),
                                       label=label, station=f'{sid} {s.get("label", "")}'.strip(), river=s.get("riverName"),
                                       distance_km=d, url=r.get("@id")))
        return out
