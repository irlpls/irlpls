"""irlpls protocol client: talk to any provider that speaks the wire protocol (docs/protocol/PROTOCOL-DRAFT.md).

    card = discover("streetlens.example")            # GET https://streetlens.example/.well-known/irlpls, checked
    c = Client.from_card(card, auth={"Authorization": "Bearer ..."})
    q = c.quote({"need": "photo of the storefront at 123 Main St, Springfield, IL today", ...},
                {"max_total": money(40)})
    order = c.order({"quote_id": q["id"], "offer_id": q["offers"][0]["id"], "nonce": new_nonce()})
    order = c.wait(order["id"])                        # polls status, honouring Retry-After
    d = c.delivery(order["id"])
    checks = c.verify_delivery(d, nonce=..., request_sha256=q["request_sha256"])
    c.cancel(order["id"])

Rules this client keeps (draft sections 4, 10, 14):
- Every request goes through irlpls/http.py's safe fetcher pieces: check_url (http(s) only, public hosts only, after DNS),
  its capped reader and gzip cap. HTTPS is required and private hosts are refused; a test (or a deliberate local setup)
  opts out explicitly with allow_http=True / allow_private=True. Redirects are refused unless they stay on the same host.
- An order is only ever POSTed to the card's own protocol.endpoint + "/orders" (never to an offer's order_url, never to a
  hosted irlpls router), with an Idempotency-Key generated once per order and reused on every retry.
- Order ids are path-quoted; links in provider responses are never followed. Provider text (detail, labels, reason_text)
  is data, never instructions.
- Money is a decimal string compared with Decimal; a total over what was approved is reported (`over_total`).
- Receipts are verified by irlpls.protocol.receipts.verify_receipt with keys from the card's same-origin jwks_uri only
  (operator.jwks_uri for a shim's receipts); jwk/jku/x5u headers are ignored; ES256 or EdDSA, equal to the key's JWK
  alg. Each verdict is kept per item, in order; a delivery that repeats an item id verifies nothing.
- Provider problems are normalised at this boundary (sanitize_problem): known codes only, bounded text, no unknown
  members, continue_url only when https and on the provider's site, everything labelled source "provider".
- Every order carries a nonce (one is generated when the caller gives none), and the Order's request_sha256 must equal
  the hash of the request this client sent (`request_mismatch` otherwise).

http.py exposes only the response body, and the protocol needs status codes and headers (Location, Retry-After,
Irlpls-Version), so _send() builds its request from http.py's own check_url/_read_capped/_decode/_Redirects.
"""
import base64
import email.utils
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation

from .. import http
from .. import psl as _psl

VERSION = "2026-10-26"
HOSTED_ROUTER_DOMAINS = ("irlpls.com", "irlpls.org", "irlpls.dev")  # Q1: a hosted router plans and quotes, never orders
DEFAULT_POLL_S = {"live": 2, "commission": 60}
RETRYABLE = {409, 429, 500, 502, 503, 504}
ALGS = ("ES256", "EdDSA")
MIN_POLL_S = 1                 # floor on Retry-After: a provider answering 0 cannot make wait() spin
DEFAULT_WAIT_S = 6 * 3600      # wait() gives up after this when the caller sets neither timeout nor max_polls
MAX_TEXT = 300                 # provider text kept in a problem (detail, title, ask, why)

