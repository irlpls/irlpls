"""Private helper for batch A1 (usgs_earthquakes, emsc_seismicportal, geonet_nz, nasa_firms). Not a shared module.

FDSN Event Web Service (fdsnws-event 1.x), as served by USGS and EMSC:
- spec: https://www.fdsn.org/webservices/fdsnws-event-1.2.pdf
- USGS: https://earthquake.usgs.gov/fdsnws/event/1/ (format=geojson; radius `maxradiuskm`; times in epoch ms)
- EMSC: https://www.seismicportal.eu/fdsn-wsevent.html (format=json; radius `maxradius` in degrees; ISO times;
  no events -> HTTP 204, which http.request returns as None)
Both cap a request at 20,000 events, so every query here is bounded by time, radius and limit.

Error mapping for all four adapters: a documented HTTP error becomes an empty result with a warning (never a made-up
reading); anything else is raised.
"""
import urllib.parse
from datetime import datetime, timedelta, timezone

from .. import geo, http
from .base import Results, warning

KM_PER_DEG = 111.195   # mean great-circle km per degree, for services whose radius is in degrees


def starttime(days, now=None):
    """FDSN starttime (UTC, no offset, as the spec writes it) `days` before now."""
    t = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def query_params(lat, lon, radius_km, limit, days, fmt, radius_in_km, now=None, minmag=None):
    p = {"format": fmt, "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "starttime": starttime(days, now),
         "orderby": "time", "limit": int(max(1, min(limit, 20000)))}
    if radius_in_km:
        p["maxradiuskm"] = round(min(radius_km, 20001.6), 1)
    else:
        p["maxradius"] = round(min(radius_km / KM_PER_DEG, 180.0), 4)
    if minmag is not None:
        p["minmagnitude"] = minmag
    return p


def url_with(base, params):
    return base + "?" + urllib.parse.urlencode(params)


def iso_ms(ms):
    """Epoch milliseconds (USGS) -> ISO 8601 UTC, or None."""
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ms) % 1000:03d}Z"


def features(doc):
    """GeoJSON features with a point geometry; [] for an empty or 204 response."""
    if not isinstance(doc, dict):
        return []
    out = []
    for f in doc.get("features") or []:
        c = ((f or {}).get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2 and all(isinstance(x, (int, float)) for x in c[:2]):
            out.append(f)
    return out


def distance(lat, lon, f):
    c = f["geometry"]["coordinates"]
    return round(geo.distance_km(lat, lon, c[1], c[0]), 1)


ERRORS = {400: ("bad_request", "rejected the request"), 401: ("unauthorized", "refused the credentials"),
          403: ("forbidden", "refused access"), 404: ("not_found", "has no such resource"),
          413: ("too_many_events", "says the query matches too many events"),
          429: ("rate_limited", "is rate-limiting requests"), 503: ("unavailable", "is unavailable")}


def error_result(card_id, err):
    """An http.HTTPError -> Results([], [warning]) for the statuses in ERRORS; None for any other status (re-raise).
    429/503 carry retry_after_s from the Retry-After header (or the pacer's figure). The provider's error body is
    data, so only its first 200 characters are kept, and never the request URL (it may carry a key)."""
    status = getattr(err, "status", None)
    if status not in ERRORS:
        return None
    code, what = ERRORS[status]
    extra = {"status": status}
    if status in (429, 503):
        from .. import ratelimit
        ra = getattr(err, "retry_after", None)
        if ra is None:
            ra = ratelimit.retry_after(getattr(err, "headers", None))
        if ra is not None:
            extra["retry_after_s"] = int(-(-ra // 1))
    body = (getattr(err, "body", "") or "").strip()[:200]
    msg = f"{card_id} {what} (HTTP {status})" + (f": {body}" if body else "")
    if extra.get("retry_after_s") is not None:
        msg += f"; retry after {extra['retry_after_s']} s"
    return Results([], [warning(card_id, code, msg, **extra)])


def guarded(card_id, fn, scrub=()):
    """Run fn(); map documented HTTP errors to a warning. `scrub` strings (a key) are removed from any warning text."""
    try:
        return fn()
    except http.HTTPError as err:
        res = error_result(card_id, err)
        if res is None:
            if not any(scrub):
                raise
            clean = lambda t: _scrub(t, scrub)   # noqa: E731
            raise http.HTTPError(err.status, clean(err.url), clean(err.body or ""), headers=err.headers) from None
        for w in res.warnings:
            w["msg"] = _scrub(w["msg"], scrub)
        return res


def _scrub(text, secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    return text
