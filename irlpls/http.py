"""Tiny stdlib HTTP helper so the SDK has no dependencies.

Outbound hygiene (docs/STANDARD.md Sections 12 and 14.12): only http(s); every host is resolved and refused if any address is
private, loopback, link-local or otherwise non-public, or if it cannot be resolved (fail closed); at most MAX_REDIRECTS
redirects, each re-checked, never from https to http, and a redirect to another origin drops every credential header
(Authorization, Cookie, Proxy-Authorization, API-key headers); a MAX_BYTES cap on the body and on the body after gzip
decompression; a timeout. Calls made inside ratelimit.using(<card id>) are paced, retried on 429/503 (Retry-After) and
cached per provider (irlpls/ratelimit.py). Every request names irlpls and a contact in its User-Agent. Tests (or a local
broker on purpose) can opt out with allow_private=True or IRLPLS_ALLOW_PRIVATE_HOSTS=1.

Known limit: the address is checked before the connection is opened, not pinned to it, so a DNS-rebinding attacker
with a very short TTL is not fully excluded. Run the broker where it cannot reach internal services as well.
"""
import contextlib
import contextvars
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib

# Providers (NWS, Nominatim, Digitraffic, Sensor.Community) ask callers to identify themselves with a way to reach them.
USER_AGENT = "irlpls/0.1 (+https://irlpls.com; hello@irlpls.com)"
MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5


class HTTPError(RuntimeError):
    def __init__(self, status, url, body, headers=None):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status, self.url, self.body = status, url, body
        self.headers = dict(headers or {})  # Retry-After and RateLimit-* live here


class UnsafeURL(ValueError):
    """The URL was refused before any request was made (scheme, host or address)."""


class TooLarge(RuntimeError):
    """The response was larger than MAX_BYTES (before or after decompression)."""


