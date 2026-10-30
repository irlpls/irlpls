"""AirNow (US EPA and partner agencies): current Air Quality Index by reporting area across the US (plus Mexicali and a
few US embassy posts abroad). Keyless: reads the public reportingarea.dat file, updated at :25 and :55 past the hour. The
per-point web services (/aq/observation/current/ziplatlong/ etc.) need a free API key and their parameter docs are
visible only when logged in, so this adapter does not call them; it names them in the ready call's notes.

Sources (public, checked 2026-10-28):
- Web services list and the 2026-09-30 retirement of the older endpoints: https://docs.airnowapi.org/webservices
- FAQ (file updated at :25/:55, observations 10-30 min past the hour, area value = the maximum of its monitors, cache,
  don't loop over ZIP codes): https://docs.airnowapi.org/faq
- File: https://files.airnowtech.org/airnow/today/reportingarea.dat, pipe-delimited: issue date | valid date | valid time
  | time zone | day offset | record type (O observation, F forecast, Y yesterday) | primary flag | reporting area | state
  | lat | lon | parameter | AQI | category | action day | discussion | agency. Recorded 2026-10-28 (trimmed:
  tests/fixtures/adapters/airnow/reportingarea.dat).
Times are local with a US zone abbreviation; only the abbreviations below are converted to UTC. Any other zone (e.g.
the embassy posts' AZT, IST) keeps phenomenon_time None and reports the local time as given.
"""
import time
from datetime import datetime, timedelta, timezone

from .. import geo, http
from . import _a3
from .base import Adapter, Results, observation, prepared_call

FILE = "https://files.airnowtech.org/airnow/today/reportingarea.dat"
DOCS = "https://docs.airnowapi.org/webservices"
CACHE_S = 30 * 60   # the file changes twice an hour and is ~2 MB
# UTC offsets in hours. ADT appears in the file for Alaska (AKDT), so it is converted only for state AK.
ZONES = {"EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7, "PDT": -7, "PST": -8, "AKDT": -8, "AKST": -9,
         "HST": -10}
_cache = {}


def parse(text):
    """Current observation rows (record type O) of reportingarea.dat, as dicts."""
    rows = []
    for line in text.splitlines():
        f = line.split("|")
        if len(f) < 17 or f[5].strip() != "O":
            continue
        try:
            lat, lon, aqi = float(f[9]), float(f[10]), int(f[12])
        except ValueError:
            continue
        rows.append({"valid_date": f[1].strip(), "valid_time": f[2].strip(), "tz": f[3].strip(), "primary": f[6].strip() == "Y",
                     "area": f[7].strip(), "state": f[8].strip(), "lat": lat, "lon": lon, "parameter": f[11].strip(),
                     "aqi": aqi, "category": f[13].strip() or None, "action_day": f[14].strip() == "Yes",
                     "agency": f[16].strip() or None})
    return rows


def utc_time(row):
    """ISO 8601 UTC of the row's valid date and hour, or None when its zone isn't one we can convert."""
    tz = row["tz"]
    off = ZONES.get(tz)
    if tz == "ADT" and row["state"] == "AK":
        off = -8
    if off is None or not row["valid_time"]:
        return None
    try:
        local = datetime.strptime(f"{row['valid_date']} {row['valid_time']}", "%m/%d/%y %H:%M")
    except ValueError:
        return None
    return (local - timedelta(hours=off)).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_rows():
    hit = _cache.get("rows")
    if hit and hit[0] > time.monotonic():
        return hit[1]
    rows = parse(http.get(FILE, raw=True) or "")
    _cache["rows"] = (time.monotonic() + CACHE_S, rows)
    return rows


class AirNow(Adapter):
    card_id = "airnow"
    can_live = True

    def prepare(self, req, route, **kw):
        """The keyless file holds every reporting area; the nearest one is picked client-side. Free, so safe."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(self.card_id, "read", "GET", FILE, docs_url=DOCS,
                             notes=f"Keyless, ~2 MB, updated at :25 and :55; cache it 30 minutes. Pipe-delimited; keep rows "
                                   "whose 6th field is O (current observation), then pick the reporting area nearest to "
                                   f"{float(req['lat']):.4f},{float(req['lon']):.4f} (fields 10-11 are lat, lon) and read "
                                   "parameter (12), AQI (13) and category (14); times are local (fields 2-4). With a free "
                                   "AirNow key (https://docs.airnowapi.org/account/request/), GET "
                                   "https://www.airnowapi.org/aq/observation/current/ziplatlong/ with API_KEY={AIRNOW_API_KEY} "
                                   "answers for one point; its other parameters are documented only behind the login.")

    def live(self, lat, lon, radius_km=50, limit=1, **kw):
        """Every current pollutant AQI for the `limit` nearest reporting areas within radius_km."""
        try:
            rows = current_rows()
        except http.HTTPError as e:
            return Results([], [_a3.http_warning(self.card_id, e, "reportingarea.dat")])
        areas = {}
        for r in rows:
            areas.setdefault((r["area"], r["state"]), []).append(r)
        near = []
        for key, rs in areas.items():
            d = geo.distance_km(lat, lon, rs[0]["lat"], rs[0]["lon"])
            if d <= radius_km:
                near.append((d, rs))
        out = []
        for d, rs in sorted(near, key=lambda x: x[0])[:limit]:
            for r in rs:
                out.append(observation(self.card_id, "sensor", utc_time(r), r["lat"], r["lon"], value=r["aqi"], unit="AQI",
                                       label=f"{r['parameter']} AQI", category=r["category"], parameter=r["parameter"],
                                       reporting_area=r["area"], state=r["state"] or None, primary=r["primary"],
                                       action_day=r["action_day"], agency=r["agency"],
                                       local_time=f"{r['valid_date']} {r['valid_time']} {r['tz']}", distance_km=round(d, 1)))
        return Results(out, [])
