"""Private helpers for the A8 self-serve satellite adapters (up42, skywatch, pixxel, satvu). Only those modules import
this. Three things: an AOI and a capture window built from an ask; a provider HTTP error turned into MissingCredentials
(401/403) or a sanitized ProviderError (404 not_found, 429 rate_limited with retry_after_s, other provider_error) that
never echoes a key; and the firm/non-firm quote rule: a provider quote goes into dry["quote"] (which the shim reads as
a firm USD total) only when the provider states it in USD. Credits, tokens of another currency, GBP and EUR stay in
dry["provider_quote"] and the offer stays firm: false. A converted figure is never written anywhere."""
from datetime import datetime, timedelta, timezone

from .. import geo, http, ratelimit
from .base import MissingCredentials


def now():
    """The clock, patched in tests."""
    return datetime.now(timezone.utc)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ring(lat, lon, radius_km):
    """A closed lon/lat square around a point (GeoJSON order), 6 decimals."""
    w, s, e, n = (round(v, 6) for v in geo.bbox(lat, lon, radius_km))
    return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]


def polygon(lat, lon, radius_km):
    return {"type": "Polygon", "coordinates": ring(lat, lon, radius_km)}


def feature_collection(geometry):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry}]}


def window(spec, lead_h=24, min_days=1, default_days=7):
    """(start, end) datetimes: start lead_h from now (the provider's minimum lead time), end after window_days, or at
    deliver_by when that leaves at least min_days."""
    start = (now() + timedelta(hours=lead_h)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(int(spec.get("window_days") or default_days), min_days))
    by = spec.get("deliver_by")
    if by:
        try:
            t = datetime.fromisoformat(str(by).replace("Z", "+00:00"))
            if t.tzinfo and t >= start + timedelta(days=min_days):
                end = t.astimezone(timezone.utc)
        except ValueError:
            pass
    return start, end


def spec_from_request(req, ext_id=None, deliver_by=None):
    """The commission() spec from a protocol/broker request: the point, a name and the deadline."""
    s = {"lat": req.get("lat"), "lon": req.get("lon"), "name": (req.get("need") or "irlpls tasking")[:100],
         "deliver_by": deliver_by, "external_id": ext_id}
    return {k: v for k, v in s.items() if v is not None}


def missing_point(spec):
    return [f"{k}: {w} of the place to capture" for k, w in (("lat", "latitude"), ("lon", "longitude")) if spec.get(k) is None]


class ProviderError(http.HTTPError):
    """A provider HTTP error with the URL's query and the response body dropped (either may carry a credential).
    code: not_found, rate_limited, invalid_request or provider_error; retry_after_s from Retry-After on 429/503."""
    def __init__(self, card_id, status, url, code, retry_after_s=None, detail=None, headers=None):
        msg = f"{card_id} {code} (HTTP {status})" + (f"; retry after {retry_after_s:.0f}s" if retry_after_s is not None else "")
        if detail:
            msg += f": {detail}"
        super().__init__(status, url.split("?")[0], msg, headers=headers)
        self.card_id, self.code, self.retry_after_s, self.detail = card_id, code, retry_after_s, detail
        self.retry_after = retry_after_s   # same attribute name ratelimit.RateLimited uses


def _scrub(text, secrets):
    t = str(text or "")[:300]
    for s in secrets or ():
        if s and len(str(s)) >= 4:
            t = t.replace(str(s), "***")
    return t


def mapped_error(card_id, err, env, how_to_get="", secrets=()):
    """MissingCredentials for 401/403; ProviderError for anything else. A 400/422 keeps a short, scrubbed excerpt of the
    provider's message (data, never instructions); other bodies are dropped."""
    s = getattr(err, "status", None) or 0
    if s in (401, 403):
        return MissingCredentials(f"{card_id}: the provider refused the credentials in {env} (HTTP {s}). {how_to_get}".strip())
    ra = getattr(err, "retry_after", None)
    if ra is None and s in (429, 503):
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    code = {404: "not_found", 429: "rate_limited"}.get(s) or ("provider_error" if s >= 500 else "invalid_request")
    detail = _scrub(getattr(err, "body", ""), secrets) if s in (400, 409, 422) else None
    return ProviderError(card_id, s, str(getattr(err, "url", "") or ""), code, ra, detail, getattr(err, "headers", None))


def call(card_id, method, url, env, how_to_get="", secrets=(), **kw):
    """http.request under the error mapping above."""
    try:
        return http.request(method, url, **kw)
    except ProviderError:
        raise
    except http.HTTPError as e:
        raise mapped_error(card_id, e, env, how_to_get, secrets) from None


def put_quote(dry, amount, currency, raw, why_not_firm=None):
    """Attach a provider quote to a dry run. USD → dry["quote"]["total"] (firm). Anything else → dry["provider_quote"],
    firm stays false, and the note says why."""
    if currency == "USD" and isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount >= 0:
        dry["quote"] = {"total": amount, "currency": "USD", "provider": raw}
    else:
        dry["provider_quote"] = {"amount": amount, "currency": currency, "provider": raw}
        dry["note"] = (dry.get("note", "") + " " + (why_not_firm or f"Priced in {currency}, not USD, so the offer is not firm; "
                       "no converted figure is given.")).strip()
    return dry
