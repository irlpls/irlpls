"""Client-side politeness for providers the SDK calls directly: pacing, Retry-After, a small cache, fan-out.

irlpls has no hosted API: an agent's process calls each provider itself, so the limits that matter are the provider's.
This module keeps one process within them.

- One token bucket per provider id (the Capture Card id), from the card's `access.rate_limit` (Section 2.2), or
  DEFAULT (1 request/s, burst 5) when a card states no number. Reservations queue in order: many asks to one provider
  wait their turn instead of stampeding, across threads too.
- A 429 or 503 pauses that provider for everyone in the process: Retry-After (seconds or HTTP date) when sent,
  otherwise exponential backoff; capped at MAX_BACKOFF_S, at most MAX_RETRIES retries, and never past the caller's
  time budget. Past it, the HTTP error is raised with `retry_after` so the adapter reports it in warnings.
- GET responses are cached by normalised request (method, URL with sorted query, the headers that shape the answer;
  never credentials) for the provider's stated update interval (`rate_limit.cache_seconds`, else DEFAULT_CACHE_S), so
  the same ask inside it reuses the reading.
- When the next request would have to wait past the budget, RateLimited (an HTTP 429 raised before anything is sent)
  stops the work; live_many() turns it into partial results plus a warning naming what is still queued. Nothing is
  dropped silently.

Calls are attributed to a provider with `using(card_id)` (irlpls.live/commission and the broker do this); http.request
consults the current provider. Code outside `using` is not paced. Tests swap CLOCK for a fake one.
"""
import contextlib
import contextvars
import copy
import email.utils
import math
import threading
import time
import urllib.parse

from .http import HTTPError

DEFAULT = {"requests": 1, "per_seconds": 1, "burst": 5}   # for cards that state no number: keyless public feeds
DEFAULT_CACHE_S = 60
MAX_WAIT_S = 30          # longest a single call waits for its turn when no budget is given
MAX_BACKOFF_S = 60       # never sleep longer than this on one Retry-After
MAX_RETRIES = 2
CACHE_MAX = 512
RETRY_STATUSES = (429, 503)

# Limits for services the SDK calls that have no Capture Card. Nominatim's usage policy: at most 1 request per second.
EXTRA = {"nominatim": {"requests": 1, "per_seconds": 1, "burst": 1, "cache_seconds": 86400,
                       "source_url": "https://operations.osmfoundation.org/policies/nominatim/"}}


class _Clock:
    def now(self):
        return time.monotonic()

    def sleep(self, s):
        if s > 0:
            time.sleep(s)


CLOCK = _Clock()
_current = contextvars.ContextVar("irlpls_provider", default=None)
_deadline = contextvars.ContextVar("irlpls_deadline", default=None)
_lock = threading.Lock()
_buckets, _cache, _overrides = {}, {}, {}
STATS = {"sent": 0, "cached": 0, "retried": 0, "refused": 0}


class RateLimited(HTTPError):
    """Refused before sending: the provider's next slot is past the caller's budget. An HTTP 429 (status 429,
    client_side True), so adapters that already handle a provider's 429 handle this one too."""

    def __init__(self, provider, url, retry_after, limit_text):
        super().__init__(429, url, f"client-side limit for {provider}: not sent; next slot in {retry_after:.0f}s ({limit_text})")
        self.provider, self.retry_after, self.limit_text, self.client_side = provider, retry_after, limit_text, True


def reset():
    """Forget every bucket, cached response, override and counter (tests)."""
    with _lock:
        _buckets.clear()
        _cache.clear()
        _overrides.clear()
        for k in STATS:
            STATS[k] = 0


def configure(provider, **limit):
    """Override a provider's limit in this process (a paid plan, a provider's written permission): requests,
    per_seconds, burst, cache_seconds, batch_max. configure(provider) with no fields removes the override."""
    with _lock:
        if limit:
            _overrides[provider] = dict(limit)
        else:
            _overrides.pop(provider, None)
        _buckets.pop(provider, None)


def _card_limit(provider):
    if provider in EXTRA:
        return EXTRA[provider], False
    try:
        from . import adapters, card
        c = card(provider) or {}
        a = adapters.get(provider)
        keyed = bool(a and a.env and a.configured())
    except Exception:
        return {}, False
    return ((c.get("access") or {}).get("rate_limit") or {}), keyed


def limits(provider):
    """{requests, per_seconds, burst, cache_seconds, batch_max, scope, source_url, stated} in force for a provider: the
    card's tier (`with_key` when the requester's key is set), a configure() override, or DEFAULT."""
    rl, keyed = _card_limit(provider)
    tier = dict(rl)
    tier.pop("with_key", None)
    if keyed and isinstance(rl.get("with_key"), dict):
        tier.update(rl["with_key"])
    tier.update(_overrides.get(provider, {}))
    stated = bool(tier.get("requests") and tier.get("per_seconds"))
    out = dict(tier) if stated else dict(tier, **DEFAULT)
    out["stated"] = stated or provider in _overrides
    out.setdefault("burst", min(int(out["requests"]), DEFAULT["burst"]))
    out.setdefault("cache_seconds", DEFAULT_CACHE_S)
    out["keyed"] = keyed
    return out


