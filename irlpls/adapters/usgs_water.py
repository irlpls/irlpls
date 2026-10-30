"""USGS stream gauges: gage height and discharge, near real time, plus the latest frame from any USGS river camera
nearby (National Imagery Management System; public domain). Free; a key (USGS_WATER_API_KEY) only raises rate limits.

Everything is on api.waterdata.usgs.gov. Docs: readings, OGC API collection latest-continuous (https://api.waterdata.usgs.gov/docs/ogcapi/).
"Usual for this date": the USGS Water Data Statistics API, observationNormals, day-of-year percentiles of the daily mean
(https://api.waterdata.usgs.gov/statistics/v0/docs). Photos: NIMS cameras/listFiles (https://api.waterdata.usgs.gov/docs/nims/).
The legacy WaterServices (waterservices.usgs.gov) is not used: USGS slows it from 2026-11-16 (2 s per request, rising to
60 s from 2027-02-01, with planned outages) and shuts it on 2027-02-22 (https://waterdata.usgs.gov/blog/wdfn-waterservices-degradation/).

Rate limits (https://api.waterdata.usgs.gov/docs/ogcapi/keys/): keys are api.data.gov keys, optional, sent as the X-Api-Key
header (or api_key query parameter); a key raises the per-hour limit, and an exhausted limit answers 429. USGS publishes no
keyless number ("more than a few queries per hour" needs a key, per the migration guide), so ratelimit.DEFAULT paces calls.

Each reading carries its age (age_s) and, where USGS publishes them, the long-term statistics for that calendar day at that
site (daily_median, daily_stats: p25/p50/p75 and the count of years behind them). They are USGS's own numbers, not a
judgement: whether a reading is "low" is for the requester's model to say. Anything tried and not delivered (a rate limit on
the camera service, missing statistics) is returned in `warnings`."""
import os
from datetime import datetime, timedelta, timezone

from .. import geo, http, ratelimit
from .base import Adapter, Results, age_s, observation, pick_operation, prepared_call, query, warning

BASE = "https://api.waterdata.usgs.gov/ogcapi/v1"
NIMS = "https://api.waterdata.usgs.gov/nims/v0"
STATS = "https://api.waterdata.usgs.gov/statistics/v0/observationNormals"
MEAN = "00003"         # statistics are percentiles of the daily mean, as the legacy daily statistics were
PARAMS = {"00065": "gage height", "00060": "discharge"}
CODES = {v: k for k, v in PARAMS.items()}
KEY_HINT = "; set USGS_WATER_API_KEY to raise the rate limit"
BATCH_CELL_DEG = 2.0   # live_batch groups asks into cells this many degrees on a side: one request per parameter per cell
BATCH_PAGE = 1000      # features per page in a batched request (the API pages beyond it with a next link)
MAX_PAGES = 20
_cameras = None


def cameras(headers=None):
    global _cameras
    if _cameras is None:
        _cameras = [c for c in http.get(f"{NIMS}/cameras", headers=headers) if not c.get("hideCam") and c.get("lat")]
    return _cameras


def camera_frames(lat, lon, radius_km=15, limit=2, headers=None):
    """Latest still from each USGS river camera within radius_km (cameras record every 15-60 min, often daylight only)."""
    near = sorted(((geo.distance_km(lat, lon, float(c["lat"]), float(c["lng"])), c) for c in cameras(headers)), key=lambda x: x[0])
    out = []
    for d, c in near:
        if d > radius_km or len(out) >= limit:
            break
        files = http.get(f"{NIMS}/listFiles", params={"camId": c["camId"], "limit": 1}, headers=headers)
        if not files:
            continue
        f = files[0]
        ts = f.split("___")[-1].rsplit(".", 1)[0]
        ts = ts[:10] + ts[10:].replace("-", ":") if len(ts) >= 19 else None
        out.append(observation("usgs-water", "photo", ts, float(c["lat"]), float(c["lng"]), label="river camera", url=c["smallDir"] + f,
                               site=f'USGS-{c["nwisId"]}' if c.get("nwisId") else None, camera=c["camName"], distance_km=round(d, 1),
                               page=f'https://apps.usgs.gov/hivis/camera/{c["camId"]}'))
    return out


