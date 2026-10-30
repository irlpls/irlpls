"""Receipts (STANDARD 6.1, PROTOCOL-DRAFT 5.4) and webhook signatures (PROTOCOL-DRAFT 7).

- `jcs(obj)`: RFC 8785 canonical JSON bytes (key order by UTF-16 code units, ECMAScript number form).
- `request_sha256(request)`: hex SHA-256 of jcs(request); quotes and receipts carry it.
- `value_media(item)`: the receipt `media` binding for a value item (no file): SHA-256 of jcs({kind, value, unit,
  phenomenon_time}), media_type application/json. `expected_media_sha256(item)` picks the right hash for any item.
- `ReceiptSigner`: ES256 key; `sign(payload)` returns `{"jws": "<hdr>..<sig>", "payload"}` (a detached compact JWS,
  RFC 7515 Appendix F, whose payload is jcs(payload)). The kid lives only in the protected header.
- `verify_receipt(receipt, jwks, ...)` (STANDARD 14.7): header must be an object with alg ES256 or EdDSA equal to the
  key's JWK alg, a kid and typ irlpls-receipt+jws; jwk/jku/x5u are ignored and crit is refused; the key comes only from
  the JWK Set the caller fetched (the card's jwks_uri, or operator.jwks_uri for a shim) and must say irlpls_use
  "receipt"; not_after is an RFC 3339 date-time; every required payload member must be there; the expectations (iss,
  nonce, request hash, order, item) and, given `item`, the media, time and place bindings are compared exactly.
- `WebhookSigner` / `verify_webhook`: Standard Webhooks `v1a` (Ed25519 over `id.timestamp.body`), key named by the
  `irlpls-webhook-kid` header and marked irlpls_use "webhook"; optional replay dedupe on webhook-id.

Needs the `cryptography` package for signing and verifying; `jcs` and `request_sha256` are stdlib only.
"""
import base64
import hashlib
import json
import math
import time
from datetime import datetime, timezone

RECEIPT_TYP = "irlpls-receipt+jws"
ALLOWED_ALGS = ("ES256", "EdDSA")
WEBHOOK_TOLERANCE_S = 300


class ReceiptError(ValueError):
    """A receipt or webhook failed verification. The message says which check."""


# ---------------------------------------------------------------- RFC 8785 (JCS)

def _num(x):
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        if abs(x) > 2 ** 53:
            raise ValueError("integer outside the IEEE 754 safe range cannot be canonicalised")
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN and Infinity are not JSON")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    mant, _, exp = repr(abs(x)).partition("e")
    ip, _, fp = mant.partition(".")
    digits = ip + fp
    n = len(ip) + (int(exp) if exp else 0)
    stripped = digits.lstrip("0")
    n -= len(digits) - len(stripped)
    s = stripped.rstrip("0") or "0"
    k = len(s)
    if k <= n <= 21:
        out = s + "0" * (n - k)
    elif 0 < n <= 21:
        out = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + s
    else:
        e = n - 1
        out = (s[0] + ("." + s[1:] if k > 1 else "")) + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def _jcs(o, out):
    if o is None:
        out.append("null")
    elif o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif isinstance(o, (int, float)):
        out.append(_num(o))
    elif isinstance(o, str):
        out.append(json.dumps(o, ensure_ascii=False))
    elif isinstance(o, (list, tuple)):
        out.append("[")
        for i, v in enumerate(o):
            if i:
                out.append(",")
            _jcs(v, out)
        out.append("]")
    elif isinstance(o, dict):
        out.append("{")
        for i, k in enumerate(sorted(o, key=lambda k: k.encode("utf-16-be"))):
            if not isinstance(k, str):
                raise TypeError("object keys must be strings")
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _jcs(o[k], out)
        out.append("}")
    else:
        raise TypeError(f"not JSON: {type(o).__name__}")


def jcs(obj):
    """RFC 8785 canonical JSON, as UTF-8 bytes."""
    out = []
    _jcs(obj, out)
    return "".join(out).encode("utf-8")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def request_sha256(request):
    return sha256_hex(jcs(request))


# ---------------------------------------------------------------- value items (PROTOCOL-DRAFT 5.4, STANDARD 14.7)

VALUE_MEDIA_TYPE = "application/json"
VALUE_FIELDS = ("kind", "value", "unit", "phenomenon_time")


