"""Adapter shim: any reference adapter behind the irlpls wire protocol (docs/protocol/PROTOCOL-DRAFT.md, Section 11).

    shim = AdapterShim("usgs-water")                     # in process: a protocol server mounts it
    reply = shim.handle("POST", "/irlpls/orders", headers, body)   # -> Reply(status, body, headers)
    serve_adapter("usgs-water", 8431)                    # or a standalone stdlib HTTP server

The six operations map to the adapter like this:
- discover: the card unchanged (still `unclaimed`) plus a `protocol` block whose `operator` names this shim.
- quote:    live adapters answer from the card (free → total "0", otherwise no price); commission adapters run
            `commission(spec, confirm=False)`, which never sends anything. An offer is firm only when that dry run carries
            a real provider quote; otherwise `firm: false` and the price is omitted, never invented.
- order:    live → `adapter.live()` at once, delivered in the 200 response. Commission → `commission(spec, confirm=True)`,
            but ONLY when the operator built the shim with `confirm_orders=True` and the adapter's credentials are set;
            otherwise a problem (`insufficient_scope` / `needs_account`) and nothing is sent.
- status:   `adapter.status()` + the adapter's own `status_map`; an unmapped word stays `in_flight`, passed through verbatim.
- deliver:  items from the adapter, each with an id; media is downloaded and hashed by the shim itself (an item whose media
            could not be hashed loses `url`, kept under `x-com.irlpls.unhashed_url`).
- cancel:   cooperative (STANDARD 14.6). When the adapter documents a cancel (`can_cancel`), `protocol.cancel` is true and
            POST /orders/{id}/cancel calls `adapter.cancel(provider order)`; the order stays in_flight until the provider's
            own status says it ended (failed / cancelled), and a provider refusal (too late) is `409 not_cancellable`.
            Otherwise `protocol.cancel` is false and a non-terminal order answers `409 not_cancellable`.
- approve:  some providers hold a placed order for a second, paid approval (Capella's cost estimate, GLOBHE's quotes).
            Status shows it under `x-com.irlpls.approval` and POST /orders/{id}/approve {approval_id, total, confirm: true}
            sends it, only on an operator with confirm_orders=True and only for that one order and that exact total:
            never automatic, never covered by a max_total or a standing mandate.
- money:    an offer total is firm only in USD. A provider price in another currency (EUR, KRW, GBP) is shown in that
            currency with `firm: false`; one that is not an ISO 4217 code (credits, USDC) goes in
            `x-com.irlpls.indicative_price`. Nothing is converted, and another currency fails every USD ceiling.

Provenance is filled from the card only (`basis: card`), stage by stage, at the card's level: claimed, evidenced, or
none when the card states nothing. The shim never raises a level. Receipts exist only when the operator gives a signing
key; they are signed by the shim operator (iss = the operator's origin, keys at protocol.operator.jwks_uri,
`signer.role = shim`), attest custody only, and say so. Requests that watch, follow or photograph a specific person are refused with `person_targeting_refused`, whatever
the buyer's policy (STANDARD 5 rule 4; the Policy never reaches a provider).

State (quotes, orders, idempotency keys) is kept in memory for the life of the shim object. Credentials are the
adapter's, read from the operator's environment as everywhere else in the reference library; the shim stores none.
Stdlib only; `cryptography` is imported only to sign receipts.
"""
import base64
import hashlib
import json
import math
import mimetypes
import re
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .. import adapters as _adapters
from .. import http
from ..adapters.base import MissingCredentials, NotSupported, quote_price
from ..need import detect_targets_person, text_fields
from .receipts import ec_public_jwk, jcs, sign_es256, value_media

VERSION = "2026-10-26"
VERSIONS = (VERSION,)
PROBLEM_BASE = f"https://irlpls.com/{VERSION}/problems/"
GUARANTEES = ("human", "application", "camera", "time", "location", "content", "corroboration")
LEVELS = ("none", "claimed", "evidenced")
STAGES = (  # (stage id, shown as, guarantees it covers) — PROTOCOL-DRAFT 5.4
    ("enrolment", "Real person", ("human",)),
    ("capture", "Captured locally", ("camera", "location")),
    ("device_attestation", "Genuine device", ("application",)),
    ("signing", "Sealed capture", ("camera", "location", "content")),
    ("timestamp", "Known timestamp", ("time",)),
    ("custody", "Tracked in transit", ()),
    ("appraisal", "Independently verified", ("corroboration",)),
    ("delivery", "Delivered to you", ()),
)
RIGHTS = ("commercial_use", "redistribution", "modification", "ai_training", "resale")
MONEY = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
ISO4217 = re.compile(r"[A-Z]{3}")
KEY = re.compile(r"^[\x21-\x7e]{1,255}$")
MEDIA_KINDS = ("photo", "image", "video", "audio", "clip")
MAX_BODY = 1024 * 1024
MAX_MEDIA = 25 * 1024 * 1024
MAX_QUANTITY = 10000
NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")

# code -> (HTTP status, resolution, title). PROTOCOL-DRAFT Section 8.
CODES = {
    "invalid_request": (400, "recoverable", "Invalid request"),
    "unsupported_version": (400, "recoverable", "Unsupported version"),
    "idempotency_key_required": (400, "recoverable", "Idempotency-Key required"),
    "unauthorized": (401, "recoverable", "Unauthorized"),
    "needs_account": (403, "requires_buyer_input", "An account is needed"),
    "insufficient_scope": (403, "requires_buyer_input", "Not allowed to order"),
    "terms": (403, "unrecoverable", "Forbidden by the provider's terms"),
    "not_found": (404, "unrecoverable", "Not found"),
    "method_not_allowed": (405, "unrecoverable", "Method not allowed"),
    "quote_expired": (410, "recoverable", "Quote expired"),
    "length_required": (411, "recoverable", "Content-Length required"),
    "payload_too_large": (413, "recoverable", "Body too large"),
    "idempotency_key_reused": (422, "unrecoverable", "Idempotency-Key reused with a different body"),
    "quote_required": (422, "recoverable", "A firm quote or a max_total is required"),
    "needs_input": (422, "requires_buyer_input", "More input needed"),
    "no_match": (422, "unrecoverable", "This provider does not capture this"),
    "outside_coverage": (422, "unrecoverable", "Outside coverage"),
    "too_slow": (422, "recoverable", "Cannot meet the deadline"),
    "over_max_total": (422, "requires_buyer_review", "Over max_total"),
    "rights_unavailable": (422, "requires_buyer_review", "Rights unavailable"),
    "proof_unavailable": (422, "requires_buyer_review", "Proof level unavailable"),
    "person_targeting_refused": (422, "unrecoverable", "Requests aimed at a specific person are refused"),
    "request_in_progress": (409, "retry_later", "Request in progress"),
    "not_cancellable": (409, "unrecoverable", "Not cancellable"),
    "rate_limited": (429, "retry_later", "Rate limited"),
    "provider_unavailable": (503, "retry_later", "Provider unavailable"),
}


# ---------------------------------------------------------------------------------------------------------------- basics

class Problem(Exception):
    """An RFC 9457 problem (problem.json). Raised by the operations; handle() turns it into a Reply."""

    def __init__(self, code, detail=None, status=None, headers=None, **extra):
        st, res, title = CODES.get(code, (status or 422, "unrecoverable", code.replace("_", " ").capitalize()))
        self.code, self.status, self.headers = code, status or st, dict(headers or {})
        self.body = {"type": PROBLEM_BASE + code, "title": title, "status": self.status, "code": code, "resolution": res}
        if detail:
            self.body["detail"] = detail
        self.body.update({k: v for k, v in extra.items() if v is not None})
        super().__init__(f"{code}: {detail or title}")


@dataclass
class Reply:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def _money(amount, currency="USD"):
    """Decimal -> protocol money ({amount: decimal string, currency}), or None. The currency is kept as given, never
    converted; one that is not an ISO 4217 code (credits, USDC) gives None here (see _price_fields)."""
    if amount is None or not isinstance(currency, str) or not ISO4217.fullmatch(currency):
        return None
    try:
        s = format(Decimal(amount).quantize(Decimal("0.000001")).normalize(), "f")
    except InvalidOperation:
        return None
    return {"amount": s, "currency": currency} if MONEY.match(s) else None