def _resolve(host):
    return [ai[4][0] for ai in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def is_public_ip(addr):
    ip = ipaddress.ip_address(addr.split("%")[0])
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url, allow_private=False, allow_http=True):
    """Raise UnsafeURL unless `url` is http(s) and (unless allow_private) every address its host resolves to is public."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL(f"only http(s) URLs with a host are allowed: {url[:100]!r}")
    if u.scheme == "http" and not allow_http:
        raise UnsafeURL(f"https required: {url[:100]!r}")
    if allow_private or os.environ.get("IRLPLS_ALLOW_PRIVATE_HOSTS") == "1":
        return u
    try:
        ipaddress.ip_address(u.hostname.split("%")[0])
        addrs = [u.hostname]
    except ValueError:
        try:
            addrs = _resolve(u.hostname)
        except (OSError, UnicodeError) as e:  # fail closed: a second lookup by urllib could answer differently
            raise UnsafeURL(f"cannot resolve {u.hostname[:100]!r}; refused") from e
        if not addrs:
            raise UnsafeURL(f"{u.hostname[:100]!r} resolves to no address; refused")
    for a in addrs:
        if not is_public_ip(a):
            raise UnsafeURL(f"{u.hostname} resolves to a non-public address ({a}); refused")
    return u


SENSITIVE_HEADERS = ("authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "x-auth-token",
                     "x-access-token", "apikey", "x-goog-api-key")


def _origin(url):
    u = urllib.parse.urlsplit(url)
    try:
        port = u.port or {"https": 443, "http": 80}.get(u.scheme)
    except ValueError:
        port = None
    return (u.scheme.lower(), (u.hostname or "").lower(), port)


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, allow_private=False):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and urllib.parse.urlsplit(new).scheme != "https":
            raise UnsafeURL(f"redirect from https to {new[:100]!r} refused")
        check_url(new, self.allow_private)
        out = super().redirect_request(req, fp, code, msg, headers, new)
        if out is not None and _origin(new) != _origin(req.full_url):
            # credentials are for the origin they were sent to, never for wherever it redirects
            for k in list(out.headers):
                if k.lower() in SENSITIVE_HEADERS or "key" in k.lower() or "token" in k.lower() or "secret" in k.lower():
                    del out.headers[k]
            for k in list(out.unredirected_hdrs):
                if k.lower() in SENSITIVE_HEADERS:
                    del out.unredirected_hdrs[k]
        return out


_timeout_cap = contextvars.ContextVar("irlpls_timeout_cap", default=None)


@contextlib.contextmanager
def timeout_cap(seconds):
    """Inside the block, no request waits longer than `seconds` for the provider, whatever timeout the caller asked
    for (tools/healthcheck.py keeps every read short this way)."""
    t = _timeout_cap.set(seconds)
    try:
        yield
    finally:
        _timeout_cap.reset(t)


def _open(req, timeout, allow_private=False):
    cap = _timeout_cap.get()
    if cap is not None:
        timeout = min(timeout, cap) if timeout else cap
    return urllib.request.build_opener(_Redirects(allow_private)).open(req, timeout=timeout)


def _read_capped(r, limit):
    declared = r.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise TooLarge(f"response is {declared} bytes; the limit is {limit}")
    data = r.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"response is larger than {limit} bytes")
    return data


def _gunzip_capped(data, limit):
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise TooLarge(f"decompressed response is larger than {limit} bytes")
    return out


def _decode(data, headers, limit):
    return _gunzip_capped(data, limit) if headers.get("Content-Encoding") == "gzip" else data


class Response(tuple):
    """What request(..., return_headers=True) returns: (status, headers with lower-case names, body)."""
    __slots__ = ()

    def __new__(cls, status, headers, body):
        return tuple.__new__(cls, (status, headers, body))

    def __getnewargs__(self):   # copy/deepcopy (the provider cache) and pickle
        return tuple(self)

    status = property(lambda self: self[0])
    headers = property(lambda self: self[1])
    body = property(lambda self: self[2])


def request(method, url, params=None, headers=None, body=None, timeout=30, raw=False, allow_private=False, max_bytes=MAX_BYTES,
            provider=None, form=None, return_headers=False, allow_http=True):
    """JSON in, JSON out; raw=True returns the response text as is (for tab-separated and other non-JSON services).
    form={...} sends an application/x-www-form-urlencoded body instead of JSON (OAuth token endpoints); not with body.
    provider (default: the one set by ratelimit.using) paces, retries and caches the call under that provider's limit.
    return_headers=True returns a Response (status, headers with lower-case names, body) instead of the body alone, under
    the same URL checks, redirect rules, size caps, pacing and cache rules (a cached GET keeps its headers).
    allow_http=False refuses plain http (https only)."""
    if body is not None and form is not None:
        raise ValueError("send either body (JSON) or form, not both")
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    if allow_http:
        check_url(url, allow_private)
    else:
        check_url(url, allow_private, allow_http=False)
    h = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode({k: v for k, v in form.items() if v is not None}).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")

    def send():
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with _open(req, timeout, allow_private) as r:
                text = _decode(_read_capped(r, max_bytes), r.headers, max_bytes).decode()
                status, rh = getattr(r, "status", None), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            try:
                err = _decode(_read_capped(e, min(max_bytes, 64 * 1024)), e.headers, 64 * 1024).decode(errors="replace")
            except (TooLarge, OSError, zlib.error):
                err = "(error body too large or unreadable)"
            raise HTTPError(e.code, url, err, headers=dict(e.headers.items()) if e.headers else None) from None
        out = text if raw else (json.loads(text) if text.strip() else None)
        return Response(status, rh, out) if return_headers else out

    from . import ratelimit
    provider = provider or ratelimit.current()
    if not provider:
        return send()
    variant = ("headers+raw" if raw else "headers") if return_headers else raw
    out = ratelimit.call(provider, method, url, send, headers=h, raw=variant)
    return Response(*out) if return_headers and not isinstance(out, Response) else out


def get(url, **kw):
    return request("GET", url, **kw)


def head(url, headers=None, timeout=15, allow_private=False, provider=None):
    """Response headers of a HEAD request, keys lower-cased (e.g. an image's last-modified as its capture time), under the
    same URL checks, redirect rules and provider pacing as request(). Never cached."""
    check_url(url, allow_private)
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})

    def send():
        req = urllib.request.Request(url, headers=h, method="HEAD")
        try:
            with _open(req, timeout, allow_private) as r:
                return {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            raise HTTPError(e.code, url, "", headers=dict(e.headers.items()) if e.headers else None) from None

    from . import ratelimit
    provider = provider or ratelimit.current()
    return send() if not provider else ratelimit.call(provider, "HEAD", url, send, headers=h)


def post(url, body=None, **kw):
    return request("POST", url, body=body, **kw)