def _name(provider):
    try:
        from . import card
        return (card(provider) or {}).get("name") or provider
    except Exception:
        return provider


def describe(provider):
    """One line for warnings: 'USGS Water allows 30 requests per minute per IP' or 'X publishes no number; irlpls paces …'."""
    lim = limits(provider)
    name = _name(provider)
    n, s = lim["requests"], lim["per_seconds"]
    per = {1: "second", 60: "minute", 3600: "hour", 86400: "day"}.get(s, f"{s} s")
    rate = f"{n} request{'s' if n != 1 else ''} per {per}"
    if not lim["stated"]:
        return f"{name} publishes no number; irlpls paces itself at {rate} (burst {lim['burst']})"
    scope = {"per-key": " per key", "per-ip": " per IP", "per-account": " per account"}.get(lim.get("scope"), "")
    return f"{name} allows {rate}{scope}" + (f", at most {lim['batch_max']} items per request" if lim.get("batch_max") else "")


class Bucket:
    """Token bucket with reservations: tokens may go negative, which is the queue. Thread-safe."""

    def __init__(self, rate, burst):
        self.rate, self.burst = float(rate), float(burst)
        self.tokens, self.t, self.blocked_until = float(burst), CLOCK.now(), 0.0
        self.lock = threading.Lock()

    def _refill(self, now):
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def wait_for_next(self):
        """Seconds until a request could go now (without reserving)."""
        with self.lock:
            now = CLOCK.now()
            self._refill(now)
            return max(self.blocked_until - now, max(0.0, 1 - self.tokens) / self.rate, 0.0)

    def reserve(self, max_wait):
        """Take the next slot: seconds to wait before sending, or None (nothing reserved) if that is over max_wait."""
        with self.lock:
            now = CLOCK.now()
            self._refill(now)
            ready = max(self.blocked_until, now + max(0.0, 1 - self.tokens) / self.rate)
            if ready - now > max_wait + 1e-9:
                return None
            self.tokens -= 1
            return ready - now

    def block(self, seconds):
        """The provider said wait (429/503): nobody in this process sends before then."""
        with self.lock:
            now = CLOCK.now()
            self._refill(now)
            self.blocked_until = max(self.blocked_until, now + seconds)
            self.tokens = min(self.tokens, 0.0)


def bucket(provider):
    with _lock:
        b = _buckets.get(provider)
    if b is None:
        lim = limits(provider)
        b = Bucket(lim["requests"] / lim["per_seconds"], lim["burst"])
        with _lock:
            b = _buckets.setdefault(provider, b)
    return b


@contextlib.contextmanager
def using(provider, budget_s=None):
    """Attribute HTTP calls inside the block to `provider`; budget_s bounds the total wait for the whole block."""
    deadline = CLOCK.now() + budget_s if budget_s is not None else _deadline.get()
    t1, t2 = _current.set(provider), _deadline.set(deadline)
    try:
        yield
    finally:
        _current.reset(t1)
        _deadline.reset(t2)


def current():
    return _current.get()


def remaining():
    """Seconds left in the current budget (MAX_WAIT_S for a single call without one)."""
    d = _deadline.get()
    return MAX_WAIT_S if d is None else max(0.0, d - CLOCK.now())


_SHAPING_HEADERS = ("accept", "accept-language")


def cache_key(provider, method, url, headers=None, raw=False):
    """Normalised request: query parameters sorted, empty ones dropped, scheme/host lower-cased; credential headers ignored."""
    u = urllib.parse.urlsplit(url)
    q = sorted((k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=False))
    norm = urllib.parse.urlunsplit((u.scheme.lower(), (u.netloc or "").lower(), u.path or "/", urllib.parse.urlencode(q), ""))
    shaping = tuple(sorted((k.lower(), str(v)) for k, v in (headers or {}).items() if k.lower() in _SHAPING_HEADERS))
    return (provider, method.upper(), norm, shaping, raw if isinstance(raw, str) else bool(raw))   # raw may name a variant