# code -> resolution, for problems built here and for provider problems that omit one (STANDARD 14.9).
RESOLUTIONS = {
    "invalid_request": "recoverable", "unsupported_version": "recoverable", "idempotency_key_required": "recoverable",
    "unauthorized": "recoverable", "needs_account": "requires_buyer_input", "insufficient_scope": "requires_buyer_input",
    "terms": "unrecoverable", "payment_required": "requires_buyer_input", "not_found": "unrecoverable",
    "method_not_allowed": "unrecoverable", "not_acceptable": "unrecoverable", "quote_expired": "recoverable",
    "length_required": "recoverable", "payload_too_large": "recoverable", "unsupported_media_type": "recoverable",
    "idempotency_key_reused": "unrecoverable", "quote_required": "recoverable", "needs_input": "requires_buyer_input",
    "no_match": "unrecoverable", "outside_coverage": "unrecoverable", "too_slow": "recoverable",
    "over_max_total": "requires_buyer_review", "rights_unavailable": "requires_buyer_review",
    "proof_unavailable": "requires_buyer_review", "person_targeting_refused": "unrecoverable",
    "policy": "requires_buyer_review", "no_offer": "requires_buyer_review", "request_in_progress": "retry_later",
    "not_cancellable": "unrecoverable", "rate_limited": "retry_later", "provider_error": "retry_later",
    "upstream_error": "retry_later", "provider_unavailable": "retry_later",
    # client-local codes: produced by a client library or an in-process router, never sent by a provider
    "no_protocol": "unrecoverable", "price_changed": "requires_buyer_review", "unsafe_endpoint": "unrecoverable",
    "media_mismatch": "unrecoverable", "request_mismatch": "unrecoverable",
}
PROVIDER_CODES = {c for c in RESOLUTIONS if c not in ("no_protocol", "price_changed", "unsafe_endpoint", "media_mismatch",
                                                      "request_mismatch", "policy", "no_offer", "upstream_error")}
PROBLEM_MEMBERS = ("type", "title", "status", "detail", "instance", "code", "resolution", "continue_url", "errors",
                   "questions", "violations", "ruled_out", "supported_versions", "quote_id", "order_id", "upstream")
# Request members a provider question may ask about; anything else (a card number, a password) is dropped.
QUESTION_FIELDS = ("place", "lat", "lon", "country_code", "within_minutes", "quantity", "need", "modalities", "mode",
                   "terms", "why", "deliver_by", "max_total")


def _resolution(code, status):
    if code in RESOLUTIONS:
        return RESOLUTIONS[code]
    if str(code).startswith("policy:"):
        return "requires_buyer_review"
    return "retry_later" if status in (429,) or (isinstance(status, int) and status >= 500) else "unrecoverable"


def _cut(s, n=MAX_TEXT):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def sanitize_problem(problem, status=None, site=None):
    """A provider's problem as data the agent can be shown: the code only when it is a code a provider may send (else
    provider_error, with the original, bounded, under x-provider_code); detail and title cut to 300 characters; only
    the problem members STANDARD 14.9 defines; continue_url only when https and on `site`'s registrable domain (when
    given); questions only for Request fields, each labelled source "provider"; and `source: "provider"` on the whole.
    Idempotent."""
    p = problem if isinstance(problem, dict) else {}
    st = p.get("status") if isinstance(p.get("status"), int) and 400 <= p.get("status") <= 599 else (status or 502)
    code = p.get("code")
    out = {"source": "provider"}
    if isinstance(code, str) and code in PROVIDER_CODES:
        out["code"] = code
    else:
        out["code"] = "provider_error"
        if code is not None:
            out["x-provider_code"] = _cut(code, 60)
    out["status"] = st
    for k in ("title", "detail", "instance"):
        if isinstance(p.get(k), str):
            out[k] = _cut(p[k])
    res = p.get("resolution")
    out["resolution"] = res if res in set(RESOLUTIONS.values()) else _resolution(out["code"], st)
    cu = p.get("continue_url")
    if isinstance(cu, str) and cu.startswith("https://") and len(cu) <= 2000 and not any(c.isspace() for c in cu) \
            and (site is None or same_site(urllib.parse.urlsplit(cu).hostname, site)):
        out["continue_url"] = cu
    for k in ("quote_id", "order_id"):
        if isinstance(p.get(k), str):
            out[k] = _cut(p[k], 128)
    if isinstance(p.get("supported_versions"), list):
        out["supported_versions"] = [_cut(v, 20) for v in p["supported_versions"][:10] if isinstance(v, str)]
    if isinstance(p.get("violations"), list):
        out["violations"] = [_cut(v, 120) for v in p["violations"][:20] if isinstance(v, str)]
    if isinstance(p.get("errors"), list):
        out["errors"] = [{k: _cut(e[k], 200) for k in ("path", "code", "detail") if isinstance(e.get(k), str)}
                         for e in p["errors"][:20] if isinstance(e, dict) and isinstance(e.get("path"), str)]
    if isinstance(p.get("questions"), list):
        qs = []
        for q in p["questions"][:10]:
            if isinstance(q, dict) and q.get("field") in QUESTION_FIELDS and isinstance(q.get("ask"), str):
                qs.append({"field": q["field"], "ask": _cut(q["ask"], 200), "blocking": q.get("blocking") is not False,
                           "source": "provider", **({"why": _cut(q["why"], 200)} if isinstance(q.get("why"), str) else {})})
        out["questions"] = qs
    if out["code"] == p.get("code") and isinstance(p.get("type"), str) and p["type"].startswith("https://irlpls.com/"):
        out["type"] = _cut(p["type"], 120)
    else:
        out["type"] = f"https://irlpls.com/{VERSION}/problems/" + out["code"]
    out.setdefault("title", out["code"].replace("_", " "))
    return out