def _price_fields(amount, currency):
    """Offer fields for a provider price: {"total": money} for an ISO 4217 currency, else (credits, USDC, an unstated
    unit) {"x-com.irlpls.indicative_price": {amount, currency}}; {} without an amount."""
    if amount is None:
        return {}
    m = _money(amount, currency or "USD")
    if m:
        return {"total": m}
    try:
        a = format(Decimal(amount).normalize(), "f")
    except InvalidOperation:
        return {}
    return {"x-com.irlpls.indicative_price": {"amount": a, "currency": str(currency or "unstated")[:20]}}


def _read_money(m, path):
    """A money object from a request, as Decimal, or a Problem. Never a float, never NaN."""
    if not isinstance(m, dict) or not isinstance(m.get("amount"), str) or not MONEY.match(m["amount"]) \
            or not isinstance(m.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", m["currency"]):
        raise Problem("invalid_request", "money is {amount: decimal string, currency: ISO 4217}",
                      errors=[{"path": path, "detail": "not a valid money object"}])
    return Decimal(m["amount"]), m["currency"]


def _hdr(headers, name):
    if not headers:
        return None
    if hasattr(headers, "get") and not isinstance(headers, dict):
        return headers.get(name)
    low = name.lower()
    return next((v for k, v in headers.items() if k.lower() == low), None)


def _origin(url):
    """RFC 6454 ASCII origin: scheme://host[:port], lower-case, the default port omitted."""
    u = urllib.parse.urlsplit(url)
    port = f":{u.port}" if u.port and u.port != {"https": 443, "http": 80}.get(u.scheme) else ""
    return f"{u.scheme}://{(u.hostname or '').lower()}{port}"


def _https(url):
    return url if isinstance(url, str) and url.startswith("https://") and not re.search(r"\s", url) else None


def fetch_media(url):
    """Download media the way irlpls/http.py fetches anything (public https hosts, checked redirects, a size cap).
    Returns (bytes, media type or None)."""
    http.check_url(url, allow_http=False)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": http.USER_AGENT})
    with http._open(req, 30) as r:
        data = http._read_capped(r, MAX_MEDIA)
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip() or None
    return data, ctype


def covers(areas, code):
    """Whether card coverage areas (ISO 3166-1 / 3166-2 codes, or "*") include a request's country_code. A country
    matches its own sub-national areas (US matches US-GA, KR matches KR-11); a sub-national code matches its country
    and itself (US-GA matches US and US-GA, not US-CA)."""
    c = str(code).strip().upper()
    for a in areas:
        a = str(a).strip().upper()
        if a == "*" or a == c or a.split("-")[0] == c or c.split("-")[0] == a:
            return True
    return False


# ------------------------------------------------------------------------------------------------- per-provider hooks

def _spec_generic(req, ext_id):
    return {k: v for k, v in {"address": req.get("place"), "lat": req.get("lat"), "lon": req.get("lon"), "notes": req.get("need"),
                              "quantity": req.get("quantity"), "external_id": ext_id}.items() if v is not None}


def _spec_proxypics(req, ext_id, deliver_by=None):
    s = {"address": req.get("place"), "lat": req.get("lat"), "lon": req.get("lon"), "notes": req.get("need"),
         "expires_at": deliver_by, "external_id": ext_id}   # external_id is ProxyPics's own idempotency hook
    return {k: v for k, v in s.items() if v is not None}


def _spec_rentahuman(req, ext_id, deliver_by=None):
    s = {"title": (req.get("need") or "Capture this in person")[:120], "description": req.get("need"),
         "deadline": deliver_by, "spots": req.get("quantity")}
    return {k: v for k, v in s.items() if v is not None}


SPEC_BUILDERS = {"proxypics": _spec_proxypics, "rentahuman": _spec_rentahuman}


def _cents(v):
    return Decimal(v) / 100 if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


# What the provider says it will charge, read from its order response (documented fields only).
ORDER_TOTAL = {"proxypics": lambda r: _cents(r.get("cost")) if isinstance(r, dict) else None}


# ------------------------------------------------------------------------------------------------------------- the shim

class AdapterShim:
    """The protocol in front of one reference adapter. Thread-safe; state lives in memory.

    card_id        Capture Card id (adapter looked up in irlpls.adapters unless `adapter` is given).
    base_url       The protocol endpoint this shim answers at (no trailing slash). Links and offers point here.
    operator       {"name": ..., "url": ...}: who runs the shim (disclosed in protocol.operator and receipts).
    signing_key    An EC P-256 private key (cryptography object, or PEM bytes/str). Without one, no receipts.
    kid            Key id for the signing key (published at {origin}/.well-known/irlpls-keys.json).
    confirm_orders Commission orders are refused unless this is True (and the adapter's credentials are set).
    media_fetcher  url -> (bytes, media_type); defaults to fetch_media. Tests pass a fake.
    clock          () -> aware datetime.
    """

    def __init__(self, card_id, adapter=None, card=None, base_url="https://shim.irlpls.invalid/irlpls", operator=None,
                 signing_key=None, kid=None, confirm_orders=False, media_fetcher=None, clock=None, live_kw=None):
        self.card_id = card_id
        self.adapter = adapter or _adapters.get(card_id)
        if self.adapter is None:
            raise NotSupported(f"no adapter for {card_id!r}")
        if card is None:
            import irlpls
            card = irlpls.card(card_id)
        if not card:
            raise ValueError(f"no card {card_id!r}")
        self.card = card
        self.base_url = base_url.rstrip("/")
        self.path_prefix = urllib.parse.urlsplit(self.base_url).path.rstrip("/")
        op = dict(operator or {})
        op.setdefault("name", f"irlpls reference library ({card_id} shim)")
        op["role"] = "shim"
        if not _https(op.get("url")):
            op.pop("url", None)
            if _https(self.base_url):
                op["url"] = self.base_url
        self.operator = op
        self.confirm_orders = bool(confirm_orders)
        self.media_fetcher = media_fetcher or fetch_media
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.live_kw = dict(live_kw or {})
        self._key = _load_key(signing_key) if signing_key is not None else None
        self.kid = kid or ("shim-" + sha256_hex(_pub_bytes(self._key))[:12] if self._key else None)
        self._lock = threading.RLock()
        self._quotes, self._orders, self._idem = {}, {}, {}

    # ---- what the card says
    @property
    def modes(self):
        return list((self.card.get("delivery") or {}).get("modes") or [])

    @property
    def route(self):
        if getattr(self.adapter, "can_live", False) and "live" in self.modes:
            return "live"
        if getattr(self.adapter, "can_commission", False) and ("commission" in self.modes or not self.modes):
            return "commission"
        return "live" if getattr(self.adapter, "can_live", False) else "commission"

    def levels(self):
        """Guarantee -> level exactly as the card states it; 'none' where it states nothing usable. Never raised."""
        ver = self.card.get("verification") or {}
        out = {}
        for g in GUARANTEES:
            lv = (ver.get(g) or {}).get("level") if isinstance(ver.get(g), dict) else None
            out[g] = lv if lv in LEVELS else "none"
        return out

    def _unknown(self, g):
        v = (self.card.get("verification") or {}).get(g)
        if not isinstance(v, dict) or v.get("level") not in LEVELS:
            return "The card does not state this guarantee."
        return v.get("unknown") if isinstance(v.get("unknown"), str) else None

    def _is_free(self):
        return (self.card.get("access") or {}).get("cost") in ("free", "free with key")

    def _rights(self):
        lic = self.card.get("licensing") or {}
        out = {k: lic[k] for k in RIGHTS if lic.get(k) in ("allowed", "forbidden")}
        if isinstance(lic.get("buyer_owns_output"), bool):
            out["buyer_owns_output"] = lic["buyer_owns_output"]
        return out

    def _continue_url(self):
        api = self.card.get("api") or {}
        for a in api.get("auth") or []:
            m = re.search(r"https://[^\s,;)'\"]+", str(a.get("how_to_get") or ""))
            if m:
                return m.group(0).rstrip(".")
        return _https(api.get("docs_url")) or _https(self.card.get("provider_url"))

    def _sensor_kind(self):
        return "human" if (self.card.get("capture") or {}).get("by") in ("people", "contributors", "subject") else "device"

    @property
    def can_cancel(self):
        """True when the adapter documents a cancel call (protocol.cancel)."""
        return bool(getattr(self.adapter, "can_cancel", False))

    # ---- discover
    def discover(self):
        card = json.loads(json.dumps(self.card))
        proto = {"versions": list(VERSIONS), "endpoint": self.base_url,
                 "quote": "optional" if self.route == "live" else "required",
                 "public_quotes": True, "webhooks": False, "cancel": self.can_cancel, "operator": dict(self.operator)}
        if self._key:   # the operator's own keys, on the operator's origin (never presented as the provider's)
            proto["operator"]["jwks_uri"] = self.iss + "/.well-known/irlpls-keys.json"
        card["protocol"] = proto
        return Reply(200, card)

    @property
    def iss(self):
        """Who signs this shim's receipts: the operator's origin (never the provider's)."""
        return _origin(self.operator.get("url") or self.base_url)

    def jwks(self):
        """The shim operator's public key set (not the provider's): one ES256 receipt key."""
        if not self._key:
            return {"keys": []}
        return {"keys": [ec_public_jwk(self._key, self.kid)]}

    # ---- checks shared by quote and direct order
    def _check_request(self, req, constraints, path="$.request"):
        if not isinstance(req, dict) or not isinstance(req.get("need"), str) or not req["need"].strip():
            raise Problem("invalid_request", "request.need is required", errors=[{"path": path + ".need", "detail": "required string"}])
        if "policy" in req:
            raise Problem("invalid_request", "a protocol request never carries the Policy; send constraints instead",
                          errors=[{"path": path + ".policy", "detail": "not allowed"}])
        for k in ("lat", "lon"):
            v = req.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                  or abs(v) > (90 if k == "lat" else 180)):
                raise Problem("invalid_request", f"{k} is out of range", errors=[{"path": f"{path}.{k}"}])
        q = req.get("quantity")
        if q is not None and (isinstance(q, bool) or not isinstance(q, int) or not 1 <= q <= MAX_QUANTITY):
            raise Problem("invalid_request", f"quantity is an integer from 1 to {MAX_QUANTITY}", errors=[{"path": path + ".quantity"}])
        if req.get("targets_person") is True or detect_targets_person(*text_fields(req)):
            raise Problem("person_targeting_refused", "This shim does not send capture aimed at a specific person, "
                          "whatever the buyer's policy.")
        mode = req.get("mode")
        if mode == "commission" and self.route == "live":
            raise Problem("no_match", f"{self.card_id} only reports what its instruments already record; it takes no commissions.")
        if mode == "live" and self.route == "commission":
            raise Problem("too_slow", f"{self.card_id} captures to order; it has no live feed.")
        areas = (self.card.get("coverage") or {}).get("areas") or []
        cc = req.get("country_code")
        if isinstance(cc, str) and areas and not covers(areas, cc):
            raise Problem("outside_coverage", f"{self.card_id} covers {', '.join(areas)}, not {cc.upper()}.")
        constraints = constraints or {}
        if not isinstance(constraints, dict):
            raise Problem("invalid_request", "constraints must be an object", errors=[{"path": "$.constraints"}])
        if constraints.get("scene_nonce"):
            raise Problem("proof_unavailable", "This shim cannot make the provider show the buyer's nonce in the capture.",
                          violations=["scene_nonce"])
        need = [r for r in constraints.get("rights_needed") or [] if r not in self._granted_rights()]
        if need:
            raise Problem("rights_unavailable", "The card does not state these rights as granted: " + ", ".join(need),
                          violations=[f"rights_needed:{r}" for r in need])
        lv, rank = self.levels(), {"none": 0, "claimed": 1, "evidenced": 2}
        short = [g for g, want in (constraints.get("proof") or {}).items() if rank.get(lv.get(g, "none"), 0) < rank.get(want, 3)]
        if short:
            raise Problem("proof_unavailable", "The card's levels are below what was asked: "
                          + ", ".join(f"{g} is {lv.get(g, 'none')}" for g in short), violations=[f"proof:{g}" for g in short])
        if "max_total" in constraints:
            _read_money(constraints["max_total"], "$.constraints.max_total")

    def _granted_rights(self):
        r = self._rights()
        return {k for k, v in r.items() if v == "allowed" or v is True}

    def _price(self):
        """(total Decimal or None, firm). Live + free card → 0, not firm (the card, not a provider quote)."""
        return (Decimal(0), False) if self._is_free() else (None, False)

    def _spec(self, req, ext_id=None, deliver_by=None):
        hook = getattr(self.adapter, "spec_from_request", None)   # the adapter's own hook wins (no shim edit per provider)
        if callable(hook):
            return hook(req, ext_id, deliver_by)
        b = SPEC_BUILDERS.get(self.card_id)
        return b(req, ext_id, deliver_by) if b else _spec_generic(req, ext_id)

    def _order_total(self, resp):
        hook = getattr(self.adapter, "order_total", None)
        if callable(hook):
            return hook(resp)
        return ORDER_TOTAL.get(self.card_id, lambda r: None)(resp)

    # ---- quote
    def quote(self, body, headers=None):
        self._version(headers)
        if not isinstance(body, dict) or "request" not in body:
            raise Problem("invalid_request", "body is {request, constraints?}", errors=[{"path": "$.request", "detail": "required"}])
        req, cons = body["request"], body.get("constraints") or {}
        self._check_request(req, cons)
        now = self.clock()
        offer = {"id": "o_1", "provider": {"card_id": self.card_id, "name": self.card.get("name", self.card_id)},
                 "route": self.route, "firm": False, "quantity": int(req.get("quantity") or 1)}
        extra, cur = {}, "USD"
        if self.route == "live":
            if req.get("lat") is None or req.get("lon") is None:
                raise Problem("needs_input", "Give coordinates; this shim does not geocode.",
                              questions=[{"field": "lat", "ask": "Latitude of the place to read", "blocking": True},
                                         {"field": "lon", "ask": "Longitude of the place to read", "blocking": True}])
            total, firm = self._price()
            expires = now + timedelta(minutes=15)
        else:
            dry = self._dry_run(req, cons.get("deliver_by"))
            missing = dry.get("missing") or []
            if missing:
                raise Problem("needs_input", "The provider needs more before it can be asked.",
                              questions=[_question(m) for m in missing])
            total, cur, firm = self._dry_price(dry)
            expires = now + timedelta(hours=1)
            extra["x-com.irlpls.shim"] = {"dry_run": True, "would_send": dry.get("would_send"), "note": dry.get("note")}
        if total is not None:
            offer.update(_price_fields(total, cur))
            if self.route == "live":
                offer["unit_price"] = _money(total)
        offer["firm"] = bool(firm and "total" in offer)
        if "max_total" in cons and total is not None:
            cap, cap_cur = _read_money(cons["max_total"], "$.constraints.max_total")
            if cap_cur != cur or total > cap:
                raise Problem("over_max_total", f"The cheapest total is {total} {cur}" + ("" if cap_cur == cur else
                              f"; max_total is in {cap_cur} and the shim never converts currencies") + ".", violations=["max_total"])
        rights = self._rights()
        if rights:
            offer["rights"] = rights
        offer["proof"] = self.levels()
        offer["scene_nonce"] = False
        offer["expires_at"] = _iso(expires)
        offer["order_url"] = self.base_url + "/orders"
        if self.route == "commission" and not self.adapter.configured():
            cu = self._continue_url()
            if cu:
                offer["continue_url"] = cu
        offer.update(extra)
        qid = "q_" + secrets.token_urlsafe(12)
        quote = {"id": qid, "created_at": _iso(now), "expires_at": _iso(expires),
                 "request_sha256": sha256_hex(jcs(req)), "offers": [offer]}
        with self._lock:
            self._quotes[qid] = {"quote": quote, "request": req, "constraints": cons}
        return Reply(201, quote, {"Location": f"{self.base_url}/quotes/{qid}"})

    def get_quote(self, quote_id):
        with self._lock:
            q = self._quotes.get(quote_id)
        if not q:
            raise Problem("not_found", "No such quote.", quote_id=quote_id)
        return Reply(200, q["quote"])

    def _dry_run(self, req, deliver_by=None):
        try:
            dry = self.adapter.commission(self._spec(req, None, deliver_by), confirm=False)
        except NotSupported as e:
            raise Problem("no_match", str(e)) from None
        except http.HTTPError as e:
            raise Problem("provider_unavailable", f"{self.card_id} quote failed (HTTP {e.status})", headers={"Retry-After": "60"}) from None
        if not isinstance(dry, dict) or not dry.get("dry_run"):
            # Defence in depth: a dry run that did not say it was one is treated as unsafe and never repeated.
            raise Problem("provider_unavailable", "the adapter's dry run did not identify itself as one")
        return dry

    @staticmethod
    def _dry_price(dry):
        """(amount, currency, firm) from a real provider quote inside the dry run (adapters.base.quote_price): firm only
        in USD; another currency stays indicative, in its own currency."""
        return quote_price(dry)

    @staticmethod
    def _dry_total(dry):
        """(firm USD total or None, firm): a firm total only from a real provider quote in USD."""
        amt, cur, firm = quote_price(dry)
        return (amt, True) if firm else (None, False)

    # ---- order
    def order(self, body, headers=None):
        self._version(headers)
        key = _hdr(headers, "Idempotency-Key")
        if not key:
            raise Problem("idempotency_key_required", "POST /orders needs an Idempotency-Key header.")
        if not KEY.match(key):
            raise Problem("invalid_request", "Idempotency-Key must be 1-255 printable ASCII characters, no spaces.")
        scope = sha256_hex((_hdr(headers, "Authorization") or "").encode())[:16]
        fp = sha256_hex(jcs(body)) if isinstance(body, dict) else None
        slot = (scope, key)
        with self._lock:
            seen = self._idem.get(slot)
            if seen:
                if seen["fp"] != fp:
                    raise Problem("idempotency_key_reused", "This key was used with a different body.")
                if seen.get("reply") is None:
                    raise Problem("request_in_progress", "The first call with this key is still running.", headers={"Retry-After": "2"})
                return seen["reply"]
            self._idem[slot] = {"fp": fp, "reply": None}   # persisted as pending before anything is called
        try:
            reply = self._order(body, key, scope)
        except Problem:
            with self._lock:
                self._idem.pop(slot, None)   # nothing was placed: the client may retry with the same key
            raise
        except Exception:
            with self._lock:
                self._idem.pop(slot, None)
            raise
        with self._lock:
            self._idem[slot]["reply"] = reply
        return reply

    def _order(self, body, key, scope=""):
        if not isinstance(body, dict):
            raise Problem("invalid_request", "body must be an object")
        has_q, has_r = "quote_id" in body or "offer_id" in body, "request" in body
        if has_q == has_r:
            raise Problem("invalid_request", "send either {quote_id, offer_id} or {request, constraints:{max_total}}",
                          errors=[{"path": "$", "detail": "exactly one form"}])
        nonce = body.get("nonce")
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise Problem("invalid_request", "nonce is required: 22-128 base64url characters (at least 128 random bits)",
                          errors=[{"path": "$.nonce"}])
        cref = body.get("client_reference")
        if cref is not None and (not isinstance(cref, str) or len(cref) > 255):
            raise Problem("invalid_request", "client_reference is a string of at most 255 characters", errors=[{"path": "$.client_reference"}])
        cons = body.get("constraints") or {}
        body = dict(body, _scope=scope)
        if has_q:
            with self._lock:
                q = self._quotes.get(body.get("quote_id"))
            if not q or body.get("offer_id") not in [o["id"] for o in q["quote"]["offers"]]:
                raise Problem("not_found", "No such quote or offer.", quote_id=body.get("quote_id"))
            if _parse_time(q["quote"]["expires_at"]) <= self.clock():
                raise Problem("quote_expired", "Re-quote.", quote_id=body["quote_id"])
            req, offer = q["request"], q["quote"]["offers"][0]
            cons = {**q["constraints"], **cons}
            self._check_request(req, cons)
            req_hash = q["quote"]["request_sha256"]
        else:
            req, offer = body["request"], None
            if "max_total" not in cons:
                raise Problem("invalid_request", "a direct order needs constraints.max_total", errors=[{"path": "$.constraints.max_total"}])
            self._check_request(req, cons)
            req_hash = sha256_hex(jcs(req))
        cap = _read_money(cons["max_total"], "$.constraints.max_total") if "max_total" in cons else None
        if self.route == "live":
            return self._order_live(req, offer, cap, nonce, req_hash, body)
        return self._order_commission(req, offer, cap, cons, nonce, req_hash, scope + "|" + key, body)

    def _new_order(self, route, nonce, req_hash, body, total=None, currency="USD"):
        now = _iso(self.clock())
        oid = "ord_" + secrets.token_urlsafe(16)
        o = {"id": oid, "state": "submitted", "card_id": self.card_id, "route": route, "request_sha256": req_hash,
             "created_at": now, "updated_at": now,
             "progress": {"ordered": int((body.get("request") or {}).get("quantity") or 1), "delivered": 0},
             "status_history": [{"at": now, "state": "submitted"}],
             "links": {"self": f"{self.base_url}/orders/{oid}", "delivery": f"{self.base_url}/orders/{oid}/delivery"}}
        for k in ("quote_id", "offer_id"):
            if k in body:
                o[k] = body[k]
        if "request" not in body and body.get("quote_id"):
            with self._lock:
                q = self._quotes.get(body["quote_id"])
            if q:
                o["progress"]["ordered"] = int(q["request"].get("quantity") or 1)
        if total is not None and _money(total, currency):
            o["total"] = _money(total, currency)
        return o, {"nonce": nonce, "request_sha256": req_hash, "delivery": None, "provider_order": None,
                   "client_reference": body.get("client_reference"), "scope": body.get("_scope")}

    def _order_live(self, req, offer, cap, nonce, req_hash, body):
        total, _ = self._price()
        if total is None:
            raise Problem("quote_required", f"{self.card_id} publishes no price, so the shim cannot promise to stay under max_total.")
        if cap is not None and (cap[1] != "USD" or total > cap[0]):
            raise Problem("over_max_total", f"The total is {total} USD.", violations=["max_total"])
        if req.get("lat") is None or req.get("lon") is None:
            raise Problem("needs_input", "Give coordinates; this shim does not geocode.",
                          questions=[{"field": "lat", "ask": "Latitude", "blocking": True}, {"field": "lon", "ask": "Longitude", "blocking": True}])
        o, priv = self._new_order("live", nonce, req_hash, body, total)
        try:
            res = self.adapter.live(req["lat"], req["lon"], **self.live_kw)
        except MissingCredentials as e:
            raise Problem("needs_account", str(e), continue_url=self._continue_url()) from None
        except NotSupported as e:
            raise Problem("no_match", str(e)) from None
        except http.HTTPError as e:
            if e.status == 429:
                raise Problem("rate_limited", f"{self.card_id} rate-limited the shim", headers={"Retry-After": "60"}) from None
            self._fail(o, "provider_error", f"{self.card_id} returned HTTP {e.status}")
            self._store(o, priv)
            return Reply(200, o)
        items = list(res)
        warnings = list(getattr(res, "warnings", []))
        if not items:
            self._fail(o, "undeliverable", f"{self.card_id} reported nothing near this place.")
            self._store(o, priv)
            return Reply(200, o)
        delivery = self._delivery(o["id"], items, priv, complete=True, warnings=warnings)
        self._transition(o, "delivered")
        o["progress"]["delivered"] = len(delivery["items"])
        priv["delivery"] = delivery
        o["delivery"] = delivery
        self._store(o, priv)
        return Reply(200, o)

    def _order_commission(self, req, offer, cap, cons, nonce, req_hash, key, body):
        if offer is None:
            raise Problem("quote_required", f"{self.card_id} publishes no price: quote first, then order the offer.")
        if not offer.get("firm") and cap is None:
            raise Problem("quote_required", "This offer is indicative (the provider publishes no price): send "
                          "constraints.max_total with the order.")
        if cap is not None and offer.get("x-com.irlpls.indicative_price"):
            raise Problem("over_max_total", "The offer is priced in " + str(offer["x-com.irlpls.indicative_price"].get("currency"))
                          + ", which no max_total can cap; order it with the provider directly.", violations=["max_total"])
        if offer.get("total") and cap is not None and (cap[1] != offer["total"]["currency"] or Decimal(offer["total"]["amount"]) > cap[0]):
            raise Problem("over_max_total", "The offer's total is over max_total, or in another currency.", violations=["max_total"])
        if not self.confirm_orders:
            raise Problem("insufficient_scope", "This shim is running quote-only: its operator has not enabled orders. "
                          "Nothing was sent to the provider.")
        if not self.adapter.configured():
            raise Problem("needs_account", f"The shim has no {self.card_id} credentials ({self.adapter.env}). Nothing was sent.",
                          continue_url=self._continue_url())
        ext_id = "irlpls-" + sha256_hex(f"{key}|{self.card_id}".encode())[:32]   # key is "<caller scope>|<Idempotency-Key>"
        spec = self._spec(req, ext_id, cons.get("deliver_by"))
        try:
            resp = self.adapter.commission(spec, confirm=True)
        except MissingCredentials as e:
            raise Problem("needs_account", str(e), continue_url=self._continue_url()) from None
        except http.HTTPError as e:
            raise Problem("provider_unavailable" if e.status >= 500 or e.status == 429 else "invalid_request",
                          f"{self.card_id} refused the order (HTTP {e.status}); see upstream", upstream={"status": e.status},
                          headers={"Retry-After": "60"} if e.status >= 500 or e.status == 429 else None) from None
        if isinstance(resp, dict) and resp.get("dry_run"):
            raise Problem("provider_unavailable", "the adapter returned a dry run where an order was expected")
        total = Decimal(offer["total"]["amount"]) if offer.get("total") else None
        reported = self._order_total(resp)   # USD, by the hook's contract
        cur = (offer.get("total") or {}).get("currency", "USD") if reported is None else "USD"
        o, priv = self._new_order("commission", nonce, req_hash, body, reported if reported is not None else total, cur)
        priv["provider_order"] = resp
        ps = self.adapter.provider_status(resp) if isinstance(resp, dict) else None
        if ps:
            o["provider_status"] = ps
            o["status_history"][0]["provider_status"] = ps
        if reported is not None and cap is not None and (cap[1] != "USD" or reported > cap[0]):
            o["x-com.irlpls.warning"] = (f"{self.card_id} reports a cost of {reported} USD, over max_total {cap[0]}; "
                                         "the shim cannot cap what the provider charges. Review or cancel with the provider.")
        self._store(o, priv)
        return Reply(202, o, {"Location": o["links"]["self"], "Retry-After": "600"})

    # ---- status / deliver / cancel
    def status(self, order_id, headers=None):
        self._version(headers)
        o, priv = self._get(order_id)
        if o["state"] in ("delivered", "failed") or priv["provider_order"] is None:
            return Reply(200, self._public(o))
        try:
            resp = self.adapter.status(priv["provider_order"])
        except (NotSupported, MissingCredentials):
            return Reply(200, self._public(o))
        except http.HTTPError:
            return Reply(200, self._public(o), {"Retry-After": "300"})
        raw = self.adapter.provider_status(resp)
        mapped = self.adapter.map_status(raw) or "in_flight"   # unmapped: in_flight, word passed through verbatim
        with self._lock:
            self._approvals(o, priv, resp if mapped == "in_flight" else None)
            if mapped == "delivered":
                items = self.adapter.deliverables(resp) or []
                priv["delivery"] = self._delivery(o["id"], items, priv, complete=True)
                o["progress"]["delivered"] = len(priv["delivery"]["items"])
                self._transition(o, "delivered", raw)
            elif mapped == "failed":
                w = (raw or "").lower()
                code = next((c for c, s in (("rejected", "reject"), ("expired", "expir"), ("cancelled", "cancel")) if s in w), "provider_error")
                self._fail(o, code, f"{self.card_id} reports {raw!r}", raw)
            else:
                self._transition(o, "in_flight", raw)
        return Reply(200, self._public(o))

    def deliver(self, order_id, headers=None):
        self._version(headers)
        o, priv = self._get(order_id)
        if priv["delivery"] is not None:
            return Reply(200, priv["delivery"])
        return Reply(200, {"order_id": o["id"], "complete": False, "items": [], "provenance": self._provenance(signed=False),
                           "relayed_by": [dict(self.operator)]})

    def list_orders(self, query, headers=None):
        """GET /orders?client_reference=R: the caller's orders with that reference, newest first."""
        self._version(headers)
        refs = urllib.parse.parse_qs(query or "").get("client_reference")
        if not refs or len(refs) != 1:
            raise Problem("invalid_request", "GET /orders needs exactly one client_reference query parameter.",
                          errors=[{"path": "$.client_reference"}])
        scope = sha256_hex((_hdr(headers, "Authorization") or "").encode())[:16]
        with self._lock:
            got = [o for o, pv in self._orders.values() if pv.get("client_reference") == refs[0] and pv.get("scope") == scope]
        got.sort(key=lambda o: o["created_at"], reverse=True)
        return Reply(200, {"orders": [self._public(o) for o in got[:50]]})

    def cancel(self, order_id, headers=None):
        """Cooperative cancel (STANDARD 14.6). Terminal orders come back unchanged. With a documented provider cancel the
        provider is asked once per Idempotency-Key; the order ends failed / cancelled only when the provider's own status
        says so (in the cancel response or a later status), else it stays in_flight with provider_status as sent."""
        self._version(headers)
        key = _hdr(headers, "Idempotency-Key")
        if not key:
            raise Problem("idempotency_key_required", "POST /orders/{id}/cancel needs an Idempotency-Key header.")
        if not KEY.match(key):
            raise Problem("invalid_request", "Idempotency-Key must be 1-255 printable ASCII characters, no spaces.")
        o, priv = self._get(order_id)
        if o["state"] in ("delivered", "failed"):
            return Reply(200, self._public(o))
        if not self.can_cancel or priv.get("provider_order") is None:
            raise Problem("not_cancellable", f"No cancel is documented for {self.card_id} in this shim; cancel with the provider.",
                          order_id=order_id)
        slot = ("cancel", order_id, sha256_hex((_hdr(headers, "Authorization") or "").encode())[:16], key)
        with self._lock:
            if slot in self._idem:
                return Reply(200, self._public(o))
            self._idem[slot] = {"fp": None, "reply": True}
        try:
            resp = self.adapter.cancel(priv["provider_order"])
        except (NotSupported, MissingCredentials) as e:
            with self._lock:
                self._idem.pop(slot, None)
            raise Problem("not_cancellable", f"{self.card_id}: {e}", order_id=order_id) from None
        except http.HTTPError as e:
            with self._lock:
                self._idem.pop(slot, None)
            if e.status >= 500 or e.status == 429:
                raise Problem("provider_unavailable", f"{self.card_id} did not answer the cancel (HTTP {e.status})",
                              headers={"Retry-After": "60"}) from None
            raise Problem("not_cancellable", f"{self.card_id} refused the cancel (HTTP {e.status}); the work may be too far "
                          "along. Check the order's status.", order_id=order_id, upstream={"status": e.status}) from None
        raw = self.adapter.provider_status(resp) if isinstance(resp, dict) else None
        with self._lock:
            if raw and self.adapter.map_status(raw) == "failed":
                self._fail(o, "cancelled", f"{self.card_id} reports {raw!r} after a cancel", raw)
            else:
                self._transition(o, "in_flight", raw or "cancel_requested")
            o.pop("x-com.irlpls.approval", None)
            priv["approvals"] = []
        return Reply(200, self._public(o))

    # ---- two-step orders: an explicit, per-order second approval
    def _approvals(self, o, priv, resp):
        """Record what the provider is waiting for the person to approve (adapter.pending_approvals), or clear it."""
        try:
            got = self.adapter.pending_approvals(resp) if resp is not None else []
        except Exception:
            got = []
        opts = []
        for a in got or []:
            if not isinstance(a, dict):
                continue
            opt = {"id": a.get("id"), "label": str(a.get("label") or "")[:200] or None}
            opt.update(_price_fields(a.get("amount"), a.get("currency")))
            for k in ("expires_at", "deliver_by"):
                if isinstance(a.get(k), str):
                    opt[k] = a[k][:40]
            opts.append({k: v for k, v in opt.items() if v is not None})
        priv["approvals"] = opts
        if opts:
            o["x-com.irlpls.approval"] = {
                "required": True, "options": opts, "approve_url": f"{self.base_url}/orders/{o['id']}/approve",
                "note": (f"{self.card_id} is waiting for the buyer to accept a price. Nothing is accepted automatically: "
                         "POST approve_url with {approval_id, total (as shown), confirm: true} and an Idempotency-Key, only "
                         "after the person approves this order at this price. A max_total or a standing mandate does not cover it.")}
        else:
            o.pop("x-com.irlpls.approval", None)

    def approve(self, order_id, body, headers=None):
        """Send the provider's second, paid step for one order (Capella approve, GLOBHE accept quote). Needs confirm: true,
        the approval id and the exact total the person saw, an Idempotency-Key, and an operator with confirm_orders."""
        self._version(headers)
        key = _hdr(headers, "Idempotency-Key")
        if not key:
            raise Problem("idempotency_key_required", "POST /orders/{id}/approve needs an Idempotency-Key header.")
        if not KEY.match(key):
            raise Problem("invalid_request", "Idempotency-Key must be 1-255 printable ASCII characters, no spaces.")
        o, priv = self._get(order_id)
        if not isinstance(body, dict) or body.get("confirm") is not True:
            raise Problem("invalid_request", "approving spends money: send confirm: true with the approval_id and the total "
                          "the person approved", errors=[{"path": "$.confirm", "detail": "must be true"}])
        opts = priv.get("approvals") or []
        aid = body.get("approval_id")
        opt = next((x for x in opts if x.get("id") == aid), None) if aid is not None else (opts[0] if len(opts) == 1 else None)
        if o["state"] in ("delivered", "failed") or not opts:
            raise Problem("not_found", "This order is not waiting for an approval; check its status first.", order_id=order_id)
        if opt is None:
            raise Problem("invalid_request", "approval_id must be one of the order's x-com.irlpls.approval options",
                          errors=[{"path": "$.approval_id"}])
        shown = opt.get("total") or opt.get("x-com.irlpls.indicative_price")
        if shown is not None:
            got = body.get("total")
            if not isinstance(got, dict) or got.get("amount") != shown.get("amount") or got.get("currency") != shown.get("currency"):
                raise Problem("over_max_total", "total must repeat the option's price exactly as shown (the price the person "
                              "approved); it changed or is missing.", violations=["total"])
        slot = ("approve", order_id, sha256_hex((_hdr(headers, "Authorization") or "").encode())[:16], key)
        with self._lock:
            if slot in self._idem:
                return Reply(200, self._public(o))
        if not self.confirm_orders:
            raise Problem("insufficient_scope", "This shim is running quote-only: its operator has not enabled orders. "
                          "Nothing was sent to the provider.")
        if not self.adapter.configured():
            raise Problem("needs_account", f"The shim has no {self.card_id} credentials. Nothing was sent.",
                          continue_url=self._continue_url())
        with self._lock:
            self._idem[slot] = {"fp": None, "reply": True}
        try:
            resp = self.adapter.approve(priv["provider_order"], approval_id=aid, confirm=True)
        except NotSupported as e:
            with self._lock:
                self._idem.pop(slot, None)
            raise Problem("no_match", str(e)) from None
        except MissingCredentials as e:
            with self._lock:
                self._idem.pop(slot, None)
            raise Problem("needs_account", str(e), continue_url=self._continue_url()) from None
        except http.HTTPError as e:
            with self._lock:
                self._idem.pop(slot, None)
            raise Problem("provider_unavailable" if e.status >= 500 or e.status == 429 else "invalid_request",
                          f"{self.card_id} refused the approval (HTTP {e.status})", upstream={"status": e.status}) from None
        if isinstance(resp, dict) and resp.get("dry_run"):
            raise Problem("provider_unavailable", "the adapter returned a dry run where an approval was expected")
        with self._lock:
            ps = self.adapter.provider_status(resp) if isinstance(resp, dict) else None
            self._transition(o, "in_flight", ps or "approved")
            if opt.get("total"):
                o["total"] = opt["total"]
            o["x-com.irlpls.approved"] = {"approval_id": aid, "at": _iso(self.clock()), "price": shown}
            o.pop("x-com.irlpls.approval", None)
            priv["approvals"] = []
        return Reply(200, self._public(o))

    # ---- helpers
    def _version(self, headers):
        v = _hdr(headers, "Irlpls-Version")
        if v is not None and v not in VERSIONS:
            raise Problem("unsupported_version", f"This shim speaks {', '.join(VERSIONS)}.", supported_versions=list(VERSIONS))

    def _get(self, order_id):
        with self._lock:
            got = self._orders.get(order_id)
        if not got:
            raise Problem("not_found", "No such order.", order_id=order_id)
        return got

    def _store(self, o, priv):
        with self._lock:
            self._orders[o["id"]] = (o, priv)

    @staticmethod
    def _public(o):
        return {k: v for k, v in o.items() if k != "delivery" or o["route"] == "live"}

    def _transition(self, o, state, provider_status=None):
        if o["state"] in ("delivered", "failed"):
            return   # terminal states never change
        last = o["status_history"][-1]
        if last["state"] == state and last.get("provider_status") == provider_status:
            return
        now = _iso(self.clock())
        h = {"at": now, "state": state}
        if provider_status:
            h["provider_status"] = provider_status
            o["provider_status"] = provider_status
        o["status_history"].append(h)
        o["state"], o["updated_at"] = state, now

    def _fail(self, o, code, text, provider_status=None):
        if o["state"] in ("delivered", "failed"):
            return
        self._transition(o, "failed", provider_status)
        o["reason_code"], o["reason_text"] = code, text
        o["status_history"][-1].update(reason_code=code, reason_text=text)

    def _provenance(self, signed, signer=None):
        signer = signer or f"the shim operator ({self.operator['name']}, key {self.kid})"
        lv = self.levels()
        stages = []
        for sid, shown, gs in STAGES:
            st = {"stage": sid, "shown_as": shown}
            if gs:
                st["guarantees"] = {}
                for g in gs:
                    e = {"level": lv[g], "basis": "card"}
                    u = self._unknown(g)
                    if u:
                        e["unknown"] = u
                    st["guarantees"][g] = e
            elif sid == "custody":
                st["unknown"] = (f"Relayed by {self.operator['name']}, a shim, from the {self.card.get('name', self.card_id)} API; "
                                 "hops between the provider and the shim are not logged.")
                if signed:
                    st["evidence"] = [{"type": "receipt", "detail": f"Each item carries a receipt signed by {signer}, "
                                       f"not by {self.card_id}. It attests custody only: what the shim received and relayed. "
                                       "It raises no provider guarantee."}]
            else:  # delivery
                st["unknown"] = (f"No provider receipt: {self.card_id} signs nothing" + (
                    "; the shim's own receipts cover custody only." if signed else ", and this shim has no signing key."))
            stages.append(st)
        p = {"card_id": self.card_id, "stages": stages}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(self.card.get("updated") or "")):
            p["card_updated"] = self.card["updated"]
        return p

    def _delivery(self, order_id, observations, priv, complete, warnings=()):
        items, notes = [], list(warnings)
        for n, obs in enumerate(observations, 1):
            it = self._item(obs, f"itm_{n}", notes)
            if self._key and it.get("phenomenon_time"):
                it["receipt"] = self._receipt(order_id, it, priv)
            items.append(it)
        d = {"order_id": order_id, "complete": complete, "items": items, "provenance": self._provenance(signed=bool(self._key)),
             "relayed_by": [dict(self.operator)]}
        if notes:
            d["x-com.irlpls.warnings"] = notes
        return d

    def _item(self, obs, iid, notes, keep_bytes=False):
        it = {k: v for k, v in dict(obs).items() if v is not None and k not in ("provider_proof", "receipt", "provenance", "c2pa")}
        it["id"], it["provider"] = iid, self.card_id
        it.setdefault("kind", "observation")
        pt = it.get("phenomenon_time")
        t = _parse_time(pt) if pt is not None else None
        it["phenomenon_time"] = _iso(t) if t else None
        if it.get("result_time") is not None:
            rt = _parse_time(it["result_time"])
            if rt:
                it["result_time"] = _iso(rt)
            else:
                it.pop("result_time")
        for k in ("lat", "lon"):
            if k in it and (isinstance(it[k], bool) or not isinstance(it[k], (int, float))):
                it.pop(k)
        for k in ("unit", "label"):
            if k in it and not isinstance(it[k], str):
                it[k] = str(it[k])
        it["sensor"] = {"kind": self._sensor_kind()}
        url = it.get("url")
        if url is not None and (not _https(url) or str(it.get("kind", "")).lower() not in MEDIA_KINDS):
            it["x-com.irlpls.source_url"] = it.pop("url")   # an item url is its media file, hashed; other links go here
        elif url:
            try:
                data, ctype = self.media_fetcher(url)
                it["sha256"], it["bytes"] = sha256_hex(data), len(data)
                if keep_bytes:
                    it["x-com.irlpls.media_bytes"] = data
                it["media_type"] = ctype or mimetypes.guess_type(urllib.parse.urlsplit(url).path)[0] or "application/octet-stream"
            except Exception as e:  # unhashed media is never offered as a media url (the client could not check it)
                it["x-com.irlpls.unhashed_url"] = it.pop("url")
                notes.append({"provider": self.card_id, "code": "media_unhashed", "item": iid,
                              "msg": f"the shim could not download the media to hash it ({type(e).__name__}); url withheld"})
        return it

    def _receipt(self, order_id, it, priv):
        if it.get("sha256"):
            media = {"sha256": it["sha256"], "media_type": it.get("media_type"), "bytes": it.get("bytes")}
        else:  # a reading, not a file: the receipt binds jcs({kind, value, unit, phenomenon_time}) of the item as relayed
            media = value_media(it)
        payload = {"iss": self.iss, "order_id": order_id, "item_id": it["id"], "nonce": priv["nonce"],
                   "request_sha256": priv["request_sha256"], "media": media, "phenomenon_time": it["phenomenon_time"],
                   "time_method": f"as reported by {self.card_id}; not checked by the shim",
                   "place_method": f"as reported by {self.card_id}; not checked by the shim", "issued_at": _iso(self.clock())}
        for k in ("lat", "lon", "precision_m"):
            if isinstance(it.get(k), (int, float)) and not isinstance(it.get(k), bool):
                payload[k] = it[k]
        return {"jws": _sign_detached(self._key, self.kid, payload), "payload": payload,
                "signer": {"role": "shim", "name": self.operator["name"], "origin": payload["iss"], "not_the_provider": True,
                           "attests": f"custody only: the shim received this item from {self.card_id} and relayed it unchanged. "
                                      f"{self.card_id} did not sign it."}}

    # ---- one entry point for any server
    def handle(self, method, path, headers=None, body=None):
        """Route one HTTP request. `path` may include the endpoint prefix; `body` is bytes, str, a dict or None.
        Always returns a Reply (problems included), with Irlpls-Version echoed."""
        try:
            u = urllib.parse.urlsplit(path)
            r = self._route(method.upper(), u.path, headers, body, u.query)
        except Problem as p:
            r = Reply(p.status, dict(p.body, instance=urllib.parse.urlsplit(path).path), p.headers)
        r.headers = {"Irlpls-Version": VERSION, **(r.headers or {})}
        return r

    def _route(self, method, path, headers, body, query=""):
        if path == "/.well-known/irlpls" and method == "GET":
            return self.discover()
        if path == "/.well-known/irlpls-keys.json" and method == "GET":
            return Reply(200, self.jwks())
        if self.path_prefix and path.startswith(self.path_prefix):
            path = path[len(self.path_prefix):]
        parts = [p for p in path.split("/") if p]
        if method == "GET" and parts == ["orders"]:
            return self.list_orders(query, headers)
        if method == "POST" and parts == ["quotes"]:
            return self.quote(_body(body), headers)
        if method == "GET" and len(parts) == 2 and parts[0] == "quotes":
            return self.get_quote(parts[1])
        if method == "POST" and parts == ["orders"]:
            return self.order(_body(body), headers)
        if len(parts) >= 2 and parts[0] == "orders":
            if method == "GET" and len(parts) == 2:
                return self.status(parts[1], headers)
            if method == "GET" and parts[2:] == ["delivery"]:
                return self.deliver(parts[1], headers)
            if method == "POST" and parts[2:] == ["cancel"]:
                return self.cancel(parts[1], headers)
            if method == "POST" and parts[2:] == ["approve"]:
                return self.approve(parts[1], _body(body), headers)
        raise Problem("not_found", f"No operation at {method} {path}.")


