"""Reference PROVIDER server for the irlpls wire protocol (docs/protocol/PROTOCOL-DRAFT.md, draft 2026-10-26).

    python -m irlpls.protocol.server --port 8811 --sim        # localhost only by default

Operations (Section 4), under the endpoint path `/irlpls`:
    GET  /.well-known/irlpls                 discover: the Capture Card with its `protocol` block
    GET  /.well-known/irlpls-keys.json       JWK Set: ES256 receipt key + Ed25519 webhook key
    POST /irlpls/quotes                      quote (201 + Location); Idempotency-Key optional
    GET  /irlpls/quotes/{id}
    POST /irlpls/orders                      order (202 + Location + Retry-After; 200 when delivered at once); key required
    GET  /irlpls/orders/{id}                 status, with status_history
    GET  /irlpls/orders?client_reference=R    this client's orders with that reference (recovery after 24 h)
    GET  /irlpls/orders/{id}/delivery        deliver: items + per-stage provenance + detached-JWS receipts
    POST /irlpls/orders/{id}/cancel          cooperative cancel; key required
    GET  /irlpls/media/{token}               short-lived capability URL for delivered media

Errors are RFC 9457 problems with irlpls codes. Idempotency follows draft-ietf-httpapi-idempotency-key-header-08:
missing key 400 idempotency_key_required, same key + different body 422 idempotency_key_reused, same key while the
first call runs 409 request_in_progress, same key + same body replays the stored response (Idempotent-Replayed: true).
Keys are scoped to the authenticated client (one key space across operations) and kept 24 h; "same request" = the
same method, path and RFC 8785 body (an absent body is {}). 2xx and 4xx answers are stored except 401/402/403/409/429; 5xx never is. A 4xx/5xx from
POST /orders means no order exists; once one does, the answer is the Order (a later failure is state failed).

Request bodies: Content-Length is required and parsed strictly (Transfer-Encoding is refused with 411), at most
MAX_BODY bytes, read before authentication only up to that limit; numbers must be finite and integers within 2^53.

What a provider actually does is a pluggable `Fulfiller` (quote / start / poll / cancel). `SimFulfiller` completes
after N seconds with a generated PNG photo, or answers a live reading at once; it never captures anything real.

The server is plain HTTP. In deployment it sits behind a TLS terminator and `endpoint` is the public https URL it
advertises (links, Location, media URLs, the card). The schemas require https URLs, so a bare http endpoint
works for local poking but its responses won't validate.
"""
import argparse
import hashlib
import hmac
import importlib
import json
import math
import os
import pathlib
import re
import secrets
import ssl
import struct
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import models as M
from .models import Problem
from .receipts import ReceiptSigner, WebhookSigner, jcs, request_sha256, sha256_hex, value_media

PREFIX = "/irlpls"
MAX_BODY = 256 * 1024
MAX_QUANTITY = 10000
_CLEN_RE = re.compile(r"^[0-9]{1,10}$")
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$")
_INLINE_MEDIA = ("image/", "video/", "audio/")
_INLINE_DENY = ("image/svg+xml",)
IDEMPOTENCY_TTL_S = 24 * 3600
_KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")
_NOT_STORED = (401, 402, 403, 409, 429)   # the client may fix these and retry with the same key
SCOPE_QUOTE, SCOPE_ORDER, SCOPE_READ = "com.irlpls.capture:quote", "com.irlpls.capture:order", "com.irlpls.capture:read"


# ================================================================ fulfiller interface

@dataclass
class Terms:
    """What a fulfiller offers for one request. The server turns it into an offer and checks it against constraints."""
    route: str                       # "live" | "commission"
    total: Decimal = None            # None: no price known (only with firm=False; the offer then carries no total)
    unit_price: Decimal = None
    currency: str = "USD"
    quantity: int = 1
    unit: str = None
    firm: bool = True
    deliver_by: object = None        # datetime
    rights: dict = field(default_factory=dict)   # {"commercial_use": "allowed", ..., "buyer_owns_output": True}
    proof: dict = field(default_factory=dict)    # guarantee -> level
    scene_nonce: bool = False
    terms_url: str = None


@dataclass
class Observation:
    """One delivered item, as the fulfiller produced it. The server assigns ids, stores media, hashes and signs."""
    kind: str
    phenomenon_time: str             # ISO 8601
    lat: float = None
    lon: float = None
    precision_m: float = None
    value: object = None
    unit: str = None
    label: str = None
    media: bytes = None
    media_type: str = None
    sensor_kind: str = "human"       # device | human | software
    c2pa: str = None
    time_method: str = None
    place_method: str = None


@dataclass
class Update:
    """A change reported by a fulfiller. Any member may be None; observations are appended."""
    state: str = None
    provider_status: str = None
    observations: list = field(default_factory=list)
    reason_code: str = None
    reason_text: str = None


class Fulfiller:
    """Plug-in for what the provider really does. Methods receive the server's order record `rec` (a dict);
    `rec["fstate"]` is the fulfiller's own JSON-serialisable state, persisted with the order.

    quote(request, constraints, client) -> Terms, or raise Problem (outside_coverage, no_match, needs_input, ...)
    start(rec) -> Update            called once, after the order is accepted (outside the server lock)
    poll(rec) -> [Update]           called periodically for non-terminal orders (keep it fast)
    cancel(rec) -> "accepted" | "cancelled" | "refused"
    provenance(rec) -> [stage] or None (None: the card's levels, basis "card", for every stage)
    retry_after(rec) -> seconds the client should wait before polling
    """
    card = {}

    def quote(self, request, constraints, client):  # pragma: no cover - interface
        raise NotImplementedError

    def start(self, rec):  # pragma: no cover - interface
        raise NotImplementedError

    def poll(self, rec):
        return []

    def cancel(self, rec):
        return "refused"

    def provenance(self, rec):
        return None

    def retry_after(self, rec):
        return 60


