"""NASA FIRMS: satellite active-fire detections near a point. Free.

Two documented paths:
- No key (default): the MODIS global 24-hour CSV, https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/
  MODIS_C6_1_Global_24h.csv (about 1.3 MB, refreshed hourly; listed by GET /api/active_fire_files/all?format=json),
  filtered by distance client-side. The VIIRS global files are 7-9 MB, so they are not the keyless default.
- With the free MAP_KEY in FIRMS_MAP_KEY: the area API, GET /api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/
  {DAY_RANGE} (https://firms.modaps.eosdis.nasa.gov/api/area/), here VIIRS_NOAA20_NRT over the last day. Limit 5,000
  transactions per 10 minutes per key (https://firms.modaps.eosdis.nasa.gov/api/map_key/). An invalid key answers
  HTTP 400 "Invalid MAP_KEY." The key goes in the URL path, so it is scrubbed from every warning and error.
Columns: https://www.earthdata.nasa.gov/data/tools/firms/faq and the attribute pages linked from the FIRMS API docs
(acq_date, acq_time HHMM in UTC; frp in MW; MODIS confidence 0-100, VIIRS l/n/h; satellite T/A for MODIS Terra/Aqua).
"""
import csv
import io
import os

from .. import geo, http
from . import _fdsn
from .base import Adapter, Results, observation, prepared_call, warning

BASE = "https://firms.modaps.eosdis.nasa.gov"
DOCS = "https://firms.modaps.eosdis.nasa.gov/api/"
KEYLESS_FILE = f"{BASE}/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv"
MAP_KEY_ENV = "FIRMS_MAP_KEY"   # optional: not `env`, because the keyless file works without it
GET_KEY_URL = "https://firms.modaps.eosdis.nasa.gov/api/map_key/"
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT", "LANDSAT_NRT")
MODIS_SATELLITE = {"T": "Terra", "A": "Aqua"}   # FIRMS MODIS attributes: T = Terra, A = Aqua
SOURCE_LABEL = {"VIIRS_SNPP_NRT": "VIIRS Suomi NPP", "VIIRS_NOAA20_NRT": "VIIRS NOAA-20", "VIIRS_NOAA21_NRT": "VIIRS NOAA-21",
                "MODIS_NRT": "MODIS", "LANDSAT_NRT": "Landsat"}


def _area(lat, lon, radius_km):
    w, s, e, n = geo.bbox(lat, lon, radius_km)
    return ",".join(f"{v:.4f}" for v in (max(w, -180), max(s, -90), min(e, 180), min(n, 90)))


def _time(row):
    d, t = (row.get("acq_date") or "").strip(), (row.get("acq_time") or "").strip()
    if not d or not t.isdigit():
        return None
    t = t.zfill(4)
    return f"{d}T{t[:2]}:{t[2:]}:00Z"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class NASAFIRMS(Adapter):
    card_id = "nasa-firms"
    can_live = True
    how_to_get = f"Optional: a free MAP_KEY in {MAP_KEY_ENV} enables the bounding-box area API; get one at {GET_KEY_URL}"

    def _key(self):
        return os.environ.get(MAP_KEY_ENV) or None

    def prepare(self, req, route, radius_km=50, source="VIIRS_NOAA20_NRT", day_range=1):
        """With FIRMS_MAP_KEY set, the area API call (key named, never filled in); without, the keyless global file."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        lat, lon = req["lat"], req["lon"]
        if self._key():
            ph = "{" + MAP_KEY_ENV + "}"
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/api/area/csv/{ph}/{source}/{_area(lat, lon, radius_km)}/{int(day_range)}",
                                 headers={"Accept": "text/csv"}, auth_kind="other", key_name=MAP_KEY_ENV,
                                 key_in="URL path segment after /api/area/csv/", get_key_url=GET_KEY_URL, docs_url=DOCS,
                                 notes="CSV, one row per fire pixel: latitude, longitude, acq_date, acq_time (HHMM UTC), "
                                       "confidence, frp (MW), daynight. Area is west,south,east,north. An invalid key "
                                       "answers HTTP 400 'Invalid MAP_KEY.'")
        return prepared_call(self.card_id, "read", "GET", KEYLESS_FILE, headers={"Accept": "text/csv"}, docs_url=DOCS,
                             notes=f"No key. Global MODIS detections of the last 24 h (about 1.3 MB, refreshed hourly): keep "
                                   f"rows within {radius_km} km of {lat:.4f}, {lon:.4f}. acq_time is HHMM UTC; frp is in "
                                   f"MW. With a free MAP_KEY ({GET_KEY_URL}) use GET /api/area/csv/{{MAP_KEY}}/"
                                   f"VIIRS_NOAA20_NRT/{_area(lat, lon, radius_km)}/1 for VIIRS in that box only.")

    def live(self, lat, lon, radius_km=50, limit=10, source="VIIRS_NOAA20_NRT", day_range=1, **kw):
        key = self._key()
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")

        def run():
            if key:
                text = http.get(f"{BASE}/api/area/csv/{key}/{source}/{_area(lat, lon, radius_km)}/{max(1, min(int(day_range), 5))}",
                                headers={"Accept": "text/csv"}, raw=True)
                product = source
            else:
                text = http.get(KEYLESS_FILE, headers={"Accept": "text/csv"}, raw=True)
                product = "MODIS_C6_1_Global_24h"
            text = text or ""
            if text.strip() and not text.lstrip().lower().startswith("latitude"):
                # FIRMS answered with a message instead of CSV (e.g. a key problem): report it, never parse it.
                return Results([], [warning(self.card_id, "unexpected_response",
                                            "FIRMS returned no CSV: " + _fdsn._scrub(text.strip()[:200], (key,)))])
            rows = []
            for r in csv.DictReader(io.StringIO(text)):
                rl, ro = _num(r.get("latitude")), _num(r.get("longitude"))
                if rl is None or ro is None:
                    continue
                d = geo.distance_km(lat, lon, rl, ro)
                if d <= radius_km:
                    rows.append((d, rl, ro, r))
            rows.sort(key=lambda x: (_time(x[3]) or "", -x[0]), reverse=True)   # newest first, then nearest
            return Results([self._obs(d, rl, ro, r, product) for d, rl, ro, r in rows[:limit]])

        res = _fdsn.guarded(self.card_id, run, scrub=(key,))
        for w in getattr(res, "warnings", []):
            if w.get("status") == 400 and "map_key" in w["msg"].lower():
                w["code"], w["msg"] = "unauthorized", f"FIRMS refused the MAP_KEY in {MAP_KEY_ENV} (HTTP 400 Invalid MAP_KEY)."
        return res

    def _obs(self, d, lat, lon, r, product):
        sat = (r.get("satellite") or "").strip()
        if product in SOURCE_LABEL:
            what = SOURCE_LABEL[product] + (f" {MODIS_SATELLITE[sat]}" if product == "MODIS_NRT" and sat in MODIS_SATELLITE else "")
        else:
            what = "MODIS " + MODIS_SATELLITE.get(sat, sat)
        conf = (r.get("confidence") or "").strip()
        conf_v = int(conf) if conf.isdigit() else ({"l": "low", "n": "nominal", "h": "high"}.get(conf.lower(), conf) or None)
        label = f"active fire, radiative power ({what.strip()})"
        return observation(self.card_id, "sensor", _time(r), lat, lon, value=_num(r.get("frp")), unit="MW",
                           label=label, confidence=conf_v, daynight=r.get("daynight"), satellite=sat or None, product=product,
                           version=r.get("version"), distance_km=round(d, 1))