Shim = AdapterShim


# ------------------------------------------------------------------- mount in irlpls.protocol.server (Fulfiller)

class AdapterFulfiller:
    """The same shim rules behind irlpls.protocol.server's `Fulfiller` interface (quote / start / poll / cancel /
    provenance / retry_after), duck-typed so this module never imports the server at load time:

        ProviderServer(AdapterFulfiller(AdapterShim("usgs-water", operator=...)), endpoint=..., provider_url=<operator's origin>)

    That server signs a receipt on every item with ITS key: mounted like this, the server's operator is the shim
    operator, and the provenance returned here says those receipts are the operator's, custody only, not the provider's.
    Limits of that interface today: Terms.total is required, so an offer whose price the provider does not publish
    (ProxyPics) cannot be represented and quote() answers quote_required; use AdapterShim.handle / serve_adapter for
    indicative offers."""

    def __init__(self, shim):
        self.shim = shim
        self.card = shim.card

    @staticmethod
    def _models():
        from . import models as M  # the server's Problem type, imported only when mounted
        return M

    def _wrap(self, fn, *a):
        try:
            return fn(*a)
        except Problem as p:
            M = self._models()
            ext = {k: v for k, v in p.body.items() if k not in ("type", "title", "status", "code", "resolution", "detail")}
            raise M.Problem(p.code, p.body.get("detail"), headers=p.headers or None, **ext) from None

    def quote(self, request, constraints, client):
        return self._wrap(self._quote, request, constraints or {})

    def _quote(self, request, constraints):
        from .server import Terms
        s = self.shim
        s._check_request(request, constraints)
        rights = s._rights()
        if s.route == "live":
            if request.get("lat") is None or request.get("lon") is None:
                raise Problem("needs_input", "Give coordinates; this shim does not geocode.",
                              questions=[{"field": "lat", "ask": "Latitude", "blocking": True}, {"field": "lon", "ask": "Longitude", "blocking": True}])
            total, _ = s._price()
            if total is None:
                raise Problem("quote_required", f"{s.card_id} publishes no price.")
            # firm=False: the price is the card's, not a provider quote
            return Terms(route="live", total=total, unit_price=total, quantity=1, unit="reading", firm=False,
                         rights=rights, proof=s.levels())
        dry = s._dry_run(request, constraints.get("deliver_by"))
        if dry.get("missing"):
            raise Problem("needs_input", "The provider needs more before it can be asked.", questions=[_question(m) for m in dry["missing"]])
        total, cur, firm = s._dry_price(dry)
        if total is None or not _money(total, cur):
            raise Problem("quote_required", f"{s.card_id} publishes no price (or not in an ISO 4217 currency) and its dry run "
                          "returned none; this interface cannot carry an indicative offer without a total.")
        return Terms(route="commission", total=total, currency=cur, quantity=int(request.get("quantity") or 1), firm=firm,
                     rights=rights, proof=s.levels())

    def start(self, rec):
        return self._wrap(self._start, rec)

    def _start(self, rec):
        from .server import Observation, Update
        s, req = self.shim, rec["request"]
        if rec["route"] == "live":
            try:
                res = s.adapter.live(req["lat"], req["lon"], **s.live_kw)
            except MissingCredentials as e:
                raise Problem("needs_account", str(e), continue_url=s._continue_url()) from None
            except http.HTTPError as e:
                return Update(state="failed", reason_code="provider_error", reason_text=f"{s.card_id} returned HTTP {e.status}")
            obs, notes = [], []
            for n, o in enumerate(res, 1):
                it = s._item(o, f"itm_{n}", notes, keep_bytes=True)
                media = it.pop("x-com.irlpls.media_bytes", None)   # the server stores, hashes and serves it
                ob = Observation(kind=it["kind"], phenomenon_time=it["phenomenon_time"], lat=it.get("lat"), lon=it.get("lon"),
                                 value=it.get("value"), unit=it.get("unit"), label=it.get("label"), sensor_kind=it["sensor"]["kind"],
                                 time_method=f"as reported by {s.card_id}; not checked by the shim",
                                 place_method=f"as reported by {s.card_id}; not checked by the shim")
                if media is not None:
                    ob.media, ob.media_type = media, it.get("media_type")
                obs.append(ob)
            if not obs:
                return Update(state="failed", reason_code="undeliverable", reason_text=f"{s.card_id} reported nothing near this place.")
            return Update(state="delivered", provider_status="ok", observations=obs)
        if not s.confirm_orders:
            raise Problem("insufficient_scope", "This shim is running quote-only: its operator has not enabled orders. Nothing was sent.")
        if not s.adapter.configured():
            raise Problem("needs_account", f"The shim has no {s.card_id} credentials. Nothing was sent.", continue_url=s._continue_url())
        ext_id = "irlpls-" + sha256_hex(f"{rec['id']}|{s.card_id}".encode())[:32]
        resp = s.adapter.commission(s._spec(req, ext_id), confirm=True)
        if isinstance(resp, dict) and resp.get("dry_run"):
            raise Problem("provider_unavailable", "the adapter returned a dry run where an order was expected")
        rec["fstate"] = {"provider_order": resp}
        return Update(provider_status=s.adapter.provider_status(resp) if isinstance(resp, dict) else None)

    def poll(self, rec):
        from .server import Update
        s, po = self.shim, (rec.get("fstate") or {}).get("provider_order")
        if rec["route"] != "commission" or po is None:
            return []
        try:
            resp = s.adapter.status(po)
        except (NotSupported, MissingCredentials, http.HTTPError):
            return []
        raw = s.adapter.provider_status(resp)
        mapped = s.adapter.map_status(raw) or "in_flight"
        if mapped == "failed":
            w = (raw or "").lower()
            code = next((c for c, k in (("rejected", "reject"), ("expired", "expir"), ("cancelled", "cancel")) if k in w), "provider_error")
            return [Update(state="failed", provider_status=raw, reason_code=code, reason_text=f"{s.card_id} reports {raw!r}")]
        if mapped == "delivered":
            from .server import Observation
            obs = [Observation(kind=o.get("kind", "observation"), phenomenon_time=o.get("phenomenon_time"), lat=o.get("lat"),
                               lon=o.get("lon"), value=o.get("value"), unit=o.get("unit"), label=o.get("label"),
                               sensor_kind=s._sensor_kind()) for o in s.adapter.deliverables(resp) or []]
            return [Update(state="delivered", provider_status=raw, observations=obs)]
        return [Update(state="in_flight", provider_status=raw)]

    @property
    def can_cancel(self):
        return self.shim.can_cancel

    def cancel(self, rec):
        """"accepted" once the provider took the cancel (the order ends when its status says so), "cancelled" when the
        provider's answer already says cancelled, "refused" when there is no documented cancel or the provider refused."""
        s, po = self.shim, (rec.get("fstate") or {}).get("provider_order")
        if not s.can_cancel or po is None:
            return "refused"
        try:
            resp = s.adapter.cancel(po)
        except (NotSupported, MissingCredentials, http.HTTPError):
            return "refused"
        raw = s.adapter.provider_status(resp) if isinstance(resp, dict) else None
        return "cancelled" if raw and s.adapter.map_status(raw) == "failed" else "accepted"

    def provenance(self, rec):
        return self.shim._provenance(signed=True, signer="the operator of the server this shim is mounted in")["stages"]

    def retry_after(self, rec):
        return 5 if rec.get("route") == "live" else 600