class ProtocolError(RuntimeError):
    """A non-2xx answer (an RFC 9457 problem), or a client-side refusal. `problem` always has code/status/title."""

    def __init__(self, problem, status=None, retry_after=None):
        self.problem = dict(problem or {})
        self.problem.setdefault("code", "provider_error")
        self.problem.setdefault("status", status or 502)
        self.problem.setdefault("title", self.problem["code"].replace("_", " "))
        self.problem.setdefault("resolution", _resolution(self.problem["code"], self.problem["status"]))
        self.problem.setdefault("type", f"https://irlpls.com/{VERSION}/problems/"
                                + ("policy" if self.problem["code"].startswith("policy:") else self.problem["code"]))
        self.status, self.retry_after, self.code = self.problem["status"], retry_after, self.problem["code"]
        super().__init__(f"{str(self.code)[:60]} ({self.status})" + (f": {str(self.problem.get('detail'))[:200]}" if self.problem.get("detail") else ""))


class UnsafeEndpoint(ProtocolError):
    """Refused before any request: the endpoint, a key URL or an order URL breaks the discovery rules (draft section 3)."""

    def __init__(self, detail):
        super().__init__({"code": "unsafe_endpoint", "status": 400, "title": "Unsafe endpoint", "detail": detail,
                          "resolution": "unrecoverable"})


# -- small helpers ----------------------------------------------------------------------------------------------------

from .receipts import jcs  # noqa: E402 - RFC 8785, stdlib only; one implementation for every module


def request_sha256(request):
    return hashlib.sha256(jcs(request)).hexdigest()


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_key():
    """A fresh Idempotency-Key: a UUIDv4 (122 random bits) is what the draft suggests."""
    return str(uuid.uuid4())


def new_nonce():
    """An order nonce: 128 random bits, base64url (22 characters)."""
    return b64u(secrets.token_bytes(16))


def derive_key(key, card_id):
    """Draft section 10: a router derives a distinct key per provider, base64url(SHA-256(key || "|" || card_id))."""
    return b64u(hashlib.sha256(f"{key}|{card_id}".encode()).digest())


def money(amount, currency="USD"):
    """A protocol money object from a number or string: {"amount": "12.5", "currency": "USD"}. Refuses NaN, inf, < 0."""
    try:
        d = Decimal(str(amount))
    except InvalidOperation:
        raise ValueError(f"not an amount: {amount!r}") from None
    if not d.is_finite() or d < 0 or isinstance(amount, bool):
        raise ValueError(f"not an amount: {amount!r}")
    d = d.quantize(Decimal("0.000001")).normalize()
    s = format(d, "f")
    return {"amount": "0" if Decimal(s) == 0 else s, "currency": currency}


