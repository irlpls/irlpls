"""Private helpers for the A3 camera and air adapters (tfl_jamcams, wsdot_cameras, airnow, empiric_earth). Only those
modules import this. Two things: an image's capture time from its Last-Modified header (through http.head, so the URL
checks, redirect rules and provider pacing apply), and turning a provider's HTTP error into a warning or a
MissingCredentials without ever echoing a URL or body that could carry a key."""
from datetime import timezone
from email.utils import parsedate_to_datetime

from .. import http, ratelimit
from .base import MissingCredentials, warning


def last_modified(url):
    """(ISO 8601 UTC capture time from the image's Last-Modified header, None) or (None, reason). Never made up."""
    try:
        lm = http.head(url).get("last-modified")
    except http.HTTPError as e:
        return None, f"HTTP {e.status}"
    except (OSError, ValueError, http.UnsafeURL) as e:
        return None, type(e).__name__
    if not lm:
        return None, "no Last-Modified header"
    try:
        t = parsedate_to_datetime(lm)
    except (TypeError, ValueError, IndexError):
        return None, "unreadable Last-Modified header"
    if t.tzinfo is None:
        return None, "Last-Modified header without a time zone"
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), None


def http_warning(card_id, err, what, keyed=False, env=None, how_to_get=""):
    """Map a provider HTTP error to a warning dict (429 rate_limited with retry_after_s, 404 not_found, other
    provider_error), or raise MissingCredentials for 401/403 when the call carried the requester's key. The message is
    built from the status only: the provider's URL and body are never copied (a key may sit in a query string)."""
    status = getattr(err, "status", None)
    if status in (401, 403) and keyed:
        raise MissingCredentials(f"{card_id}: the provider refused the credentials in {env} (HTTP {status}). {how_to_get}".strip())
    if status == 429:
        ra = getattr(err, "retry_after", None)
        if ra is None:
            ra = ratelimit.retry_after(getattr(err, "headers", None))
        msg = f"{card_id} rate limit reached while fetching {what}" + (f"; retry after {ra:.0f}s" if ra is not None else "")
        return warning(card_id, "rate_limited", msg, status=429, retry_after_s=ra,
                       client_side=bool(getattr(err, "client_side", False)))
    if status == 404:
        return warning(card_id, "not_found", f"{card_id} returned HTTP 404 for {what}", status=404)
    if status in (401, 403):
        return warning(card_id, "refused", f"{card_id} refused the request for {what} (HTTP {status})", status=status)
    return warning(card_id, "provider_error", f"{card_id} returned HTTP {status} for {what}", status=status)
