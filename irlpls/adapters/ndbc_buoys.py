"""NOAA National Data Buoy Center: the latest observation from the buoys and coastal stations nearest a point, plus the
newest BuoyCAM still where the station has a camera. Free, no key; US government data (public domain).

Sources (public docs, read 2026-10-28):
- Real-time data access: https://www.ndbc.noaa.gov/faq/rt_data_access.shtml
- latest_obs.txt (one row per reporting station: STN LAT LON YYYY MM DD hh mm and the standard variables, times in UTC,
  "MM" = missing): https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt
- Measurement descriptions and units: https://www.ndbc.noaa.gov/faq/measdes.shtml
- BuoyCAM list (JSON: id, name, lat, lng, img; img null when there is no recent photo): https://www.ndbc.noaa.gov/buoycams.php
  and individual stills at /images/buoycam/{img}.
NDBC publishes no rate number; it asks users to "limit your retrievals to a minimal level", so live() makes at most two
GETs (the station file and the camera list), both cached by irlpls.ratelimit.
"""
from datetime import datetime, timezone

from .. import geo, http, ratelimit
from .base import Adapter, Results, observation, prepared_call, warning

BASE = "https://www.ndbc.noaa.gov"
LATEST = f"{BASE}/data/latest_obs/latest_obs.txt"
CAMS = f"{BASE}/buoycams.php"
DOCS = "https://www.ndbc.noaa.gov/faq/rt_data_access.shtml"

# column -> (label, unit). Units from the file's own second header line and https://www.ndbc.noaa.gov/faq/measdes.shtml.
VARS = {
    "WDIR": ("wind direction (from)", "degT"), "WSPD": ("wind speed", "m/s"), "GST": ("wind gust", "m/s"),
    "WVHT": ("significant wave height", "m"), "DPD": ("dominant wave period", "s"), "APD": ("average wave period", "s"),
    "MWD": ("wave direction (from)", "degT"), "PRES": ("sea-level pressure", "hPa"), "PTDY": ("pressure tendency", "hPa"),
    "ATMP": ("air temperature", "degC"), "WTMP": ("water temperature", "degC"), "DEWP": ("dew point", "degC"),
    "VIS": ("visibility", "nmi"), "TIDE": ("water level (tide)", "ft"),
}
TIME_COLS = ("YYYY", "MM", "DD", "hh", "mm")


def _num(s):
    if s is None or s == "MM":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_latest_obs(text):
    """Rows of latest_obs.txt as dicts: station, lat, lon, time (ISO UTC or None) and values {column: float} with every
    "MM" (missing) left out. Columns are read from the file's own header, so a reordered file still parses."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    header = next((l for l in lines if l.startswith("#") and "LAT" in l), None)
    if header is None:
        raise ValueError("latest_obs.txt: no header line")
    cols = header.lstrip("#").split()
    if cols[:3] != ["STN", "LAT", "LON"]:
        raise ValueError(f"latest_obs.txt: unexpected header {cols[:3]}")
    idx = {}
    for i, c in enumerate(cols):   # "MM" appears once as the month column; values use "MM" for missing
        idx.setdefault(c, i)
    out = []
    for l in lines:
        if l.startswith("#"):
            continue
        f = l.split()
        if len(f) < len(cols):
            continue
        try:
            lat, lon = float(f[1]), float(f[2])
        except ValueError:
            continue
        try:
            y, mo, d, h, mi = (int(f[idx[c]]) for c in TIME_COLS)
            t = datetime(y, mo, d, h, mi, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            t = None
        vals = {c: v for c in VARS if c in idx and (v := _num(f[idx[c]])) is not None}
        out.append({"station": f[0], "lat": lat, "lon": lon, "time": t, "values": vals})
    return out


def _warn(card_id, what, err):
    ra = getattr(err, "retry_after", None)
    if ra is None:
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    code = {401: "refused", 403: "refused", 404: "not_found", 429: "rate_limited"}.get(err.status, "provider_error")
    return warning(card_id, code, f"{what}: HTTP {err.status}", status=err.status,
                   **({"retry_after_s": int(ra)} if ra is not None else {}))


class NDBCBuoys(Adapter):
    card_id = "ndbc-buoys"
    can_live = True

    def prepare(self, req, route, radius_km=100, limit=2):
        """The station file is one keyless GET for every station, so it is ready even without a place."""
        if route != "live":
            return None
        where = (f" Keep the {limit} stations nearest {req['lat']:.4f},{req['lon']:.4f} within {radius_km} km."
                 if req.get("lat") is not None and req.get("lon") is not None else " Filter rows by distance to the place.")
        return prepared_call(self.card_id, "read", "GET", LATEST, headers={"Accept": "text/plain"}, docs_url=DOCS,
                             notes="Plain text, one row per reporting station: STN LAT LON YYYY MM DD hh mm then WDIR WSPD GST "
                                   "WVHT DPD APD MWD PRES PTDY ATMP WTMP DEWP VIS TIDE (units on the second header line; times "
                                   "UTC; MM = missing)." + where + f" For a photo, GET {CAMS} (JSON) and, where a station's "
                                   "img is not null, fetch /images/buoycam/{img}.")

    def live(self, lat, lon, radius_km=100, limit=2, images=True, **kw):
        rows = parse_latest_obs(http.get(LATEST, raw=True, headers={"Accept": "text/plain"}))
        dist = lambda r: geo.distance_km(lat, lon, r["lat"], r["lon"])
        near = [r for r in sorted(rows, key=dist) if dist(r) <= radius_km][:limit]
        out, warns = [], []
        for r in near:
            d = round(dist(r), 1)
            for c, v in r["values"].items():
                label, unit = VARS[c]
                out.append(observation(self.card_id, "sensor", r["time"], r["lat"], r["lon"], value=v, unit=unit, label=label,
                                       url=f"{BASE}/station_page.php?station={r['station'].lower()}", station=r["station"],
                                       variable=c, distance_km=d))
        if images and near:
            try:
                cams = http.get(CAMS) or []
            except http.HTTPError as err:
                cams = []
                warns.append(_warn(self.card_id, "BuoyCAM list unavailable; no photo", err))
            want = {r["station"].upper(): r for r in near}
            for c in cams if isinstance(cams, list) else []:
                r = want.get(str(c.get("id", "")).upper())
                if r and c.get("img"):
                    # The file name carries a date, but NDBC does not document its time zone, so it is not used as
                    # phenomenon_time (never made up): the station row's time is given as station_obs_time instead.
                    out.append(observation(self.card_id, "photo", None, c.get("lat"), c.get("lng"),
                                           label="BuoyCAM still (latest)", url=f"{BASE}/images/buoycam/{c['img']}",
                                           station=r["station"], site=c.get("name"), image_file=c["img"],
                                           station_obs_time=r["time"],
                                           distance_km=round(dist(r), 1)))
        return Results(out, warns)