def retry_after(headers):
    """Seconds from a Retry-After header (delta-seconds or HTTP date), or None."""
    v = next((val for k, val in (headers or {}).items() if str(k).lower() == "retry-after"), None)
    if v is None:
        return None
    v = str(v).strip()
    if v.isdigit():
        return float(v)
    try:
        t = email.utils.parsedate_to_datetime(v)
        return max(0.0, t.timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


def call(provider, method, url, send, headers=None, raw=False):
    """Run `send()` (one HTTP request) under the provider's limit, cache and retry rules."""
    lim = limits(provider)
    key = cache_key(provider, method, url, headers, raw) if method.upper() == "GET" else None
    if key is not None:
        with _lock:
            hit = _cache.get(key)
        if hit and hit[0] > CLOCK.now():
            STATS["cached"] += 1
            return copy.deepcopy(hit[1])
    b = bucket(provider)
    for attempt in range(MAX_RETRIES + 1):
        wait = b.reserve(remaining())
        if wait is None:
            STATS["refused"] += 1
            raise RateLimited(provider, url, b.wait_for_next(), describe(provider))
        CLOCK.sleep(wait)
        try:
            STATS["sent"] += 1
            out = send()
        except HTTPError as e:
            if e.status not in RETRY_STATUSES or getattr(e, "client_side", False):
                raise
            ra = retry_after(getattr(e, "headers", None))
            delay = ra if ra is not None else min(MAX_BACKOFF_S, 2.0 ** attempt)
            e.retry_after = delay
            b.block(min(delay, MAX_BACKOFF_S))
            if ra is not None and ra > MAX_BACKOFF_S or attempt == MAX_RETRIES or delay > remaining():
                raise
            STATS["retried"] += 1
            continue
        if key is not None:
            with _lock:
                if len(_cache) >= CACHE_MAX:
                    now = CLOCK.now()
                    for k in [k for k, v in _cache.items() if v[0] <= now] or list(_cache)[: CACHE_MAX // 4]:
                        _cache.pop(k, None)
                _cache[key] = (CLOCK.now() + lim["cache_seconds"], copy.deepcopy(out))
        return out


# -- fan-out -----------------------------------------------------------------------------------------------------------
def _ask_point(ask):
    if isinstance(ask, (tuple, list)) and len(ask) == 2:
        return float(ask[0]), float(ask[1])
    if isinstance(ask, dict) and ask.get("lat") is not None:
        return float(ask["lat"]), float(ask["lon"])
    raise ValueError(f"each ask needs lat and lon (or a (lat, lon) pair): {ask!r}")


def batch_warning(provider, n, answered, pending, retry_s, drain_s, hint=""):
    from .adapters.base import warning
    return warning(provider, "rate_limited",
                   f"rate limited: {len(pending)} of {n} asks still queued (not sent; {answered} answered). "
                   f"{describe(provider)}; at that pace the queued asks need about {math.ceil(drain_s)} s more. "
                   f"Resubmit them (warning.pending) once, with budget_s of at least that, no sooner than {math.ceil(retry_s)} s "
                   f"from now{hint}; do not loop faster.",
                   pending=pending, retry_after_s=math.ceil(retry_s), drain_s=math.ceil(drain_s), answered=answered, asked=n)


def live_many(card_id, asks, budget_s=60, **kw):
    """Answer many asks (each {lat, lon} or (lat, lon)) from one provider without stampeding it.

    Identical asks are answered once. An adapter with live_batch() combines asks into few requests (USGS: many sites in
    one call); otherwise asks queue one after another under the provider's limit. Whatever cannot be sent within
    budget_s seconds stays queued: the result has an entry per ask (None for queued ones) and a `rate_limited` warning
    listing the queued indices. Returns adapters.base.Results of per-ask Results, with .warnings and .pending."""
    from . import adapters
    from .adapters.base import Results
    a = adapters.get(card_id)
    if not a:
        raise adapters.NotSupported(f"no adapter for {card_id!r}")
    points = [_ask_point(x) for x in asks]
    uniq, index = [], {}
    for p in points:
        k = (round(p[0], 4), round(p[1], 4))
        if k not in index:
            index[k] = len(uniq)
            uniq.append(k)
    with using(card_id, budget_s=budget_s):
        if hasattr(a, "live_batch"):
            per, warns = a.live_batch(uniq, **kw)
        else:
            per, warns = [None] * len(uniq), []
            for i, (lat, lon) in enumerate(uniq):
                try:
                    r = a.live(lat, lon, **kw)
                except RateLimited:
                    break
                per[i] = r
                warns += [w for w in getattr(r, "warnings", []) if w not in warns]
    out = [per[index[(round(p[0], 4), round(p[1], 4))]] for p in points]
    pending = [i for i, r in enumerate(out) if r is None]
    if pending:
        hint = getattr(a, "rate_hint", "") if not limits(card_id)["keyed"] else ""
        warns = [w for w in warns if w.get("code") != "rate_limited"]
        lim = limits(card_id)
        left = sorted({index[(round(points[i][0], 4), round(points[i][1], 4))] for i in pending})
        cost = a.batch_cost([uniq[i] for i in left], **kw) if hasattr(a, "batch_cost") else len(left)
        drain = cost * lim["per_seconds"] / lim["requests"]
        warns.append(batch_warning(card_id, len(points), len(points) - len(pending), pending, bucket(card_id).wait_for_next() + 1, drain, hint))
    res = Results(out, warns)
    res.pending = pending
    return res
