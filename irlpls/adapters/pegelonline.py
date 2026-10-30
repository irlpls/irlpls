"""PEGELONLINE (German Federal Waterways): water level and flow at federal gauges. Free, no key; DL-DE->Zero-2.0.

Docs: https://www.pegelonline.wsv.de/webservice/dokuRestapi (GET /stations.json with latitude, longitude, radius,
includeTimeseries, includeCurrentMeasurement)."""
from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

BASE = "https://pegelonline.wsv.de/webservices/rest-api/v2"
LABEL = {"W": "water level", "Q": "flow", "WT": "water temperature", "LT": "air temperature"}


class Pegelonline(Adapter):
    card_id = "pegelonline"
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=25):
        """The gauges near the point with their current measurements, in one call. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        q = query({"latitude": f"{req['lat']:.4f}", "longitude": f"{req['lon']:.4f}", "radius": radius_km,
                   "includeTimeseries": "true", "includeCurrentMeasurement": "true"})
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/stations.json?{q}", docs_url="https://www.pegelonline.wsv.de/webservice/dokuRestapi",
            notes="No key. Each station's timeseries[] has shortname (W water level, Q flow), unit and currentMeasurement "
                  "{timestamp, value}. German federal waterways only.")

    def live(self, lat, lon, radius_km=25, limit=2, **kw):
        st = http.get(f"{BASE}/stations.json", params={"latitude": lat, "longitude": lon, "radius": radius_km,
                                                       "includeTimeseries": "true", "includeCurrentMeasurement": "true"}) or []
        st = [s for s in st if s.get("latitude") is not None]
        st = sorted(st, key=lambda s: geo.distance_km(lat, lon, s["latitude"], s["longitude"]))[:limit]
        out = []
        for s in st:
            d = round(geo.distance_km(lat, lon, s["latitude"], s["longitude"]), 1)
            if d > radius_km:
                continue
            for t in s.get("timeseries") or []:
                cm = t.get("currentMeasurement") or {}
                if cm.get("value") is None:
                    continue
                out.append(observation("pegelonline", "sensor", cm.get("timestamp"), s["latitude"], s["longitude"], value=cm["value"],
                                       unit=t.get("unit"), label=LABEL.get(t.get("shortname"), (t.get("longname") or "reading").lower()),
                                       station=f'{s.get("longname", "")} ({(s.get("water") or {}).get("longname", "")})', distance_km=d,
                                       state=cm.get("stateMnwMhw"), url=f'{BASE}/stations/{s["uuid"]}.json'))
        return out