def _png(width, height, seed, texts):
    """A small valid PNG (gradient), with tEXt chunks. Stdlib only."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows += bytes(((x * 255) // max(1, width - 1), (y * 255) // max(1, height - 1), seed & 0xFF))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    for k, v in texts.items():
        out += chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1", "replace"))
    return out + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


SIM_CARD = {
    "spec_version": M.VERSION,
    "capability": "com.irlpls.capture.card",
    "id": "irlpls-sim",
    "name": "irlpls simulated provider",
    "status": "sample",
    "summary": "Reference simulator for the irlpls wire protocol. Commissions complete after a set time with a generated photo; live readings answer at once. Nothing is captured.",
    "short": "Protocol simulator: fake photos and readings.",
    "capabilities": {"modalities": ["photo", "sensor"], "tasks": ["storefront photo", "site photo", "temperature reading"]},
    "pricing": {"model": "per photo", "from_usd": 12, "unit": "photo", "notes": "Readings are free."},
    "latency": {"typical": "seconds (simulated)", "notes": "Set by --sim-seconds."},
    "coverage": {"regions": ["United States", "Canada", "United Kingdom"], "notes": "Simulated.", "areas": ["US", "CA", "GB"]},
    "licensing": {"summary": "Simulated terms: commercial use and AI training allowed; resale forbidden; buyer owns output.",
                  "ai_training": "allowed", "resale": "forbidden", "buyer_owns_output": True},
    "delivery": {"modes": ["live", "commission"], "speed": "minutes", "basis": "Simulator setting.", "formats": ["png", "json"]},
    "verification": {
        "human": {"level": "claimed", "unknown": "Simulated: no person exists."},
        "application": {"level": "none", "unknown": "Simulated: no app attestation."},
        "camera": {"level": "none", "unknown": "Simulated: the image is generated."},
        "time": {"level": "claimed", "method": "Server clock.", "unknown": "Simulated: the time is the server's clock."},
        "location": {"level": "claimed", "method": "Copied from the request.", "unknown": "Simulated: the position is the requested one."},
        "content": {"level": "claimed", "unknown": "Simulated: the image shows a gradient, not the scene."},
        "corroboration": {"level": "none", "unknown": "Single simulated source."},
    },
    "access": {"cost": "paid", "key_required": False, "automation_allowed": True},
    "updated": "2026-10-28",
}


class SimFulfiller(Fulfiller):
    """Simulated provider. Photos (commission): submitted -> in_flight at 25% of `seconds`; item i of q is captured at
    seconds*(0.5 + 0.5*i/q); delivered `finalize_seconds` after the last capture. Cancel is accepted until every item
    is captured, refused after. Readings (live: mode "live" or modalities ["sensor"]) are free and delivered at once.

    Deterministic test triggers: a `need` containing "[sim:<code>]" makes the quote answer that problem code
    (e.g. terms, provider_unavailable, rate_limited, no_match).
    """

    def __init__(self, seconds=5.0, finalize_seconds=0.0, price="12.00", currency="USD", accept_delay=0.0,
                 coverage=("US", "CA", "GB"), card=None):
        self.seconds, self.finalize, self.currency = float(seconds), float(finalize_seconds), currency
        self.price = Decimal(str(price)) if not isinstance(price, (bool, Decimal)) else price  # str: 0.1 stays 0.1
        if isinstance(price, bool) or not self.price.is_finite() or self.price < 0:
            raise ValueError(f"price must be a finite amount >= 0, not {price!r}")
        self.accept_delay, self.coverage = float(accept_delay), tuple(coverage)
        self.card = json.loads(json.dumps(card or SIM_CARD))

    # -- quote
    def _route(self, request):
        mods = request.get("modalities") or []
        if request.get("mode") == "live" or (mods and set(mods) <= {"sensor"}):
            return "live"
        return "commission"

    def quote(self, request, constraints, client):
        m = re.search(r"\[sim:([a-z_]+)\]", request.get("need", ""))
        if m and m.group(1) in M.PROBLEMS:
            code = m.group(1)
            hdr = {"Retry-After": "2"} if code in ("rate_limited", "provider_unavailable") else None
            raise Problem(code, "Simulated problem (test trigger in the need).", headers=hdr)
        from irlpls.need import detect_targets_person  # read the ask itself, not only the router's flag
        if request.get("targets_person") or detect_targets_person(request.get("need", ""), request.get("why", "")):
            raise Problem("person_targeting_refused", "This provider never photographs or follows a specific person.")
        mods = set(request.get("modalities") or [])
        if mods - {"photo", "sensor"}:
            raise Problem("no_match", "The simulator captures photos and sensor readings only.")
        cc = request.get("country_code")
        if cc and cc not in self.coverage:
            raise Problem("outside_coverage", f"Covered: {', '.join(self.coverage)}.")
        route = self._route(request)
        now = M.now_dt()
        levels = {g: v["level"] for g, v in self.card["verification"].items()}
        rights = {"commercial_use": "allowed", "redistribution": "allowed", "modification": "allowed",
                  "ai_training": "allowed", "resale": "forbidden", "buyer_owns_output": True}
        if route == "live":
            return Terms(route="live", total=Decimal(0), unit_price=Decimal(0), currency=self.currency, quantity=1,
                         unit="reading", deliver_by=now, rights=rights, proof=levels)
        if not (request.get("place") or ("lat" in request and "lon" in request)):
            raise Problem("needs_input", "Say where to take the photo.",
                          questions=[{"field": "place", "ask": "Where should the photo be taken (address or lat/lon)?",
                                      "blocking": True, "why": "A person is sent to a place."}])
        total_s = self.seconds + self.finalize
        wm = request.get("within_minutes")
        if wm is not None and wm * 60 < total_s:
            raise Problem("too_slow", f"Earliest delivery is in {math.ceil(total_s)} s.")
        q = int(request.get("quantity") or 1)
        return Terms(route="commission", total=self.price * q, unit_price=self.price, currency=self.currency, quantity=q,
                     unit="photo", deliver_by=now + timedelta(seconds=math.ceil(total_s) + 1), rights=rights,
                     proof=levels, scene_nonce=True)

    # -- order lifecycle
    def start(self, rec):
        if self.accept_delay:
            time.sleep(self.accept_delay)
        if rec["route"] == "live":
            req = rec["request"]
            v = 10 + int(hashlib.sha256(req["need"].encode()).hexdigest()[:4], 16) % 200 / 10
            obs = Observation(kind="air_temperature", phenomenon_time=M.iso(), lat=req.get("lat"), lon=req.get("lon"),
                              precision_m=1000 if "lat" in req else None, value=v, unit="Cel",
                              label="Simulated air temperature", sensor_kind="device",
                              time_method="simulated: server clock", place_method="simulated: copied from the request")
            return Update(state="delivered", provider_status="ok", observations=[obs])
        rec["fstate"] = {"t0": time.time(), "seconds": self.seconds, "finalize": self.finalize,
                         "q": rec["quantity"], "captured": 0}
        return Update(provider_status="posted")

    def _capture_at(self, fs, i):
        return fs["seconds"] * (0.5 + 0.5 * i / fs["q"])

    def poll(self, rec):
        fs = rec.get("fstate") or {}
        if rec["route"] != "commission" or not fs:
            return []
        el = time.time() - fs["t0"]
        out = []
        if rec.get("cancel_requested") and fs["captured"] < fs["q"]:
            return [Update(state="failed", provider_status="withdrawn", reason_code="cancelled",
                           reason_text="Cancelled by the buyer before capture finished.")]
        if rec["state"] == "submitted" and el >= fs["seconds"] * 0.25:
            out.append(Update(state="in_flight", provider_status="accepted_by_photographer"))
        obs = []
        while fs["captured"] < fs["q"] and el >= self._capture_at(fs, fs["captured"] + 1):
            fs["captured"] += 1
            obs.append(self._photo(rec, fs["captured"], fs["t0"] + self._capture_at(fs, fs["captured"])))
        if obs:
            if rec["state"] == "submitted" and not out:
                out.append(Update(state="in_flight", provider_status="accepted_by_photographer"))
            out.append(Update(provider_status="capturing" if fs["captured"] < fs["q"] else "uploaded", observations=obs))
        if fs["captured"] >= fs["q"] and el >= fs["seconds"] + fs["finalize"]:
            out.append(Update(state="delivered", provider_status="approved"))
        return out

    def _photo(self, rec, i, t):
        req = rec["request"]
        from datetime import datetime, timezone
        when = M.iso(datetime.fromtimestamp(t, timezone.utc))
        texts = {"Comment": "irlpls simulated photo; not a real capture", "irlpls-order": rec["id"]}
        if rec.get("nonce"):
            texts["irlpls-nonce"] = rec["nonce"]
        img = _png(48, 32, i * 37 + len(rec["id"]), texts)
        return Observation(kind="photo", phenomenon_time=when, lat=req.get("lat"), lon=req.get("lon"),
                           precision_m=25 if "lat" in req else None, label=f"Simulated photo {i} of {rec['quantity']}",
                           media=img, media_type="image/png", sensor_kind="human", c2pa="absent",
                           time_method="simulated: server clock",
                           place_method="simulated: copied from the request")

    def cancel(self, rec):
        fs = rec.get("fstate") or {}
        if rec["route"] == "live" or fs.get("captured", 0) >= fs.get("q", 1):
            return "refused"
        return "accepted"

    def retry_after(self, rec):
        return max(1, math.ceil(self.seconds / 4))


# ================================================================ store

class Store:
    """In-memory store, optionally mirrored to `data_dir` (state.json written atomically on every change; media files
    under media/; signing keys in keys.json, mode 0600). Callers hold `lock` while reading or mutating."""

    def __init__(self, data_dir=None):
        self.lock = threading.RLock()
        self.dir = pathlib.Path(data_dir) if data_dir else None
        self.quotes, self.orders, self.idem, self.media = {}, {}, {}, {}
        if self.dir:
            self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.dir / "media").mkdir(mode=0o700, parents=True, exist_ok=True)
            f = self.dir / "state.json"
            if f.exists():
                s = json.loads(f.read_text())
                self.quotes, self.orders = s.get("quotes", {}), s.get("orders", {})
                self.idem = {tuple(json.loads(k)): v for k, v in s.get("idem", {}).items()}
                self.media = s.get("media", {})

    def save(self):
        if not self.dir:
            return
        state = {"quotes": self.quotes, "orders": self.orders,
                 "idem": {json.dumps(list(k)): v for k, v in self.idem.items() if v.get("state") == "done"},
                 "media": {t: {k: v for k, v in m.items() if k != "data"} for t, m in self.media.items()}}
        tmp = self.dir / "state.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(state))
        os.replace(tmp, self.dir / "state.json")

    def put_media(self, token, data, media_type, expires_at):
        meta = {"media_type": media_type, "expires_at": expires_at, "bytes": len(data)}
        if self.dir:
            fd = os.open(self.dir / "media" / token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        else:
            meta["data"] = data
        self.media[token] = meta

    def get_media(self, token):
        m = self.media.get(token)
        if not m:
            return None, None
        if "data" in m:
            return m["data"], m
        p = self.dir / "media" / token if self.dir else None
        return (p.read_bytes() if p and p.exists() else None), m

    def load_keys(self):
        if self.dir and (self.dir / "keys.json").exists():
            k = json.loads((self.dir / "keys.json").read_text())
            return ReceiptSigner.from_jwk(k["receipt"]), WebhookSigner.from_jwk(k["webhook"])
        rs, ws = ReceiptSigner.generate(), WebhookSigner.generate()
        if self.dir:  # created 0600 from the start, never world-readable for a moment
            fd = os.open(self.dir / "keys.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps({"receipt": rs.private_jwk(), "webhook": ws.private_jwk()}))
        return rs, ws

    def purge(self, idem_ttl):
        now = time.time()
        for k in [k for k, v in self.idem.items() if now - v["at"] > idem_ttl]:
            del self.idem[k]
        for qid in [q for q, v in self.quotes.items() if now - v["expires_ts"] > 24 * 3600]:
            del self.quotes[qid]


# ================================================================ server

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class ProviderServer:
    """The reference provider. Use as a context manager, or start()/stop(); `serve_forever()` for the CLI.

    api_keys: {key: {"client": name, "scopes": [...] | None (all), "account": "ok" | "needs_account" | "no_payment"}}.
    With no keys, auth is off and every caller is the client "local".

    provider_url / endpoint origin is THIS server's identity: receipts are signed with iss = provider_url.
    operator: {"name", "url"?} when this server is a shim run by someone other than the provider (default: taken from
    `fulfiller.operator` or `fulfiller.shim.operator`). The served card then keeps the provider's own provider_url and
    identity, gains protocol.operator (role "shim", url = this server's origin), and publishes this server's keys as
    protocol.operator.jwks_uri instead of jwks_uri (which must be the provider's own origin); receipts then verify
    against the operator's keys with iss = the operator's origin, and deliveries list the operator in relayed_by.
    cancel=None takes `fulfiller.can_cancel` (default True).
    """

    def __init__(self, fulfiller, *, host="127.0.0.1", port=8811, endpoint=None, provider_url=None, data_dir=None,
                 api_keys=None, quote_mode="optional", public_quotes=False, cancel=None, webhooks=True, operator=None,
                 quote_ttl=7200, idem_ttl=IDEMPOTENCY_TTL_S, rate_limit_per_min=None, unavailable=False,
                 allow_private_webhooks=False, webhook_ssl_context=None, webhook_retry_delays=(0.5, 2, 8),
                 tick=0.2, media_ttl=7 * 24 * 3600, signup_url=None, verbose=False):
        self.f = fulfiller
        self.host, self.port = host, port
        self._endpoint, self._provider_url = endpoint, provider_url
        self.store = Store(data_dir)
        self.receipt_key, self.webhook_key = self.store.load_keys()
        self.api_keys = dict(api_keys or {})
        if cancel is None:
            # a mounted shim (it has .shim) cancels nothing unless it says so; other fulfillers default to cancellable
            cancel = bool(getattr(fulfiller, "can_cancel", not hasattr(fulfiller, "shim")))
        self.quote_mode, self.public_quotes, self.cancel_enabled, self.webhooks = quote_mode, public_quotes, cancel, webhooks
        if operator is None:  # a mounted shim (AdapterFulfiller) discloses who runs it
            operator = getattr(fulfiller, "operator", None) or getattr(getattr(fulfiller, "shim", None), "operator", None)
        self.operator = dict(operator, role="shim") if operator else None
        if self.operator and not str(self.operator.get("url") or "").startswith("https://"):
            self.operator.pop("url", None)
        self.quote_ttl, self.idem_ttl, self.media_ttl = quote_ttl, idem_ttl, media_ttl
        self.rate_limit, self._hits = rate_limit_per_min, {}
        self.unavailable = unavailable
        self.allow_private_webhooks, self.webhook_ssl = allow_private_webhooks, webhook_ssl_context
        self.webhook_delays, self.webhook_log = tuple(webhook_retry_delays), []
        self.tick, self.verbose, self.signup_url = tick, verbose, signup_url
        self._httpd, self._threads, self._stop = None, [], threading.Event()
        if not M.HAVE_JSONSCHEMA:
            print("irlpls.protocol: jsonschema not installed; only top-level required members are checked", file=sys.stderr)

    # -- addresses
    @property
    def endpoint(self):
        return (self._endpoint or f"http://{self.host}:{self.port}{PREFIX}").rstrip("/")

    @property
    def provider_url(self):
        if self._provider_url:
            return self._provider_url.rstrip("/")
        u = urllib.parse.urlsplit(self.endpoint)
        return f"{u.scheme}://{u.netloc}"

    @property
    def lock(self):
        return self.store.lock

    def card(self):
        c = json.loads(json.dumps(self.f.card))
        keys_url = self.provider_url + "/.well-known/irlpls-keys.json"
        c["protocol"] = {"versions": list(M.SUPPORTED_VERSIONS), "endpoint": self.endpoint,
                         "quote": self.quote_mode, "public_quotes": self.public_quotes,
                         "webhooks": self.webhooks, "cancel": self.cancel_enabled}
        if self.operator:  # a shim: the provider's identity stays; the operator and its own keys are disclosed
            c["protocol"]["operator"] = dict(self.operator, url=self.operator.get("url") or self.provider_url, jwks_uri=keys_url)
        else:
            c["provider_url"] = self.provider_url
            c["protocol"]["jwks_uri"] = keys_url
        if self.api_keys:
            c["api"] = dict(c.get("api") or {}, auth=[{"type": "api_key", "header": "Authorization", "scheme": "Bearer"}])
        return c

    def jwks(self):
        return {"keys": [self.receipt_key.public_jwk(), self.webhook_key.public_jwk()]}

    @property
    def iss(self):
        """The receipt issuer: the RFC 6454 origin of provider_url (for a shim: of the operator's url)."""
        u = urllib.parse.urlsplit((self.operator or {}).get("url") or self.provider_url)
        port = f":{u.port}" if u.port and u.port != {"https": 443, "http": 80}.get(u.scheme) else ""
        return f"{u.scheme}://{(u.hostname or '').lower()}{port}"

    # -- lifecycle
    def start(self):
        app = self

        class Handler(_Handler):
            server_app = app
        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        for target in (self._httpd.serve_forever, self._ticker):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def serve_forever(self):
        self.start()
        try:
            while not self._stop.wait(3600):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _ticker(self):
        while not self._stop.wait(self.tick):
            try:
                with self.lock:
                    for rec in list(self.store.orders.values()):
                        if rec["state"] not in M.TERMINAL:
                            self._refresh(rec)
                    self.store.purge(self.idem_ttl)
            except Exception:  # noqa: BLE001 - keep ticking; log
                traceback.print_exc()

    # -- auth, limits
    def authenticate(self, headers, scope, allow_public=False):
        if not self.api_keys:
            return {"client": "local", "scopes": None, "account": "ok"}
        auth = headers.get("Authorization") or ""
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if not key:
            if allow_public:
                return {"client": "public", "scopes": None, "account": "ok"}
            raise Problem("unauthorized", "Send Authorization: Bearer <api key>.",
                          headers={"WWW-Authenticate": 'Bearer realm="irlpls"'})
        info = next((v for k, v in self.api_keys.items() if hmac.compare_digest(k.encode(), key.encode())), None)
        if info is None:
            raise Problem("unauthorized", "Unknown API key.",
                          headers={"WWW-Authenticate": 'Bearer realm="irlpls", error="invalid_token"'})
        if info.get("scopes") is not None and scope not in info["scopes"]:
            raise Problem("insufficient_scope", f"This key lacks {scope}.",
                          headers={"WWW-Authenticate": f'Bearer realm="irlpls", error="insufficient_scope", scope="{scope}"'})
        return {"client": info.get("client") or "key-" + hashlib.sha256(key.encode()).hexdigest()[:12],
                "scopes": info.get("scopes"), "account": info.get("account", "ok")}

    def rate_check(self, client):
        if not self.rate_limit:
            return
        now = time.time()
        with self.lock:
            hits = [t for t in self._hits.get(client, []) if now - t < 60]
            if len(hits) >= self.rate_limit:
                wait = max(1, math.ceil(60 - (now - hits[0])))
                raise Problem("rate_limited", f"At most {self.rate_limit} requests a minute.", headers={"Retry-After": str(wait)})
            hits.append(now)
            self._hits[client] = hits

    def _account_check(self, who, paid):
        if who["account"] == "needs_account":
            raise Problem("needs_account", "Link an account before quoting or ordering.",
                          continue_url=self.signup_url or self.provider_url + "/signup")
        if paid and who["account"] == "no_payment":
            raise Problem("payment_required", "Add a payment method before a paid order.",
                          continue_url=self.signup_url or self.provider_url + "/billing")

    # -- idempotency
    def idempotent(self, who, headers, method, path, body, required, fn):
        """Run fn() -> (status, headers, body) under the Idempotency-Key rules. Returns (status, headers, body)."""
        key = headers.get("Idempotency-Key")
        if key is None:
            if required:
                raise Problem("idempotency_key_required", "This operation needs an Idempotency-Key header.")
            return fn()
        if not _KEY_RE.match(key):
            raise Problem("invalid_request", "Idempotency-Key must be 1-255 printable ASCII characters, no spaces.",
                          errors=[{"path": "$", "code": "idempotency_key", "detail": "header Idempotency-Key"}])
        fp = sha256_hex(f"{method} {path}\n".encode() + jcs(body or {}))
        slot = (who["client"], key)   # one key space per client: reused on another method or path is a different request
        with self.lock:
            e = self.store.idem.get(slot)
            if e and time.time() - e["at"] > self.idem_ttl:
                e = None
            if e:
                if e["fp"] != fp:
                    raise Problem("idempotency_key_reused", "This Idempotency-Key was used with a different request.")
                if e["state"] == "pending":
                    raise Problem("request_in_progress", "The first request with this key is still running.",
                                  headers={"Retry-After": "1"})
                return e["status"], dict(e["headers"], **{"Idempotent-Replayed": "true"}), e["body"]
            self.store.idem[slot] = {"fp": fp, "state": "pending", "at": time.time()}
        try:
            try:
                status, hdrs, out = fn()
            except Problem as p:
                status, hdrs, out = p.status, dict(p.headers, **{"Content-Type": "application/problem+json"}), p.body(path)
        except BaseException:
            with self.lock:
                self.store.idem.pop(slot, None)
            raise
        with self.lock:
            if status >= 500 or status in _NOT_STORED:  # auth, payment, limits: the client may fix them and retry
                self.store.idem.pop(slot, None)
            else:
                self.store.idem[slot] = {"fp": fp, "state": "done", "at": time.time(), "status": status,
                                         "headers": hdrs, "body": out}
            self.store.save()
        return status, hdrs, out

    # -- terms checking
    def check_constraints(self, terms, constraints):
        c = constraints or {}
        if "max_total" in c:
            mt, cur = M.parse_money(c["max_total"], "constraints.max_total")
            if terms.total is None:
                pass  # indicative, no price known: the offer says so (no total); an order then needs a firm quote
            elif cur != terms.currency or terms.total > mt:
                raise Problem("over_max_total", f"The total is {terms.total} {terms.currency}.",
                              errors=[{"path": "$.constraints.max_total"}])
        if c.get("deliver_by"):
            want, have = M.parse_time(c["deliver_by"]), terms.deliver_by
            if want and have and have > want:
                raise Problem("too_slow", f"Earliest delivery is {M.iso(have)}.")
        missing = [r for r in c.get("rights_needed", [])
                   if not (terms.rights.get(r) is True if r == "buyer_owns_output" else terms.rights.get(r) == "allowed")]
        if missing:
            raise Problem("rights_unavailable", "Not granted: " + ", ".join(missing) + ".")
        short = [g for g, lv in (c.get("proof") or {}).items()
                 if M.LEVEL_RANK.get(terms.proof.get(g, "none"), 0) < M.LEVEL_RANK[lv]]
        if c.get("scene_nonce") and not terms.scene_nonce:
            short.append("scene_nonce")
        if short:
            raise Problem("proof_unavailable", "Cannot meet: " + ", ".join(short) + ".")

    @staticmethod
    def _check_terms(terms, request):
        """A quantity over MAX_QUANTITY, or a total that does not fit the money pattern, is refused (invalid_request)."""
        q = request.get("quantity")
        if isinstance(q, int) and q > MAX_QUANTITY or (terms.quantity or 0) > MAX_QUANTITY:
            raise Problem("invalid_request", f"quantity is at most {MAX_QUANTITY}.", errors=[{"path": "$.request.quantity"}])
        for name, v in (("total", terms.total), ("unit_price", terms.unit_price)):
            if v is not None:
                try:
                    M.parse_money(M.money(v, terms.currency), name)
                except (Problem, ValueError):
                    raise Problem("invalid_request", f"The {name} for this request does not fit a money amount.",
                                  errors=[{"path": "$.request.quantity"}]) from None

    def _offer(self, terms, qid, expires):
        o = {"id": "o_1", "provider": {"card_id": self.f.card["id"], "name": self.f.card.get("name"),
                                        "card_url": self.provider_url + "/.well-known/irlpls"},
             "route": terms.route, "firm": terms.firm and terms.total is not None, "quantity": terms.quantity,
             "expires_at": expires,
             "rights": terms.rights, "proof": terms.proof, "scene_nonce": terms.scene_nonce,
             "order_url": self.endpoint + "/orders"}
        if terms.total is not None:
            o["total"] = M.money(terms.total, terms.currency)
        if terms.unit_price is not None:
            o["unit_price"] = M.money(terms.unit_price, terms.currency)
        if terms.unit:
            o["unit"] = terms.unit
        if terms.deliver_by:
            o["deliver_by"] = M.iso(terms.deliver_by)
        if terms.terms_url:
            o["terms_url"] = terms.terms_url
        return o

    @staticmethod
    def _terms_dict(t):
        return {"route": t.route, "total": None if t.total is None else str(t.total), "unit_price": None if t.unit_price is None else str(t.unit_price),
                "currency": t.currency, "quantity": t.quantity, "unit": t.unit, "firm": t.firm and t.total is not None,
                "deliver_by": M.iso(t.deliver_by) if t.deliver_by else None, "rights": t.rights, "proof": t.proof,
                "scene_nonce": t.scene_nonce}

    # -- operations
    def op_quote(self, who, body):
        req, cons = body["request"], body.get("constraints") or {}
        self._account_check(who, paid=False)
        terms = self.f.quote(req, cons, who["client"])
        self._check_terms(terms, req)
        self.check_constraints(terms, cons)
        qid = M.new_id("q")
        now = M.now_dt()
        expires = M.iso(now + timedelta(seconds=self.quote_ttl))
        q = {"id": qid, "created_at": M.iso(now), "expires_at": expires, "request_sha256": request_sha256(req),
             "offers": [self._offer(terms, qid, expires)]}
        with self.lock:
            self.store.quotes[qid] = {"client": who["client"], "quote": q, "request": req, "constraints": cons,
                                      "terms": self._terms_dict(terms), "expires_ts": time.time() + self.quote_ttl}
            self.store.save()
        return 201, {"Location": f"{self.endpoint}/quotes/{qid}"}, q

    def get_quote(self, who, qid):
        with self.lock:
            rec = self.store.quotes.get(qid)
            if not rec or rec["client"] not in (who["client"], "public"):
                raise Problem("not_found", "No such quote.")
            return 200, {}, rec["quote"]

    def op_order(self, who, body, path):
        if "quote_id" in body:
            with self.lock:
                qrec = self.store.quotes.get(body["quote_id"])
                if not qrec or qrec["client"] not in (who["client"], "public"):
                    raise Problem("not_found", "No such quote.", quote_id=body["quote_id"])
                if body["offer_id"] != qrec["quote"]["offers"][0]["id"]:
                    raise Problem("not_found", "No such offer in that quote.", quote_id=body["quote_id"])
                if time.time() > qrec["expires_ts"]:
                    raise Problem("quote_expired", "Re-quote.", quote_id=body["quote_id"])
                t = qrec["terms"]
                req, req_hash = qrec["request"], qrec["quote"]["request_sha256"]
            if t["total"] is None or (not t["firm"] and Decimal(t["total"]) > 0):
                raise Problem("quote_required", "That offer is indicative; a firm quote is needed.", quote_id=body["quote_id"])
            total, currency, route, qty = Decimal(t["total"]), t["currency"], t["route"], t["quantity"]
        else:
            req, cons = body["request"], body["constraints"]
            terms = self.f.quote(req, cons, who["client"])
            self._check_terms(terms, req)
            self.check_constraints(terms, cons)
            if terms.total is None:
                raise Problem("quote_required", "No price is known for this request; a firm quote is needed.")
            if self.quote_mode == "required" and terms.total > 0:
                raise Problem("quote_required", "This provider needs a firm quote for paid orders: POST /quotes first.")
            total, currency, route, qty, req_hash = terms.total, terms.currency, terms.route, terms.quantity, request_sha256(req)
        self._account_check(who, paid=total > 0)
        hook = (body.get("webhook") or {}).get("url")
        if hook:
            if not self.webhooks:
                raise Problem("invalid_request", "This provider sends no webhooks.", errors=[{"path": "$.webhook"}])
            from irlpls.http import UnsafeURL, check_url
            try:
                check_url(hook, allow_private=self.allow_private_webhooks, allow_http=False)
            except UnsafeURL as e:
                raise Problem("invalid_request", f"Webhook URL refused: {e}", errors=[{"path": "$.webhook.url"}]) from e
        now = M.iso()
        oid = M.new_id("ord")
        rec = {"id": oid, "client": who["client"], "state": "submitted", "route": route, "quantity": qty,
               "request": req, "request_sha256": req_hash, "nonce": body.get("nonce"), "webhook_url": hook,
               "quote_id": body.get("quote_id"), "offer_id": body.get("offer_id"),
               "client_reference": body.get("client_reference"), "currency": currency,
               "total": M.money(total, currency), "created_at": now, "updated_at": now,
               "status_history": [{"at": now, "state": "submitted"}], "items": [], "fstate": {},
               "provider_status": None, "cancel_requested": False}
        with self.lock:
            self.store.orders[oid] = rec
        try:
            upd = self.f.start(rec)
        except Problem:
            with self.lock:
                self.store.orders.pop(oid, None)
            raise
        with self.lock:
            if upd:
                if upd.provider_status and not upd.state:
                    rec["status_history"][-1]["provider_status"] = upd.provider_status
                    rec["provider_status"] = upd.provider_status
                    upd.provider_status = None
                self._apply(rec, upd)
            self.store.save()
            view = self.order_view(rec, inline_delivery=rec["state"] == "delivered")
        loc = {"Location": f"{self.endpoint}/orders/{oid}"}
        if rec["state"] == "delivered":
            return 200, loc, view
        return 202, dict(loc, **{"Retry-After": str(self.f.retry_after(rec))}), view

    def op_list(self, who, query):
        refs = urllib.parse.parse_qs(query).get("client_reference")
        if not refs or len(refs) != 1:
            raise Problem("invalid_request", "GET /orders needs exactly one client_reference query parameter.",
                          errors=[{"path": "$.client_reference"}])
        with self.lock:
            recs = [r for r in self.store.orders.values() if r["client"] == who["client"] and r.get("client_reference") == refs[0]]
            recs.sort(key=lambda r: r["created_at"], reverse=True)
            return 200, {}, {"orders": [self.order_view(r) for r in recs[:50]]}

    def _get_order(self, who, oid):
        rec = self.store.orders.get(oid)
        if not rec or rec["client"] != who["client"]:
            raise Problem("not_found", "No such order.", order_id=oid)
        return rec

    def op_status(self, who, oid):
        with self.lock:
            rec = self._get_order(who, oid)
            self._refresh(rec)
            hdr = {} if rec["state"] in M.TERMINAL else {"Retry-After": str(self.f.retry_after(rec))}
            return 200, hdr, self.order_view(rec)

    def op_delivery(self, who, oid):
        with self.lock:
            rec = self._get_order(who, oid)
            self._refresh(rec)
            return 200, {}, self.delivery_view(rec)

    def op_cancel(self, who, oid):
        with self.lock:
            rec = self._get_order(who, oid)
            self._refresh(rec)
            if rec["state"] in M.TERMINAL:
                return 200, {}, self.order_view(rec)
            if not self.cancel_enabled:
                raise Problem("not_cancellable", "This provider does not cancel orders.", order_id=oid)
            r = self.f.cancel(rec)
            if r == "refused":
                raise Problem("not_cancellable", "Work can no longer be stopped.", order_id=oid)
            if r == "cancelled":
                self._apply(rec, Update(state="failed", provider_status="cancelled", reason_code="cancelled",
                                        reason_text="Cancelled by the buyer."))
            else:
                if not rec["cancel_requested"]:
                    rec["cancel_requested"] = True
                    rec["status_history"].append({"at": M.iso(), "state": rec["state"], "provider_status": "cancel_requested"})
                    rec["provider_status"] = "cancel_requested"
                    rec["updated_at"] = M.iso()
                self._refresh(rec)
            self.store.save()
            return 200, {}, self.order_view(rec)

    # -- state
    def _refresh(self, rec):
        if rec["state"] in M.TERMINAL:
            return
        updates = self.f.poll(rec) or []
        for upd in updates:
            self._apply(rec, upd)
        if updates:
            self.store.save()

    def _apply(self, rec, upd):
        """Apply one fulfiller Update. Terminal orders never change; status_history is append-only."""
        if rec["state"] in M.TERMINAL or upd is None:
            return
        now = M.iso()
        for ob in upd.observations:
            self._add_item(rec, ob)
        new = upd.state if upd.state in M.STATES and upd.state != "submitted" else None
        changed = (new and new != rec["state"]) or (upd.provider_status and upd.provider_status != rec.get("provider_status"))
        if upd.observations or changed:
            rec["updated_at"] = now
        if not changed:
            return
        entry = {"at": now, "state": new or rec["state"]}
        if upd.provider_status:
            entry["provider_status"] = rec["provider_status"] = upd.provider_status
        if new == "failed":
            rec["reason_code"] = entry["reason_code"] = upd.reason_code or "provider_error"
            if upd.reason_text:
                rec["reason_text"] = entry["reason_text"] = upd.reason_text
        rec["status_history"].append(entry)
        if new and new != rec["state"]:
            rec["state"] = new
            self._notify(rec)

    def _add_item(self, rec, ob):
        n = len(rec["items"]) + 1
        item = {"id": f"itm_{n}", "provider": self.f.card["id"], "kind": ob.kind, "phenomenon_time": ob.phenomenon_time,
                "result_time": M.iso()}
        for k in ("lat", "lon", "precision_m", "value", "unit", "label", "c2pa"):
            v = getattr(ob, k)
            if v is not None:
                item[k] = v
        item["sensor"] = {"kind": ob.sensor_kind}
        if ob.media is not None:
            token = secrets.token_urlsafe(24)
            exp = M.iso(M.now_dt() + timedelta(seconds=self.media_ttl))
            mt = ob.media_type if isinstance(ob.media_type, str) and _MEDIA_TYPE_RE.match(ob.media_type) else "application/octet-stream"
            self.store.put_media(token, ob.media, mt, exp)
            item.update(media_token=token, url_expires_at=exp, media_type=mt, bytes=len(ob.media), sha256=sha256_hex(ob.media))
            media = {"sha256": item["sha256"], "media_type": item["media_type"], "bytes": item["bytes"]}
        else:  # a value item: the receipt binds jcs({kind, value, unit, phenomenon_time}) of the item as delivered
            media = value_media(item)
        payload = {"iss": self.iss, "order_id": rec["id"], "item_id": item["id"],
                   "nonce": rec.get("nonce"), "request_sha256": rec["request_sha256"], "media": media,
                   "phenomenon_time": ob.phenomenon_time, "time_method": ob.time_method,
                   "lat": item.get("lat"), "lon": item.get("lon"), "precision_m": item.get("precision_m"),
                   "place_method": ob.place_method, "issued_at": M.iso()}
        payload = {k: v for k, v in payload.items() if v is not None or k == "phenomenon_time"}
        item["receipt"] = self.receipt_key.sign(payload)
        rec["items"].append(item)

    # -- views
    def order_view(self, rec, inline_delivery=False):
        base = f"{self.endpoint}/orders/{rec['id']}"
        links = {"self": base, "delivery": base + "/delivery"}
        if self.cancel_enabled and rec["state"] not in M.TERMINAL:
            links["cancel"] = base + "/cancel"
        o = {"id": rec["id"], "state": rec["state"], "card_id": self.f.card["id"], "route": rec["route"],
             "total": rec["total"], "request_sha256": rec["request_sha256"],
             "created_at": rec["created_at"], "updated_at": rec["updated_at"],
             "progress": {"ordered": rec["quantity"], "delivered": len(rec["items"])},
             "status_history": rec["status_history"], "links": links}
        for k in ("quote_id", "offer_id", "provider_status", "reason_code", "reason_text"):
            if rec.get(k):
                o[k] = rec[k]
        if inline_delivery:
            o["delivery"] = self.delivery_view(rec)
        return json.loads(json.dumps(o))

    def delivery_view(self, rec):
        items = []
        for it in rec["items"]:
            v = {k: x for k, x in it.items() if k != "media_token"}
            if "media_token" in it:
                v["url"] = f"{self.endpoint}/media/{it['media_token']}"
            items.append(v)
        out = {"order_id": rec["id"], "complete": rec["state"] == "delivered", "items": items,
               "provenance": self.provenance(rec)}
        if self.operator:
            out["relayed_by"] = [dict(self.operator)]
        return json.loads(json.dumps(out))

    def provenance(self, rec):
        stages = self.f.provenance(rec)
        card = self.f.card
        if not stages:
            ver = card.get("verification", {})
            stages = []
            for sid, shown, gs in M.STAGES:
                st = {"stage": sid, "shown_as": shown}
                if gs:
                    st["guarantees"] = {g: {"level": ver.get(g, {}).get("level", "none"), "basis": "card",
                                            **({"unknown": ver[g]["unknown"]} if ver.get(g, {}).get("unknown") else {})}
                                        for g in gs}
                elif sid == "custody":
                    st["unknown"] = "Served by the provider over TLS; hops are not logged."
                else:
                    st["evidence"] = [{"type": "receipt", "detail": f"Detached JWS on each item, key {self.receipt_key.kid} from jwks_uri."}]
                stages.append(st)
        out = {"card_id": card["id"], "card_url": self.provider_url + "/.well-known/irlpls", "stages": stages}
        if card.get("updated"):
            out["card_updated"] = card["updated"]
        return out

    # -- webhooks
    def _notify(self, rec):
        if not rec.get("webhook_url"):
            return
        body = json.dumps({"type": "order.updated", "timestamp": M.iso(),
                           "data": {"order_id": rec["id"], "state": rec["state"],
                                    "order_url": f"{self.endpoint}/orders/{rec['id']}"}}, separators=(",", ":")).encode()
        ev = {"id": M.new_id("msg"), "url": rec["webhook_url"], "body": body}
        threading.Thread(target=self._send_webhook, args=(ev,), daemon=True).start()

    def _send_webhook(self, ev):
        from irlpls.http import UnsafeURL, check_url
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=self.webhook_ssl or ssl.create_default_context()))
        for attempt, delay in enumerate((0,) + self.webhook_delays):
            if delay and self._stop.wait(delay):
                return
            try:
                check_url(ev["url"], allow_private=self.allow_private_webhooks, allow_http=False)
            except UnsafeURL as e:
                self.webhook_log.append({"id": ev["id"], "attempt": attempt, "error": str(e)})
                return
            hdrs = dict(self.webhook_key.headers(ev["id"], ev["body"]), **{"Content-Type": "application/json",
                                                                         "User-Agent": "irlpls-reference-provider/0.1"})
            req = urllib.request.Request(ev["url"], data=ev["body"], headers=hdrs, method="POST")
            try:
                with opener.open(req, timeout=10) as r:
                    ok = 200 <= r.status < 300
                    status = r.status
            except urllib.error.HTTPError as e:
                ok, status = False, e.code
            except (urllib.error.URLError, OSError) as e:
                ok, status = False, repr(e)
            self.webhook_log.append({"id": ev["id"], "attempt": attempt, "status": status})
            if ok:
                return


def _refuse_constant(c):
    raise ValueError(f"{c} is not JSON")


def _finite_float(s):
    """A JSON number with a fraction or exponent, refused when it overflows (1e400 would become inf)."""
    f = float(s)
    if not math.isfinite(f):
        raise ValueError("number out of range")
    return f


def _safe_int(s):
    """A JSON integer, refused outside the IEEE 754 safe range that RFC 8785 can canonicalise."""
    if len(s) > 20:
        raise ValueError("integer out of range")
    i = int(s)
    if abs(i) > 2 ** 53:
        raise ValueError("integer out of range")
    return i


_JSON_TYPES = ("application/json", "application/problem+json")


def accepts_json(accept):
    """True unless an Accept header (RFC 9110 12.5.1) excludes both application/json and application/problem+json.
    No header, or an empty one, accepts anything. For each type the most specific matching range decides, and q=0
    excludes; a type no range matches is excluded."""
    if accept is None or not accept.strip():
        return True
    ranges = []
    for part in accept.split(","):
        bits = [b.strip() for b in part.split(";")]
        mt = bits[0].lower()
        if "/" not in mt:
            continue
        q = 1.0
        for b in bits[1:]:
            k, _, v = b.partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(v.strip())
                except ValueError:
                    q = 0.0
                q = q if 0.0 <= q <= 1.0 else 0.0  # NaN, inf and out-of-range weights count as "not acceptable"
        ranges.append((mt, q))
    for t in _JSON_TYPES:
        best = None  # (specificity, q)
        for mt, q in ranges:
            spec = 3 if mt == t else 2 if mt == t.split("/")[0] + "/*" else 1 if mt == "*/*" else 0
            if spec and (best is None or spec > best[0]):
                best = (spec, q)
        if best and best[1] > 0:
            return True
    return False


def media_headers(media_type):
    """Headers for serving delivered media from the API origin (security finding F10): never sniffed, sandboxed,
    and anything but plain image/video/audio is a download, never rendered."""
    h = {"Content-Type": media_type, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
         "Content-Security-Policy": "default-src 'none'; sandbox"}
    if not media_type.startswith(_INLINE_MEDIA) or media_type in _INLINE_DENY:
        h["Content-Disposition"] = "attachment"
    return h


class _Handler(BaseHTTPRequestHandler):
    server_app = None
    protocol_version = "HTTP/1.1"
    server_version = "irlpls-reference-provider/0.1"

    def log_message(self, fmt, *args):
        if self.server_app.verbose:
            super().log_message(fmt, *args)

    def _send(self, status, headers, body):
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        hdrs = {"Content-Type": "application/json", "Cache-Control": "no-store", "Irlpls-Version": M.VERSION}
        hdrs.update(headers or {})
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _problem(self, p, path):
        self._send(p.status, dict(p.headers, **{"Content-Type": "application/problem+json"}), p.body(path))

    def _drain(self, limit):
        """Read and discard at most `limit` bytes, then close: never an unbounded read."""
        self.close_connection = True
        left = limit
        try:
            self.connection.settimeout(2)
            while left > 0:
                chunk = self.rfile.read1(min(65536, left)) if hasattr(self.rfile, "read1") else self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise Problem("length_required", "Send the body with a Content-Length; Transfer-Encoding is not accepted.")
        cl = self.headers.get("Content-Length")
        if cl is None or not _CLEN_RE.match(cl.strip()):
            if cl is None:
                return {}
            self._drain(16 * MAX_BODY)   # bounded and discarded, so the client can read the answer
            raise Problem("invalid_request", "Content-Length must be a non-negative integer.", errors=[{"path": "$"}])
        n = int(cl)
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if n and ctype and ctype != "application/json":
            self.close_connection = True
            raise Problem("unsupported_media_type", "Request bodies are application/json.")
        if n > MAX_BODY:
            self._drain(min(n, 16 * MAX_BODY))  # a moderate excess, so the client reads the 413 instead of a reset
            raise Problem("payload_too_large", f"Bodies are limited to {MAX_BODY} bytes.")
        raw = self.rfile.read(n) if n else b""
        if not raw.strip():
            return {}
        try:
            out = json.loads(raw, parse_constant=_refuse_constant, parse_float=_finite_float, parse_int=_safe_int)
        except (ValueError, UnicodeDecodeError) as e:
            raise Problem("invalid_request", f"Body is not JSON: {str(e)[:120]}", errors=[{"path": "$"}]) from e
        if not isinstance(out, dict):
            raise Problem("invalid_request", "Body must be a JSON object.", errors=[{"path": "$"}])
        return out

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def _route(self, method):
        app = self.server_app
        path = urllib.parse.urlsplit(self.path).path
        try:
            # every answer but delivered media is JSON (or a JSON problem): an Accept that excludes both is a 406,
            # sent as a problem anyway (RFC 9110 15.5.7 allows it) so the client can still read why
            if not re.match(rf"^{PREFIX}/media/", path) and not accepts_json(self.headers.get("Accept")):
                if method in ("POST", "PUT", "PATCH"):
                    self.close_connection = True  # the body is not read
                raise Problem("not_acceptable", "Responses are application/json, and problems application/problem+json; "
                              "send an Accept header that allows them (or none).")
            body = self._body() if method in ("POST", "PUT", "PATCH") else {}
            if path == "/.well-known/irlpls" and method == "GET":
                return self._send(200, {"Cache-Control": "public, max-age=300"}, app.card())
            if path == "/.well-known/irlpls-keys.json" and method == "GET":
                return self._send(200, {"Cache-Control": "public, max-age=300"}, app.jwks())
            m = re.match(rf"^{PREFIX}/media/([A-Za-z0-9_-]+)$", path)
            if m and method == "GET":
                with app.lock:
                    data, meta = app.store.get_media(m.group(1))
                if data is None or time.time() > (M.parse_time(meta["expires_at"]).timestamp()):
                    raise Problem("not_found", "No such media, or the link expired.")
                mt = meta["media_type"] if _MEDIA_TYPE_RE.match(str(meta.get("media_type"))) else "application/octet-stream"
                return self._send(200, media_headers(mt), data)
            routes = [
                ("POST", rf"^{PREFIX}/quotes$", "quote"),
                ("GET", rf"^{PREFIX}/quotes/([A-Za-z0-9_-]+)$", "get_quote"),
                ("POST", rf"^{PREFIX}/orders$", "order"),
                ("GET", rf"^{PREFIX}/orders$", "list"),
                ("GET", rf"^{PREFIX}/orders/([A-Za-z0-9_-]+)$", "status"),
                ("GET", rf"^{PREFIX}/orders/([A-Za-z0-9_-]+)/delivery$", "delivery"),
                ("POST", rf"^{PREFIX}/orders/([A-Za-z0-9_-]+)/cancel$", "cancel"),
            ]
            hit, allowed = None, []
            for meth, rx, name in routes:
                mm = re.match(rx, path)
                if mm:
                    allowed.append(meth)
                    if meth == method:
                        hit = (name, mm.groups())
            if not hit:
                if allowed:
                    raise Problem("method_not_allowed", f"Use {', '.join(allowed)}.", headers={"Allow": ", ".join(allowed)})
                raise Problem("not_found", "No such resource.")
            ver = self.headers.get("Irlpls-Version")
            if ver is not None and ver not in M.SUPPORTED_VERSIONS:
                raise Problem("unsupported_version", f"Irlpls-Version {ver[:40]!r} is not served.",
                              supported_versions=list(M.SUPPORTED_VERSIONS))
            name, args = hit
            scope = {"quote": SCOPE_QUOTE, "get_quote": SCOPE_QUOTE, "order": SCOPE_ORDER, "cancel": SCOPE_ORDER}.get(name, SCOPE_READ)
            who = app.authenticate(self.headers, scope, allow_public=app.public_quotes and name in ("quote", "get_quote"))
            app.rate_check(who["client"])
            if app.unavailable:
                raise Problem("provider_unavailable", "Down for maintenance.", headers={"Retry-After": "30"})
            if name == "quote":
                M.require_valid(body, "quote.json#/$defs/quote_request")
                res = app.idempotent(who, self.headers, method, path, body, False, lambda: app.op_quote(who, body))
            elif name == "get_quote":
                res = app.get_quote(who, args[0])
            elif name == "order":
                M.require_valid(body, "order.json")
                if "constraints" in body and "max_total" in body["constraints"]:
                    M.parse_money(body["constraints"]["max_total"], "constraints.max_total")
                res = app.idempotent(who, self.headers, method, path, body, True, lambda: app.op_order(who, body, path))
            elif name == "list":
                res = app.op_list(who, urllib.parse.urlsplit(self.path).query)
            elif name == "status":
                res = app.op_status(who, args[0])
            elif name == "delivery":
                res = app.op_delivery(who, args[0])
            else:
                if body and set(body) - {"reason"}:
                    raise Problem("invalid_request", "A cancel body may carry only `reason`.", errors=[{"path": "$"}])
                res = app.idempotent(who, self.headers, method, path, body, True, lambda: app.op_cancel(who, args[0]))
            status, hdrs, out = res
            return self._send(status, hdrs, out)
        except Problem as p:
            return self._problem(p, path)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return self._problem(Problem("provider_error", "Unexpected server error.", status=500), path)


# ================================================================ CLI

def _load_fulfiller(spec):
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls)()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m irlpls.protocol.server",
                                 description="Reference irlpls protocol provider (draft 2026-10-26). Localhost only by default.")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--endpoint", help="public https base URL to advertise, e.g. https://provider.example/irlpls")
    ap.add_argument("--data", help="directory for the file-backed store and signing keys (default: memory only)")
    ap.add_argument("--sim", action="store_true", help="use the simulated fulfiller")
    ap.add_argument("--sim-seconds", type=float, default=5.0, help="time for a simulated commission to complete")
    ap.add_argument("--sim-price", default="12.00", help="simulated price per photo (decimal string)")
    ap.add_argument("--fulfiller", help="module:Class implementing irlpls.protocol.server.Fulfiller")
    ap.add_argument("--api-key", action="append", default=[], help="accept this bearer key (repeatable); none = auth off")
    ap.add_argument("--quote-required", action="store_true", help="paid orders need a firm quote (protocol.quote=required)")
    ap.add_argument("--public-quotes", action="store_true", help="quotes need no credentials")
    ap.add_argument("--allow-remote", action="store_true", help="required to bind a non-loopback address")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1") and not a.allow_remote:
        ap.error("refusing to bind a non-loopback address without --allow-remote")
    if a.sim:
        f = SimFulfiller(seconds=a.sim_seconds, price=a.sim_price)
    elif a.fulfiller:
        f = _load_fulfiller(a.fulfiller)
    else:
        ap.error("pass --sim or --fulfiller module:Class")
    keys = {k: {"client": f"key{i}", "scopes": None, "account": "ok"} for i, k in enumerate(a.api_key)}
    srv = ProviderServer(f, host=a.host, port=a.port, endpoint=a.endpoint, data_dir=a.data, api_keys=keys,
                         quote_mode="required" if a.quote_required else "optional", public_quotes=a.public_quotes,
                         verbose=a.verbose)
    srv.start()
    print(f"irlpls reference provider on http://{a.host}:{srv.port}  (card: /.well-known/irlpls, endpoint {srv.endpoint})",
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