def amount(m):
    """Decimal from a protocol money object, or None when it is missing or malformed (which callers treat as a violation)."""
    import re
    if not isinstance(m, dict) or not isinstance(m.get("amount"), str):
        return None
    if not re.fullmatch(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?", m["amount"]) or not isinstance(m.get("currency"), str):
        return None
    return Decimal(m["amount"])


def registrable(host):
    """Registrable domain from the vendored Public Suffix List (irlpls.psl): "example.co.uk" for "api.example.co.uk",
    "alice.github.io" for "alice.github.io". IP addresses and single labels come back as they are; a bare public
    suffix comes back as None."""
    host = (host or "").lower().rstrip(".")
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    return host if host and "." not in host else _psl.registrable_domain(host)


def same_site(a, b):
    """Host names on one registrable domain (PSL); equal hosts always are; empty or bare-suffix hosts never are."""
    return _psl.same_site(a, b)


def origin(url):
    """RFC 6454 ASCII serialisation: scheme://host[:port], lower-case, the scheme's default port omitted."""
    u = urllib.parse.urlsplit(url or "")
    try:
        p = u.port
    except ValueError:
        p = None
    port = f":{p}" if p and p != {"https": 443, "http": 80}.get(u.scheme.lower()) else ""
    return f"{u.scheme.lower()}://{(u.hostname or '').lower()}{port}"


def receipt_signer(card):
    """(iss, jwks_uri) receipts from this card must verify against: the provider's origin and its jwks_uri, or, when a
    shim runs the endpoint (protocol.operator), the operator's origin and operator.jwks_uri (STANDARD 14.7)."""
    p = protocol_block(card) or {}
    op = p.get("operator") if isinstance(p.get("operator"), dict) else None
    if op:
        src = op.get("url") or op.get("jwks_uri")   # the schema requires url with jwks_uri; a local shim may omit it
        return (origin(src) if src else None), op.get("jwks_uri")
    return origin((card or {}).get("provider_url") or ""), p.get("jwks_uri")


def protocol_block(card):
    """The card's `protocol` block if it is usable (versions and an endpoint), else None."""
    p = (card or {}).get("protocol")
    if isinstance(p, dict) and isinstance(p.get("endpoint"), str) and p.get("versions"):
        return p
    return None


def check_card(card, fetched_from=None, allow_http=False):
    """Discovery rules (draft section 3): endpoint on the provider's registrable domain, jwks_uri on its origin, and,
    when the card was fetched, the card served from provider_url's own host or its "www." twin (psl.same_owner). Raises
    UnsafeEndpoint."""
    p = protocol_block(card)
    if not p:
        raise UnsafeEndpoint(f"card {card.get('id')!r} has no protocol block")
    schemes = ("https", "http") if allow_http else ("https",)
    ep, purl = p["endpoint"], card.get("provider_url") or ""
    for name, u in (("endpoint", ep), ("provider_url", purl)) + ((("jwks_uri", p["jwks_uri"]),) if p.get("jwks_uri") else ()):
        if urllib.parse.urlsplit(u).scheme not in schemes:
            raise UnsafeEndpoint(f"{name} must be https: {u[:100]!r}")
    if ep.endswith("/"):
        raise UnsafeEndpoint("endpoint must not end with a slash")
    if not same_site(urllib.parse.urlsplit(ep).hostname, urllib.parse.urlsplit(purl).hostname):
        raise UnsafeEndpoint(f"endpoint {ep[:100]!r} is not on the provider's domain ({purl[:100]!r})")
    if p.get("jwks_uri") and origin(p["jwks_uri"]) != origin(purl):
        raise UnsafeEndpoint("jwks_uri must be on the same origin as provider_url")
    op = p.get("operator") if isinstance(p.get("operator"), dict) else {}
    if op.get("jwks_uri"):
        if urllib.parse.urlsplit(op["jwks_uri"]).scheme not in schemes \
                or origin(op["jwks_uri"]) != origin(op.get("url") or op["jwks_uri"]):
            raise UnsafeEndpoint("operator.jwks_uri must be https and on the same origin as operator.url")
    if fetched_from and not _psl.same_owner(urllib.parse.urlsplit(fetched_from).hostname, urllib.parse.urlsplit(purl).hostname):
        raise UnsafeEndpoint(f"card for {purl[:100]!r} was served from another domain ({fetched_from[:100]!r})")
    if VERSION not in p.get("versions", []):
        raise UnsafeEndpoint(f"provider does not speak {VERSION} (speaks {p.get('versions')})")
    return p


def _retry_after(headers):
    v = (headers or {}).get("Retry-After") if headers is not None else None
    if not v:
        return None
    v = v.strip()
    if v.isdigit():
        return int(v)
    try:
        return max(0, int(email.utils.parsedate_to_datetime(v).timestamp() - time.time()))
    except (TypeError, ValueError):
        return None


class _SameHostRedirects(http._Redirects):
    """http.py's redirect checks (http(s) only, public host after DNS, never https -> http), plus: https required
    unless allow_http, and never to another host."""

    def __init__(self, allow_private=False, allow_http=False):
        super().__init__(allow_private)
        self.allow_http = allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.parse.urljoin(req.full_url, newurl)
        http.check_url(new, allow_private=self.allow_private, allow_http=self.allow_http)
        if urllib.parse.urlsplit(new).netloc.lower() != urllib.parse.urlsplit(req.full_url).netloc.lower():
            raise http.UnsafeURL(f"cross-host redirect to {new[:100]!r} refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _send(method, url, body=None, headers=None, timeout=30, allow_private=False, allow_http=False,
          max_bytes=http.MAX_BYTES, raw=False):
    """(status, headers, parsed JSON | bytes). Uses http.py's URL check, capped reader and gzip cap."""
    try:
        http.check_url(url, allow_private=allow_private, allow_http=allow_http)
    except http.UnsafeURL as e:
        raise UnsafeEndpoint(str(e)) from None
    h = {"User-Agent": http.USER_AGENT, "Accept-Encoding": "gzip",
         "Accept": "*/*" if raw else "application/json, application/problem+json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = jcs(body)  # canonical bytes: a retry sends exactly the same body
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = urllib.request.build_opener(_SameHostRedirects(allow_private, allow_http))
    try:
        with opener.open(req, timeout=timeout) as r:  # noqa: S310 - URL checked above
            status, hdrs = r.status, r.headers
            payload = http._decode(http._read_capped(r, max_bytes), r.headers, max_bytes)
    except http.UnsafeURL as e:  # a redirect refused by the checks
        raise UnsafeEndpoint(str(e)) from None
    except urllib.error.HTTPError as e:
        status, hdrs = e.code, e.headers
        try:
            payload = http._decode(http._read_capped(e, 64 * 1024), e.headers, 64 * 1024)
        except (http.TooLarge, OSError):
            payload = b""
    if raw and 200 <= status < 300:
        return status, hdrs, payload
    text = payload.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except ValueError:
        parsed = {"detail": text[:300]} if status >= 400 else None
        if status < 400:
            raise ProtocolError({"code": "provider_error", "detail": "response is not JSON"}, 502)
    return status, hdrs, parsed


# -- discovery --------------------------------------------------------------------------------------------------------

def discover(domain, allow_private=False, allow_http=False, timeout=30):
    """GET https://<domain>/.well-known/irlpls and check it (draft section 3). Returns the card, or a router's
    {"cards": [...]} as served. `domain` may be a bare host or an origin URL."""
    if not isinstance(domain, str) or not domain.strip():
        raise UnsafeEndpoint("discover needs a provider domain")
    base = domain.strip() if "://" in domain else f"https://{domain.strip()}"
    u = urllib.parse.urlsplit(base)
    if u.username is not None or u.password is not None or not u.hostname:
        raise UnsafeEndpoint(f"not a provider domain: {domain[:100]!r}")
    url = f"{u.scheme}://{u.netloc}/.well-known/irlpls"
    # The card fetch is a fetch like any other: _send runs http.check_url (http(s) only, https unless allow_http,
    # every resolved address public unless allow_private, fail closed) and every redirect through the same checks.
    status, hdrs, doc = _send("GET", url, headers={"Irlpls-Version": VERSION}, timeout=timeout,
                              allow_private=allow_private, allow_http=allow_http)
    if status != 200 or not isinstance(doc, dict):
        raise ProtocolError(doc if isinstance(doc, dict) and doc.get("code") else {"code": "not_found", "detail": f"no card at {url}"}, status)
    if "cards" in doc:
        return doc
    if protocol_block(doc):
        check_card(doc, fetched_from=url, allow_http=allow_http)
    return doc


# -- the client -------------------------------------------------------------------------------------------------------

class Client:
    """One provider's protocol endpoint. Build it from a checked card (Client.from_card) so keys and the order URL
    come from that card and nowhere else."""

    def __init__(self, endpoint, card=None, auth=None, allow_private=False, allow_http=False, timeout=30,
                 sleep=time.sleep, max_retries=3, max_retry_after=900, version=VERSION):
        self.endpoint, self.card = endpoint.rstrip("/"), card or {}
        self.auth = dict(auth or {})
        self.allow_private, self.allow_http, self.timeout = allow_private, allow_http, timeout
        self.sleep, self.max_retries, self.max_retry_after, self.version = sleep, max_retries, max_retry_after, version
        self.last_idempotency_key, self.last_retry_after, self.warnings = None, None, []
        self._jwks = None

    @classmethod
    def from_card(cls, card, fetched_from=None, **kw):
        p = check_card(card, fetched_from=fetched_from, allow_http=kw.get("allow_http", False))
        return cls(p["endpoint"], card=card, **kw)

    @property
    def protocol(self):
        return protocol_block(self.card) or {}

    @property
    def card_id(self):
        return self.card.get("id")

    # -- transport --
    def _call(self, method, path, body=None, idempotency_key=None, ok=(200,), retry=False):
        url = self.endpoint + path
        h = {"Irlpls-Version": self.version, **self.auth}
        if idempotency_key is not None:
            h["Idempotency-Key"] = idempotency_key
        attempt = 0
        while True:
            try:
                status, hdrs, doc = _send(method, url, body, h, self.timeout, self.allow_private, self.allow_http)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if retry and attempt < self.max_retries:
                    attempt += 1
                    self.sleep(min(2 ** attempt, self.max_retry_after))
                    continue
                raise ProtocolError({"code": "provider_unavailable", "detail": f"{type(e).__name__}: {e}"[:300],
                                     "resolution": "retry_later"}, 503) from None
            ra = _retry_after(hdrs)
            self.last_retry_after = ra
            if status in ok:
                echoed = hdrs.get("Irlpls-Version") if hdrs is not None else None
                if echoed and echoed != self.version:
                    self.warnings.append(f"provider answered with Irlpls-Version {echoed}, asked for {self.version}")
                return status, hdrs, doc
            problem = doc if isinstance(doc, dict) else {}
            if retry and status in RETRYABLE and attempt < self.max_retries \
                    and (status != 409 or problem.get("code") == "request_in_progress"):
                attempt += 1
                self.sleep(min(ra if ra is not None else 2 ** attempt, self.max_retry_after))
                continue
            if not problem.get("code"):
                problem = {**problem, "code": {404: "not_found", 401: "unauthorized", 429: "rate_limited",
                                               503: "provider_unavailable"}.get(status, "provider_error")}
            raise ProtocolError(sanitize_problem(problem, status, self._site()), status, ra)

    def _site(self):
        return urllib.parse.urlsplit(self.card.get("provider_url") or self.endpoint).hostname

    def _id(self, x):
        return urllib.parse.quote(str(x), safe="")

    # -- operations --
    def quote(self, request, constraints=None, idempotency_key=None):
        """POST /quotes. `request` is an irlpls Request; `policy` (and anything else private) is stripped first."""
        req = {k: v for k, v in request.items() if k != "policy"}
        body = {"request": req}
        if constraints:
            body["constraints"] = constraints
        _, _, q = self._call("POST", "/quotes", body, idempotency_key, ok=(200, 201), retry=True)
        if not isinstance(q, dict) or not isinstance(q.get("offers"), list):
            raise ProtocolError({"code": "provider_error", "detail": "quote has no offers"}, 502)
        want = request_sha256(req)
        if q.get("request_sha256") and q["request_sha256"] != want:
            self.warnings.append("quote request_sha256 does not match the request as sent")
        for o in q["offers"]:
            if o.get("order_url") and o["order_url"] != self.endpoint + "/orders":
                self.warnings.append(f"offer {o.get('id')} names order_url {str(o['order_url'])[:100]!r}; ignored, "
                                     f"orders go to {self.endpoint}/orders")
        return q

    def get_quote(self, quote_id):
        return self._call("GET", f"/quotes/{self._id(quote_id)}", retry=True)[2]

    def order(self, order_request, idempotency_key=None):
        """POST /orders with an Idempotency-Key (generated here when not given) reused on every retry, so a lost
        response, a 409 request_in_progress, a 429 or a 5xx never orders twice. A nonce is added when the request has
        none (STANDARD 14.5: required). Returns the Order. A direct order's request_sha256 is checked here; for an
        accepted offer, compare Order.request_sha256 with the quote's (the broker does)."""
        host = (urllib.parse.urlsplit(self.endpoint).hostname or "").lower()
        if registrable(host) in HOSTED_ROUTER_DOMAINS:
            raise UnsafeEndpoint("orders go to the provider's own endpoint, never through a hosted irlpls router")
        key = http_key(idempotency_key or new_key())
        self.last_idempotency_key = key
        if "nonce" not in order_request:
            order_request = dict(order_request, nonce=new_nonce())
        _, hdrs, order = self._call("POST", "/orders", order_request, key, ok=(200, 201, 202), retry=True)
        order = self._check_order(order)
        if isinstance(order_request.get("request"), dict) and order.get("request_sha256") != request_sha256(order_request["request"]):
            self.warnings.append("request_mismatch: the Order's request_sha256 is not the hash of the request sent")
        return order

    def status(self, order_id):
        return self._check_order(self._call("GET", f"/orders/{self._id(order_id)}", retry=True)[2])

    def delivery(self, order_id):
        d = self._call("GET", f"/orders/{self._id(order_id)}/delivery", retry=True)[2]
        if not isinstance(d, dict) or not isinstance(d.get("items"), list):
            raise ProtocolError({"code": "provider_error", "detail": "delivery has no items"}, 502)
        return d

    def cancel(self, order_id, idempotency_key=None):
        key = http_key(idempotency_key or new_key())
        self.last_idempotency_key = key
        return self._check_order(self._call("POST", f"/orders/{self._id(order_id)}/cancel", {}, key, retry=True)[2])

    def wait(self, order_id, timeout=None, max_polls=None, route=None, max_interval=None):
        """Poll status until delivered or failed, never faster than Retry-After (at least MIN_POLL_S; default 60 s for
        commissions, 2 s for live), backing off 1.5x when the provider gives no Retry-After. With neither timeout nor
        max_polls, gives up after DEFAULT_WAIT_S. Returns the last Order (maybe still in flight)."""
        route = route or "commission"
        if timeout is None and max_polls is None:
            timeout = DEFAULT_WAIT_S
        interval = DEFAULT_POLL_S.get(route, 60)
        max_interval = max_interval or self.max_retry_after
        start, polls = time.monotonic(), 0
        order = self.status(order_id)
        while order["state"] in ("submitted", "in_flight"):
            if max_polls is not None and polls >= max_polls:
                break
            wait_s = max(MIN_POLL_S, self.last_retry_after if self.last_retry_after is not None else interval)
            if timeout is not None and time.monotonic() - start + wait_s > timeout:
                break
            self.sleep(min(wait_s, max_interval))
            if self.last_retry_after is None:
                interval = min(interval * 1.5, max_interval)
            order = self.status(order_id)
            polls += 1
        return order

    def _check_order(self, order):
        if not isinstance(order, dict) or order.get("state") not in ("submitted", "in_flight", "delivered", "failed") \
                or not order.get("id"):
            raise ProtocolError({"code": "provider_error", "detail": "not an Order"}, 502)
        return order

    # -- media and receipts --
    def fetch_media(self, item, max_bytes=http.MAX_BYTES):
        """Download an item's media (HTTPS, public host, same-host redirects only) and check its sha256; a mismatch
        is a failed item, never a warning (draft section 14)."""
        if not item.get("url") or not item.get("sha256"):
            raise ProtocolError({"code": "invalid_request", "detail": "item has no url and sha256"}, 400)
        _, _, data = _send("GET", item["url"], timeout=self.timeout, allow_private=self.allow_private,
                           allow_http=self.allow_http, max_bytes=max_bytes, raw=True)
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ProtocolError({"code": "media_mismatch", "detail": f"item {item.get('id')}: sha256 does not match"}, 502)
        return data

    def jwks(self):
        """The key set receipts from this endpoint verify against (cached): the card's jwks_uri, same-origin with
        provider_url, or for a shim (protocol.operator) operator.jwks_uri, same-origin with operator.url."""
        if self._jwks is None:
            iss, uri = receipt_signer(self.card)
            if not uri:
                return None
            if origin(uri) != iss:
                raise UnsafeEndpoint("the receipt key set must be on the signer's own origin")
            status, _, doc = _send("GET", uri, headers={"Irlpls-Version": self.version}, timeout=self.timeout,
                                   allow_private=self.allow_private, allow_http=self.allow_http, max_bytes=256 * 1024)
            if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
                raise ProtocolError({"code": "provider_error", "detail": "jwks_uri did not return a JWK Set"}, 502)
            self._jwks = doc
        return self._jwks

    def verify_delivery(self, delivery, nonce=None, request_sha256=None):
        """One outcome per item, in the delivery's order: {item_id, verified: True | False | None (no receipt / cannot
        check), verifier, reason}. Item ids must be unique within an order: when one repeats, every item is
        verified False ("duplicate item id")."""
        iss, _ = receipt_signer(self.card)
        items = [it for it in delivery.get("items", []) if isinstance(it, dict)] if isinstance(delivery.get("items"), list) else []
        ids = [it.get("id") for it in items]
        if len(set(map(str, ids))) != len(ids):
            return [{"item_id": i, "verified": False, "verifier": None, "kid": None,
                     "reason": "duplicate item id in the delivery: malformed, nothing is verified"} for i in ids]
        try:
            keys = self.jwks()
        except ProtocolError as e:
            keys, err = None, e.code
        else:
            err = None
        out = []
        for it in items:
            if not it.get("receipt"):
                out.append({"item_id": it.get("id"), "verified": None, "verifier": None, "reason": "no receipt"})
                continue
            if keys is None:
                out.append({"item_id": it.get("id"), "verified": None, "verifier": None,
                            "reason": f"no keys to check against ({err or 'card has no jwks_uri'})"})
                continue
            out.append(verify_receipt(it, keys, iss=iss, order_id=delivery.get("order_id"), nonce=nonce,
                                      request_sha256=request_sha256))
        return out


def http_key(key):
    import re
    if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{1,255}", key):
        raise ValueError("Idempotency-Key must be 1-255 printable ASCII characters without spaces")
    return key


# -- receipts -----------------------------------------------------------------------------------------------------------

def verify_receipt(item, jwks, iss, order_id=None, nonce=None, request_sha256=None, now=None):
    """Check one item's detached-JWS receipt with irlpls.protocol.receipts.verify_receipt: the signature, the key rules
    and every binding (iss, order, item, nonce, request hash, and the item's media, phenomenon_time, lat, lon and
    precision_m). Returns {item_id, verified: True | False | None (cannot check), verifier, kid, reason}."""
    from . import receipts
    rc, item_id = item.get("receipt") or {}, item.get("id")
    kid = None
    try:
        hdr = json.loads(b64u_dec(str(rc.get("jws", "")).split(".")[0]))
        kid = hdr.get("kid") if isinstance(hdr, dict) else None
    except (ValueError, TypeError, AttributeError):
        pass
    res = {"item_id": item_id, "verified": False, "verifier": "irlpls.protocol.receipts", "kid": kid, "reason": None}
    expect = {"iss": iss, "item_id": item_id, "order_id": order_id, "nonce": nonce, "request_sha256": request_sha256}
    try:
        receipts.verify_receipt(rc, jwks, item=item, **{k: v for k, v in expect.items() if v is not None})
    except RuntimeError as e:  # the cryptography package is missing
        res["verified"], res["reason"] = None, f"cannot check: {e}"[:200]
        return res
    except Exception as e:  # noqa: BLE001 - ReceiptError and any malformed-input error mean "not valid"
        res["reason"] = str(e)[:200] or type(e).__name__
        return res
    res["verified"], res["reason"] = True, "signature verifies with the signer's key; bindings match"
    return res