def _cell(lat, lon, deg):
    return int((lat + 90) // deg), int((lon + 180) // deg)


def _local_day(phenomenon_time, lon):
    """(month, day) of a reading at the site. A UTC timestamp is shifted by the site's solar offset (lon / 15 h)."""
    try:
        t = datetime.fromisoformat(str(phenomenon_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.utcoffset() == timedelta(0) and lon is not None:
        t = t + timedelta(hours=round(lon / 15))
    return t.month, t.day


def daily_stats(sites, days=None, headers=None):
    """{(site number, parameter code): {(month, day): {p25, p50, p75, count}}} from the USGS statistics API
    (observationNormals, day of year). `days` [(month, day)] narrows the request to those dates; count = years of record."""
    params = {"monitoring_location_id": [f"USGS-{s}" for s in sites], "parameter_code": list(PARAMS), "computation_type": "percentile",
              "normal_type": "DOY", "page_size": 1000}  # several values = repeated parameters (a comma list matches nothing)
    if days:
        params.update(start_date="%02d-%02d" % min(days), end_date="%02d-%02d" % max(days))
    out, url = {}, STATS
    for _ in range(MAX_PAGES):
        data = http.get(url, params=params, headers=headers) or {}
        for f in data.get("features", []):
            p = f.get("properties") or {}
            site = str(p.get("monitoring_location_id", "")).replace("USGS-", "")
            for ts in p.get("data", []):
                if ts.get("parent_statistic_id") != MEAN:
                    continue
                for v in ts.get("values", []):
                    if v.get("computation") != "percentile" or v.get("time_of_year_type") != "day_of_year":
                        continue
                    pv = dict(zip(v.get("percentiles", []), v.get("values", [])))
                    try:
                        day = tuple(int(x) for x in v["time_of_year"].split("-"))
                        vals = {k: float(pv[k[1:]]) for k in ("p25", "p50", "p75")}
                    except (KeyError, ValueError, TypeError):
                        continue
                    out.setdefault((site, ts.get("parameter_code")), {})[day] = dict(vals, count=v.get("sample_count"))
        tok = data.get("next") or data.get("nextToken")
        if not tok:
            break
        if str(tok).startswith("https://"):
            url, params = tok, None
        else:
            params = dict(params, next_token=tok)
    return out


class USGSWater(Adapter):
    card_id = "usgs-water"
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=15):
        """The gage-height read live() makes first (latest-continuous, parameter 00065). Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        w, s, e, n = geo.bbox(req["lat"], req["lon"], radius_km)
        q = query({"bbox": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}", "parameter_code": "00065", "f": "json", "limit": 200})
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/collections/latest-continuous/items?{q}", docs_url="https://api.waterdata.usgs.gov/docs/ogcapi/",
            notes="No key needed for light use; an optional free key (USGS_WATER_API_KEY, https://api.waterdata.usgs.gov/signup) "
                  "sent as header X-Api-Key raises the rate limit. Repeat with parameter_code=00060 for discharge. Each "
                  "feature's properties carry value, unit_of_measure, time and monitoring_location_id.")

    def live(self, lat, lon, radius_km=15, limit=5, **kw):
        w, s, e, n = geo.bbox(lat, lon, radius_km)
        key = bool(os.environ.get("USGS_WATER_API_KEY"))
        h = {"X-Api-Key": os.environ["USGS_WATER_API_KEY"]} if key else {}
        warnings = []
        out = self._ogc(lat, lon, w, s, e, n, h)  # no legacy fallback: WaterServices is being slowed, then shut (2027-02-22)
        out.sort(key=lambda o: o["distance_km"])
        out = out[: limit * len(PARAMS)]
        if kw.get("stats", True) and out:
            warnings += self._add_stats(out, h)
        if kw.get("images", True):
            try:
                out += camera_frames(lat, lon, radius_km, headers=h)
            except http.HTTPError as err:
                warnings.append(warning(self.card_id, "camera_unavailable", f"USGS camera service returned HTTP {err.status}; no photo"
                                        + ("" if key or err.status != 429 else KEY_HINT), status=err.status))
            except Exception as err:  # the gauge readings stand on their own, but say the photo is missing
                warnings.append(warning(self.card_id, "camera_unavailable", f"USGS camera service failed ({type(err).__name__}: {err}); no photo"))
        now = datetime.now(timezone.utc)
        for o in out:
            o["age_s"] = age_s(o["phenomenon_time"], now)
        return Results(out, warnings)

    rate_hint = KEY_HINT

    def live_batch(self, asks, radius_km=15, limit=5, stats=False, images=False, cell_deg=BATCH_CELL_DEG, **kw):
        """Many places in few calls: asks are grouped into cells of cell_deg degrees, and each cell's gauges come from
        one OGC request per parameter (paged by the API's next links), then are shared out to the asks within
        radius_km. Photos and daily statistics are off by default here (each is a request per site); turn them on
        and they queue under the same limit. Returns ([Results or None per ask], warnings); None = still queued."""
        h = {"X-Api-Key": os.environ["USGS_WATER_API_KEY"]} if os.environ.get("USGS_WATER_API_KEY") else {}
        cells = {}
        for i, (lat, lon) in enumerate(asks):
            cells.setdefault(_cell(lat, lon, cell_deg), []).append(i)
        per, warns = [None] * len(asks), []
        for _, idx in sorted(cells.items()):
            boxes = [geo.bbox(asks[i][0], asks[i][1], radius_km) for i in idx]
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
            try:
                feats = self._ogc_pages(box, h)
            except ratelimit.RateLimited:
                break  # this cell and every later one stay queued; live_many reports them
            now = datetime.now(timezone.utc)
            for i in idx:
                lat, lon = asks[i]
                near = [dict(o, distance_km=round(geo.distance_km(lat, lon, o["lat"], o["lon"]), 1)) for o in feats]
                near = sorted((o for o in near if o["distance_km"] <= radius_km), key=lambda o: o["distance_km"])[: limit * len(PARAMS)]
                w = []
                if stats and near:
                    w += self._add_stats(near, h)
                if images:
                    try:
                        near += camera_frames(lat, lon, radius_km, headers=h)
                    except http.HTTPError as err:
                        w.append(warning(self.card_id, "camera_unavailable", f"USGS camera service returned HTTP {err.status}; no photo", status=err.status))
                for o in near:
                    o["age_s"] = age_s(o["phenomenon_time"], now)
                per[i] = Results(near, w)
                warns += [x for x in w if x not in warns]
        return per, warns

    def batch_cost(self, asks, cell_deg=BATCH_CELL_DEG, **kw):
        """Requests live_batch would need for these asks (first pages only): one per parameter per cell."""
        return len(PARAMS) * len({_cell(lat, lon, cell_deg) for lat, lon in asks})

    def _ogc_pages(self, box, h):
        """Every latest-continuous gauge value inside a bbox, both parameters, following next links."""
        w, s, e, n = box
        out = []
        for code, label in PARAMS.items():
            url = f"{BASE}/collections/latest-continuous/items"
            params = {"bbox": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}", "parameter_code": code, "f": "json", "limit": BATCH_PAGE}
            for _ in range(MAX_PAGES):
                data = http.get(url, headers=h, params=params)
                for f in data.get("features", []):
                    p = f["properties"]
                    if p.get("value") in (None, ""):
                        continue
                    slon, slat = f["geometry"]["coordinates"][:2]
                    site = p["monitoring_location_id"]
                    out.append(observation("usgs-water", "sensor", p["time"], slat, slon, value=float(p["value"]), unit=p.get("unit_of_measure"),
                                           label=label, site=site, approval=p.get("approval_status"), qualifier=p.get("qualifier"),
                                           url=f"https://waterdata.usgs.gov/monitoring-location/{site.replace('USGS-', '')}/"))
                nxt = next((l.get("href") for l in data.get("links", []) if l.get("rel") == "next" and l.get("href")), None)
                if not nxt:
                    break
                url, params = nxt, None
        return out

    def _add_stats(self, out, headers=None):
        """Attach daily_median and daily_stats for the reading's calendar day; return warnings for what's missing."""
        sites = sorted({o["site"].replace("USGS-", "") for o in out if o.get("site")})
        days = sorted({d for d in (_local_day(o["phenomenon_time"], o.get("lon")) for o in out) if d})
        try:
            stats = daily_stats(sites, days, headers=headers)
        except Exception as err:
            status = getattr(err, "status", None)
            return [warning(self.card_id, "daily_stats_unavailable", "USGS statistics API failed"
                            + (f" (HTTP {status})" if status else f" ({type(err).__name__})") + "; no daily median", status=status)]
        missing = []
        for o in out:
            day = _local_day(o["phenomenon_time"], o.get("lon"))
            st = stats.get((o["site"].replace("USGS-", ""), CODES.get(o["label"], "")), {}).get(day) if day else None
            if st:
                o["daily_median"] = st["p50"]
                o["daily_stats"] = dict(st, date=f"{day[0]:02d}-{day[1]:02d}", unit=o.get("unit"), source=STATS)
            else:
                missing.append(f'{o["site"]} {o["label"]}')
        return [warning(self.card_id, "daily_stats_missing", "USGS publishes no daily statistics for " + ", ".join(missing))] if missing else []

    def _ogc(self, lat, lon, w, s, e, n, h):
        out = []
        for code, label in PARAMS.items():
            data = http.get(f"{BASE}/collections/latest-continuous/items", headers=h,
                            params={"bbox": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}", "parameter_code": code, "f": "json", "limit": 200})
            for f in data.get("features", []):
                p = f["properties"]
                if p.get("value") in (None, ""):
                    continue
                slon, slat = f["geometry"]["coordinates"][:2]
                site = p["monitoring_location_id"]
                out.append(observation("usgs-water", "sensor", p["time"], slat, slon, value=float(p["value"]), unit=p.get("unit_of_measure"),
                                       label=label, site=site, approval=p.get("approval_status"), qualifier=p.get("qualifier"),
                                       distance_km=round(geo.distance_km(lat, lon, slat, slon), 1),
                                       url=f"https://waterdata.usgs.gov/monitoring-location/{site.replace('USGS-', '')}/"))
        return out
