"""Private helpers for the A9 contract-satellite adapters (iceye, satellogic, capella, vantor_hub,
open_cosmos_datacosmos). Only those modules import this; discovery skips it because the name starts with "_".

What is shared: the clock (tests replace `now`), acquisition windows from an ask, ready-call scaffolding for OAuth
client-credentials providers (the token is only ever named), and turning a provider's HTTP error into a warning or a
MissingCredentials without copying a URL or body that could carry a secret."""
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .. import http, ratelimit
from .base import MissingCredentials, prepared_call, warning


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(ask, lead_h, min_width_h, max_end_h, default_width_h):
    """(start, end) for an acquisition window from an ask or spec: start `lead_h` hours from now, end at the deadline
    (`within_minutes`, or `window_days`), at least `min_width_h` wide and no later than `max_end_h` hours from now."""
    t0 = now()
    start = t0 + timedelta(hours=lead_h)
    by = _parse(ask.get("deliver_by"))
    if by:
        end = by
    elif ask.get("window_days"):
        end = start + timedelta(days=float(ask["window_days"]))
    elif ask.get("within_minutes"):
        end = t0 + timedelta(minutes=int(ask["within_minutes"]))
    else:
        end = start + timedelta(hours=default_width_h)
    end = max(end, start + timedelta(hours=min_width_h))
    if max_end_h:
        end = min(end, t0 + timedelta(hours=max_end_h))
    return start, end


def _parse(v):
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None
    return t if t and t.tzinfo else None


def env(name):
    return os.environ.get(name) or None


def has_point(ask):
    return isinstance(ask, dict) and ask.get("lat") is not None and ask.get("lon") is not None


def ph(name, what, where):
    return {"name": name, "what": what, "where": where}


def oauth_call(card_id, operation, method, url, *, token_name, token_notes, get_key_url, docs_url, notes=None, body=None,
               headers=None, placeholders=None, safe=None):
    """A ready call for a provider whose bearer token comes from an OAuth grant: the header holds "{TOKEN_NAME}" and
    notes say how to get the token (token URL and grant). Nothing is fetched."""
    h = {"Authorization": "Bearer {" + token_name + "}", **(headers or {})}
    n = token_notes + ((" " + notes) if notes else "")
    return prepared_call(card_id, operation, method, url, headers=h, body=body, auth_kind="oauth2", key_name=token_name,
                         key_in="header Authorization (Bearer)", get_key_url=get_key_url, docs_url=docs_url, notes=n,
                         placeholders=placeholders, safe_to_run_without_approval=safe)


def retry_after_s(err):
    ra = getattr(err, "retry_after", None)
    if ra is None:
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    return None if ra is None else int(-(-ra // 1))


def http_warning(card_id, err, what):
    """A provider HTTP error met during a dry run -> a warning (never raised, never copies the URL or body)."""
    status = getattr(err, "status", None)
    if status == 429 or status == 503:
        ra = retry_after_s(err)
        msg = f"{card_id} rate limit reached while asking for {what}" + (f"; retry after {ra} s" if ra is not None else "")
        return warning(card_id, "rate_limited", msg, status=status, **({"retry_after_s": ra} if ra is not None else {}))
    if status == 404:
        return warning(card_id, "not_found", f"{card_id} returned HTTP 404 for {what}", status=404)
    if status in (401, 403):
        return warning(card_id, "refused", f"{card_id} refused the request for {what} (HTTP {status})", status=status)
    return warning(card_id, "provider_error", f"{card_id} returned HTTP {status} for {what}", status=status)


def call(card_id, fn, *a, creds="", **kw):
    """Run an HTTP call for an order, status or cancel. HTTP 401 becomes MissingCredentials (the credentials were refused);
    429/503 keep their Retry-After as err.retry_after_s; everything else is re-raised as the provider sent it."""
    try:
        return fn(*a, **kw)
    except http.HTTPError as e:
        if e.status == 401:
            raise MissingCredentials(f"{card_id}: the provider refused the credentials (HTTP 401). {creds}".strip()) from None
        if e.status in (429, 503):
            e.retry_after_s = retry_after_s(e)
        raise


def dry(method, url, body=None, *, note=None, **extra):
    """A dry run in the shape the shim expects. Headers are never included (they would hold a token)."""
    out = {"dry_run": True, "would_send": {"method": method, "url": url, "body": body},
           "note": note or "Nothing was sent. Pass confirm=True to place this order; it may cost money."}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


_MONEY = re.compile(r"^\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*$")


def usd(text):
    """Decimal from a provider's USD amount string ("$1,000.00", "150.00"), or None."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return Decimal(str(text))
    m = _MONEY.match(text) if isinstance(text, str) else None
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None