def _body(body):
    if isinstance(body, dict):
        return body
    if body in (None, b"", ""):
        raise Problem("invalid_request", "a JSON body is required")
    try:
        out = json.loads(body, parse_constant=_refuse, parse_float=_finite_float, parse_int=_safe_int)
    except (ValueError, UnicodeDecodeError):
        raise Problem("invalid_request", "the body is not valid JSON (NaN, Infinity, overflowing numbers and integers "
                      "beyond 2^53 are refused)") from None
    if not isinstance(out, dict):
        raise Problem("invalid_request", "the body must be a JSON object")
    return out


def _refuse(c):
    raise ValueError(f"{c} is not JSON")


def _finite_float(s):
    f = float(s)
    if not math.isfinite(f):
        raise ValueError("number out of range")
    return f


def _safe_int(s):
    if len(s) > 20 or abs(int(s)) > 2 ** 53:
        raise ValueError("integer out of range")
    return int(s)


def _question(missing):
    field_, _, why = str(missing).partition(":")
    f = {"address": "place"}.get(field_.strip(), field_.strip() or "need")
    q = {"field": f, "ask": "What street address should the provider go to?" if f == "place" else f"Give {f}.", "blocking": True}
    if why.strip():
        q["why"] = why.strip()
    return q


# ------------------------------------------------------------------------------------------------------------ receipts