def value_document(item):
    """The bytes a receipt binds for a value item (a reading with no media, i.e. an item without `sha256`): RFC 8785
    of exactly {"kind", "value", "unit", "phenomenon_time"} copied from the delivered item, each member always
    present and null when the item omits it. phenomenon_time is the observed-at time, bound as the string delivered.
    Other item members (id, label, lat/lon, sensor, receipt) are not in it; location and time are bound in the
    receipt payload itself."""
    get = item.get if isinstance(item, dict) else (lambda k: getattr(item, k, None))
    return jcs({k: get(k) for k in VALUE_FIELDS})


def value_media(item):
    """Receipt `media` member for a value item: {"sha256", "media_type": "application/json", "bytes"}."""
    doc = value_document(item)
    return {"sha256": sha256_hex(doc), "media_type": VALUE_MEDIA_TYPE, "bytes": len(doc)}


def expected_media_sha256(item):
    """What a receipt's media.sha256 must equal for this delivered item: its `sha256` for a media item, else the
    value-document hash."""
    return item["sha256"] if item.get("sha256") else value_media(item)["sha256"]


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _int_b64u(n, size):
    return b64u(n.to_bytes(size, "big"))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- keys

RECEIPT_USE, WEBHOOK_USE = "receipt", "webhook"
WEBHOOK_KID_HEADER = "irlpls-webhook-kid"
REQUIRED_PAYLOAD = ("iss", "order_id", "item_id", "nonce", "request_sha256", "media", "phenomenon_time", "issued_at")


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the optional dependency
        raise RuntimeError("receipts and webhook signatures need the 'cryptography' package") from e
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils
    return hashes, ec, ed25519, utils


def _kid(prefix):
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + b64u(hashlib.sha256(str(time.time_ns()).encode()).digest()[:4])


def ec_public_jwk(key, kid, use=RECEIPT_USE, not_after=None):
    """Public JWK for a P-256 key, with the irlpls members (irlpls_use, not_after when set)."""
    n = key.public_key().public_numbers() if hasattr(key, "public_key") else key.public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "kid": kid, "alg": "ES256", "use": "sig", "irlpls_use": use,
           "x": _int_b64u(n.x, 32), "y": _int_b64u(n.y, 32)}
    if not_after:
        jwk["not_after"] = not_after
    return jwk


def sign_es256(key, kid, payload):
    """Detached compact JWS (RFC 7515 App. F), ES256, header {alg, kid, typ}, over the RFC 8785 form of payload."""
    hashes, ec, _, utils = _crypto()
    header = b64u(jcs({"alg": "ES256", "kid": kid, "typ": RECEIPT_TYP}))
    signing_input = f"{header}.{b64u(jcs(payload))}".encode("ascii")
    r, s = utils.decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}..{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class ReceiptSigner:
    """An ES256 (P-256) signing key with a kid. `private_jwk()` round-trips through `from_jwk`. `not_after` is an
    RFC 3339 date-time (or None)."""

    def __init__(self, private_key, kid, not_after=None):
        self.key, self.kid, self.not_after = private_key, kid, not_after

    @classmethod
    def generate(cls, kid=None, not_after=None):
        _, ec, _, _ = _crypto()
        return cls(ec.generate_private_key(ec.SECP256R1()), kid or _kid("es"), not_after)

    @classmethod
    def from_jwk(cls, jwk):
        _, ec, _, _ = _crypto()
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" not in jwk:
            raise ValueError("need a private P-256 JWK")
        key = ec.derive_private_key(int.from_bytes(b64u_decode(jwk["d"]), "big"), ec.SECP256R1())
        return cls(key, jwk["kid"], jwk.get("not_after"))

    def public_jwk(self):
        return ec_public_jwk(self.key, self.kid, RECEIPT_USE, self.not_after)

    def private_jwk(self):
        return dict(self.public_jwk(), d=_int_b64u(self.key.private_numbers().private_value, 32))

    def sign(self, payload):
        """Return a receipt object: {"jws": "<protected>..<signature>", "payload"}."""
        return {"jws": sign_es256(self.key, self.kid, payload), "payload": payload}


class WebhookSigner:
    """An Ed25519 key for Standard Webhooks `v1a` signatures. Published in the same JWK Set (kty OKP, irlpls_use
    "webhook"), and named on every delivery by the irlpls-webhook-kid header."""

    def __init__(self, private_key, kid):
        self.key, self.kid = private_key, kid

    @classmethod
    def generate(cls, kid=None):
        _, _, ed25519, _ = _crypto()
        return cls(ed25519.Ed25519PrivateKey.generate(), kid or _kid("wh"))

    @classmethod
    def from_jwk(cls, jwk):
        _, _, ed25519, _ = _crypto()
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "d" not in jwk:
            raise ValueError("need a private Ed25519 JWK")
        return cls(ed25519.Ed25519PrivateKey.from_private_bytes(b64u_decode(jwk["d"])), jwk["kid"])

    def public_jwk(self):
        from cryptography.hazmat.primitives import serialization
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "kid": self.kid, "alg": "EdDSA", "use": "sig", "irlpls_use": WEBHOOK_USE,
                "x": b64u(raw)}

    def private_jwk(self):
        from cryptography.hazmat.primitives import serialization
        raw = self.key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        return dict(self.public_jwk(), d=b64u(raw))

    def headers(self, msg_id, body, timestamp=None):
        """Headers for `body` (bytes): webhook-id, webhook-timestamp, webhook-signature (Standard Webhooks) and
        irlpls-webhook-kid (which key in the JWK Set signed it)."""
        ts = str(int(timestamp if timestamp is not None else time.time()))
        sig = self.key.sign(f"{msg_id}.{ts}.".encode() + body)
        return {"webhook-id": msg_id, "webhook-timestamp": ts,
                "webhook-signature": "v1a," + base64.b64encode(sig).decode("ascii"), WEBHOOK_KID_HEADER: self.kid}


