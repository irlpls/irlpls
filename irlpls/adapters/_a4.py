"""Private helpers for the A4 agent-marketplace adapters (human_for_ai, errand, getterdone, mundane). Only those modules
import this. Three things: deadlines (a protocol `deliver_by` or a broker `within_minutes` turned into what each provider
takes), short task text built from the need, and provider HTTP errors turned into warnings or MissingCredentials without
ever echoing a URL, header or body that could carry a key (only a provider's own upper/lower-case error code is kept)."""
import json
import re
from datetime import datetime, timedelta, timezone

from .. import http, ratelimit
from .base import MissingCredentials, warning

PHOTO = re.compile(r"\b(photo\w*|pictures?|pics?|images?|snap|shoot|storefront|shelf|menu|sign)\b", re.I)
VERIFY = re.compile(r"\b(verify|verif\w*|confirm|check|is it open|whether|exists?|price)\b", re.I)
QUEUE = re.compile(r"\b(queue|line|wait(ing)?)\b", re.I)


def now_utc():
    return datetime.now(timezone.utc)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def minutes_until(deliver_by=None, within_minutes=None, now=None):
    """Minutes from now to the deadline (deliver_by ISO 8601 wins over within_minutes), or None when neither is given."""
    t = parse_time(deliver_by) if deliver_by else None
    if t is not None:
        return max(0, int((t - (now or now_utc())).total_seconds() // 60))
    if isinstance(within_minutes, (int, float)) and not isinstance(within_minutes, bool) and within_minutes > 0:
        return int(within_minutes)
    return None


def deadline_iso(deliver_by=None, within_minutes=None, now=None):
    """The deadline as ISO 8601 UTC, or None. A deliver_by that parses is passed through (normalised to UTC)."""
    t = parse_time(deliver_by) if deliver_by else None
    if t is not None:
        return iso(t)
    m = minutes_until(None, within_minutes)
    return iso((now or now_utc()) + timedelta(minutes=m)) if m else None


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def where(req):
    """A place label for the worker: the place name, else the coordinates."""
    if req.get("place"):
        return str(req["place"])
    if req.get("lat") is not None and req.get("lon") is not None:
        return f"{float(req['lat']):.5f}, {float(req['lon']):.5f}"
    return None


def provider_code(err):
    """The provider's own machine-readable error code from an error body ({"error": "CAP_AREA"} or {"code": ...}), or
    None. Only a short identifier is kept, never free text."""
    try:
        b = json.loads(getattr(err, "body", "") or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(b, dict):
        return None
    for k in ("code", "error"):
        v = b.get(k)
        if isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,59}", v):
            return v
    return None


def retry_after_s(err):
    ra = getattr(err, "retry_after", None)
    if ra is None:
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    return None if ra is None else int(-(-ra // 1))


def http_warning(card_id, err, what):
    """A provider HTTP error as a warning: 401/403 refused, 404 not_found, 429 rate_limited (retry_after_s from
    Retry-After), else provider_error. Built from the status and the provider's error code only."""
    status = getattr(err, "status", None)
    code = provider_code(err)
    extra = {"status": status}
    if code:
        extra["provider_code"] = code
    if status == 429:
        ra = retry_after_s(err)
        return warning(card_id, "rate_limited", f"{card_id} rate limit reached while {what}"
                       + (f"; retry after {ra} s" if ra is not None else ""), retry_after_s=ra, **extra)
    if status == 404:
        return warning(card_id, "not_found", f"{card_id} returned HTTP 404 while {what}", **extra)
    if status in (401, 403):
        return warning(card_id, "refused", f"{card_id} refused the request while {what} (HTTP {status}"
                       + (f" {code}" if code else "") + ")", **extra)
    return warning(card_id, "provider_error", f"{card_id} returned HTTP {status} while {what}", **extra)


def raise_if_unauthorized(card_id, err, env, how_to_get):
    """401 means the key is missing, wrong or revoked: raise MissingCredentials (the key itself is never in the message).
    Anything else is left to the caller."""
    if isinstance(err, http.HTTPError) and err.status == 401:
        raise MissingCredentials(f"{card_id}: the provider refused the key in {env} (HTTP 401). {how_to_get}".strip()) from None