def _load_key(k):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if isinstance(k, str):
        k = k.encode()
    if isinstance(k, bytes):
        k = serialization.load_pem_private_key(k, password=None)
    if not isinstance(k, ec.EllipticCurvePrivateKey) or k.curve.name != "secp256r1":
        raise ValueError("signing_key must be an EC P-256 private key (ES256)")
    return k


def _pub_bytes(k):
    from cryptography.hazmat.primitives import serialization
    return k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _sign_detached(key, kid, payload):
    """Detached compact JWS (RFC 7515 App. F), ES256, over the RFC 8785 form of the payload (receipts.sign_es256)."""
    return sign_es256(key, kid, payload)


# ------------------------------------------------------------------------------------------------ standalone server

def make_server(shim, port=0, host="127.0.0.1"):
    """A ThreadingHTTPServer serving `shim` (not started). Plain HTTP: put TLS in front for anything but local testing."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "irlpls-shim/0.1"

        def _drain(self, limit):
            """Read and discard at most `limit` bytes, then close: never an unbounded read."""
            self.close_connection = True
            left = limit
            try:
                self.connection.settimeout(2)
                while left > 0:
                    chunk = self.rfile.read1(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            except OSError:
                pass

        def _go(self, method):
            cl = self.headers.get("Content-Length")
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                r = Reply(411, Problem("length_required", "send a Content-Length; Transfer-Encoding is not accepted").body)
            elif cl is not None and not re.fullmatch(r"[0-9]{1,10}", cl.strip()):
                self._drain(16 * MAX_BODY)
                r = Reply(400, Problem("invalid_request", "Content-Length must be a non-negative integer").body)
            elif int(cl or 0) > MAX_BODY:
                self._drain(min(int(cl), 16 * MAX_BODY))
                r = Reply(413, Problem("payload_too_large", f"bodies are limited to {MAX_BODY} bytes").body)
            else:
                n = int(cl or 0)
                r = self.server.shim.handle(method, self.path, self.headers, self.rfile.read(n) if n else None)
            data = json.dumps(r.body).encode()
            r.headers = {"Irlpls-Version": VERSION, **(r.headers or {})}
            self.send_response(r.status)
            self.send_header("Content-Type", "application/problem+json" if r.status >= 400 else "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (r.headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.shim = shim
    return srv


def serve_adapter(card_id, port, host="127.0.0.1", base_url=None, block=True, **shim_kw):
    """Serve one adapter over the protocol with the stdlib HTTP server. Endpoint: http://host:port/irlpls (pass base_url
    for the public https address when it sits behind TLS; the schemas require https links). Commission orders stay
    refused unless confirm_orders=True is passed and the adapter's credentials are set.

    block=True serves forever; block=False starts a daemon thread and returns the server (server.shim is the shim;
    call server.shutdown() to stop)."""
    srv = make_server(None, port, host)   # bind first, so port=0 gets a real port for the links
    srv.shim = AdapterShim(card_id, base_url=base_url or f"http://{host}:{srv.server_address[1]}/irlpls", **shim_kw)
    if block:
        try:
            srv.serve_forever()
        finally:
            srv.server_close()
        return srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":  # python -m irlpls.protocol.shim usgs-water 8431
    import sys
    serve_adapter(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8431)