# ---------------------------------------------------------------- verification

def parse_rfc3339(s):
    """An RFC 3339 date-time as an aware datetime, or None. A bare date is not a date-time and gives None."""
    if not isinstance(s, str) or len(s) < 20 or "T" not in s.upper():
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def _public_key(jwk):
    """The public key for a JWK; ReceiptError for anything malformed or unsupported (never a crash)."""
    hashes, ec, ed25519, _ = _crypto()
    if not isinstance(jwk, dict):
        raise ReceiptError("key is not a JWK object")
    try:
        if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
            x, y = b64u_decode(jwk["x"]), b64u_decode(jwk["y"])
            if len(x) != 32 or len(y) != 32:
                raise ValueError("P-256 coordinates must be 32 bytes")
            return ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
        if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
            return ed25519.Ed25519PublicKey.from_public_bytes(b64u_decode(jwk["x"]))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ReceiptError(f"key {str(jwk.get('kid'))[:40]!r} is malformed: {type(e).__name__}") from None
    raise ReceiptError(f"unsupported key type {str(jwk.get('kty'))[:10]}/{str(jwk.get('crv'))[:10]}")


def _keys(jwks):
    keys = jwks.get("keys", []) if isinstance(jwks, dict) else (list(jwks) if isinstance(jwks, (list, tuple)) else [])
    return [k for k in keys if isinstance(k, dict)] if isinstance(keys, list) else []


def _find_key(jwks, kid, use):
    found = [k for k in _keys(jwks) if k.get("kid") == kid]
    if not found:
        raise ReceiptError(f"no key {str(kid)[:60]!r} in the key set")
    if len(found) > 1:
        raise ReceiptError(f"kid {str(kid)[:60]!r} appears more than once in the key set")
    k = found[0]
    if k.get("irlpls_use") != use:
        raise ReceiptError(f"key {str(kid)[:60]!r} is not a {use} key (irlpls_use {str(k.get('irlpls_use'))[:20]!r})")
    if k.get("alg") not in ALLOWED_ALGS:
        raise ReceiptError(f"key {str(kid)[:60]!r} has no allowed alg (ES256 or EdDSA)")
    return k


def _not_after(jwk, when, what):
    if "not_after" not in jwk:
        return
    na = parse_rfc3339(jwk["not_after"])
    if na is None:
        raise ReceiptError(f"key {jwk.get('kid')!r}: not_after is not an RFC 3339 date-time")
    if when is None or when > na:
        raise ReceiptError(f"{what} after the key's not_after ({jwk['not_after']})")


def _num_eq(a, b):
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
        and not isinstance(b, bool) and a == b


def check_item_bindings(payload, item):
    """The item-level bindings of STANDARD 14.7: item id, media (file hash, type and size, or the value document),
    phenomenon_time (the string as delivered) and lat / lon / precision_m (present in one exactly when present in the
    other, and equal). Raises ReceiptError naming the first that differs."""
    if not isinstance(item, dict):
        raise ReceiptError("item is not an object")
    if payload.get("item_id") != item.get("id"):
        raise ReceiptError("item_id does not match")
    media = payload.get("media") or {}
    if item.get("sha256"):
        if media.get("sha256") != item["sha256"]:
            raise ReceiptError("media hash does not match")
        if media.get("media_type") != item.get("media_type") or media.get("bytes") != item.get("bytes"):
            raise ReceiptError("media type or size does not match the item")
    else:
        try:
            want = value_media(item)
        except (ValueError, TypeError) as e:
            raise ReceiptError(f"value item cannot be canonicalised: {e}") from None
        if {k: media.get(k) for k in want} != want:
            raise ReceiptError("media hash does not match the value document")
    if payload.get("phenomenon_time") != item.get("phenomenon_time"):
        raise ReceiptError("phenomenon_time does not match the item")
    for k in ("lat", "lon", "precision_m"):
        if (k in payload) != (k in item and item[k] is not None) or (k in payload and not _num_eq(payload[k], item[k])):
            raise ReceiptError(f"{k} does not match the item")


def verify_receipt(receipt, jwks, *, iss=None, nonce=None, request_sha256=None, media_sha256=None, media_bytes=None,
                   order_id=None, item_id=None, item=None):
    """Verify a detached-JWS receipt against a JWK Set the caller fetched itself (the card's jwks_uri, or
    protocol.operator.jwks_uri for a shim's receipts).

    Raises ReceiptError on any failure; returns the payload on success. Expectations given are compared exactly:
    `iss` (the signer's RFC 6454 origin), `nonce` (the order's), `request_sha256` (the Order's), `order_id`, `item_id`,
    `media_sha256` or `media_bytes` (a downloaded file); `item` adds every item-level binding (check_item_bindings).
    A receipt proves who asserted what, not that it is true.
    """
    from cryptography.exceptions import InvalidSignature
    hashes, ec, ed25519, utils = _crypto()
    if not isinstance(receipt, dict):
        raise ReceiptError("malformed receipt: not an object")
    try:
        h, empty, sig = receipt["jws"].split(".")
        header = json.loads(b64u_decode(h))
        payload = receipt["payload"]
        raw = b64u_decode(sig)
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        raise ReceiptError(f"malformed receipt: {type(e).__name__}") from None
    if not isinstance(header, dict):
        raise ReceiptError("malformed receipt: the protected header is not a JSON object")
    if not isinstance(payload, dict):
        raise ReceiptError("malformed receipt: the payload is not a JSON object")
    if empty != "":
        raise ReceiptError("not a detached JWS (payload segment must be empty)")
    if "crit" in header:
        raise ReceiptError("crit header parameters are not supported")
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise ReceiptError(f"algorithm {str(alg)[:20]!r} not allowed")
    if header.get("typ") != RECEIPT_TYP:
        raise ReceiptError(f"typ must be {RECEIPT_TYP}")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ReceiptError("the protected header has no kid")
    # jwk / jku / x5u in the header are ignored on purpose: the key comes only from the set the caller fetched.
    jwk = _find_key(jwks, kid, RECEIPT_USE)
    if jwk["alg"] != alg:
        raise ReceiptError("the header alg differs from the key's JWK alg")
    key = _public_key(jwk)
    missing = [k for k in REQUIRED_PAYLOAD if k not in payload]
    media = payload.get("media")
    if not isinstance(media, dict) or not all(k in media for k in ("sha256", "media_type", "bytes")):
        missing.append("media.sha256/media_type/bytes")
    if missing:
        raise ReceiptError("payload lacks " + ", ".join(missing))
    try:
        signing_input = f"{h}.{b64u(jcs(payload))}".encode("ascii")
    except (ValueError, TypeError) as e:
        raise ReceiptError(f"payload cannot be canonicalised: {e}") from None
    try:
        if alg == "ES256":
            if len(raw) != 64 or not isinstance(key, ec.EllipticCurvePublicKey):
                raise ReceiptError("bad ES256 signature or key")
            der = utils.encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        else:
            if not isinstance(key, ed25519.Ed25519PublicKey):
                raise ReceiptError("EdDSA header with a non-Ed25519 key")
            key.verify(raw, signing_input)
    except InvalidSignature as e:
        raise ReceiptError("signature does not verify") from e
    _not_after(jwk, parse_rfc3339(payload.get("issued_at")), "receipt issued")
    expect = {"iss": iss, "nonce": nonce, "request_sha256": request_sha256, "order_id": order_id, "item_id": item_id}
    for k, v in expect.items():
        if v is not None and payload.get(k) != v:
            raise ReceiptError(f"{k} does not match")
    if media_bytes is not None:
        media_sha256 = sha256_hex(media_bytes)
    if media_sha256 is not None and media.get("sha256") != media_sha256:
        raise ReceiptError("media hash does not match")
    if item is not None:
        check_item_bindings(payload, item)
    return payload


def verify_webhook(headers, body, jwks, *, now=None, tolerance=WEBHOOK_TOLERANCE_S, seen=None):
    """Verify a Standard Webhooks `v1a` delivery over the raw `body` bytes. Raises ReceiptError; returns the parsed JSON.

    `headers` is any mapping (case-insensitive lookups are done here). The key is the one the irlpls-webhook-kid header
    names, taken only from `jwks`, and it must be an Ed25519 key with irlpls_use "webhook". The window is on the
    webhook-timestamp header. `seen` (a dict the caller keeps, webhook-id -> first-seen time) turns on replay dedupe: an
    id seen within 2 x tolerance is refused, and older entries are dropped. The caller still checks data.order_url equals
    {endpoint}/orders/{data.order_id} exactly, then re-reads the order before acting.
    """
    from cryptography.exceptions import InvalidSignature
    _crypto()
    h = {str(k).lower(): v for k, v in dict(headers).items()}
    msg_id, ts, sigs, kid = h.get("webhook-id"), h.get("webhook-timestamp"), h.get("webhook-signature", ""), h.get(WEBHOOK_KID_HEADER)
    if not msg_id or not ts or not sigs:
        raise ReceiptError("missing webhook headers")
    if not kid:
        raise ReceiptError(f"missing {WEBHOOK_KID_HEADER} header")
    try:
        ts_i = int(ts)
    except ValueError as e:
        raise ReceiptError("bad webhook-timestamp") from e
    now = time.time() if now is None else now
    if abs(now - ts_i) > tolerance:
        raise ReceiptError("webhook timestamp outside the tolerance window")
    jwk = _find_key(jwks, kid, WEBHOOK_USE)
    if jwk.get("alg") != "EdDSA":
        raise ReceiptError("webhook keys are EdDSA (Ed25519)")
    key = _public_key(jwk)
    _not_after(jwk, datetime.fromtimestamp(ts_i, timezone.utc), "webhook sent")
    signed = f"{msg_id}.{ts}.".encode() + body
    ok = False
    for part in str(sigs).split():
        ver, _, b64 = part.partition(",")
        if ver != "v1a":
            continue
        try:
            key.verify(base64.b64decode(b64, validate=True), signed)
            ok = True
            break
        except (InvalidSignature, ValueError):
            continue
    if not ok:
        raise ReceiptError("no webhook signature verifies")
    if seen is not None:
        for old in [i for i, t in list(seen.items()) if now - t > 2 * tolerance]:
            seen.pop(old, None)
        if msg_id in seen:
            raise ReceiptError("webhook-id already seen (replay)")
        seen[msg_id] = now
    try:
        return json.loads(body)
    except ValueError as e:
        raise ReceiptError("webhook body is not JSON") from e


def webhook_order_url_ok(event, endpoint):
    """True when the event's data.order_url is exactly {endpoint}/orders/{data.order_id} (STANDARD 14.8)."""
    d = (event or {}).get("data") if isinstance(event, dict) else None
    if not isinstance(d, dict) or not isinstance(d.get("order_id"), str) or not d["order_id"]:
        return False
    import urllib.parse
    return d.get("order_url") == f"{endpoint.rstrip('/')}/orders/{urllib.parse.quote(d['order_id'], safe='')}"
