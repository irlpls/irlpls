"""Shared helpers for the irlpls wire protocol: schema validation, money, problems, ids and times.

Schemas are the files in `schema/` and `schema/protocol/` (the contract; PROTOCOL-DRAFT.md). They are found next to a
source checkout, or under $IRLPLS_SCHEMA_DIR. Validation uses `jsonschema` when it is installed; without it only the
top-level `required` members are checked (a degraded mode the server logs once).
"""
import json
import os
import pathlib
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

VERSION = "2026-10-26"
SUPPORTED_VERSIONS = (VERSION,)
SCHEMA_BASE = "https://irlpls.com/2026-10-26/schemas/"
PROBLEM_BASE = f"https://irlpls.com/{VERSION}/problems/"

GUARANTEES = ("human", "application", "camera", "time", "location", "content", "corroboration")
LEVEL_RANK = {"none": 0, "claimed": 1, "evidenced": 2}
STAGES = (  # id, shown as, guarantees it covers (PROTOCOL-DRAFT 5.4)
    ("enrolment", "Real person", ("human",)),
    ("capture", "Captured locally", ("camera", "location")),
    ("device_attestation", "Genuine device", ("application",)),
    ("signing", "Sealed capture", ("camera", "location", "content")),
    ("timestamp", "Known timestamp", ("time",)),
    ("custody", "Tracked in transit", ()),
    ("appraisal", "Independently verified", ("corroboration",)),
    ("delivery", "Delivered to you", ()),
)
STATES = ("submitted", "in_flight", "delivered", "failed")
TERMINAL = ("delivered", "failed")

# code -> (HTTP status, title, resolution). STANDARD 14.9.
PROBLEMS = {
    "invalid_request": (400, "Invalid request", "recoverable"),
    "unsupported_version": (400, "Unsupported version", "recoverable"),
    "idempotency_key_required": (400, "Idempotency-Key required", "recoverable"),
    "unauthorized": (401, "Unauthorized", "recoverable"),
    "needs_account": (403, "An account is needed", "requires_buyer_input"),
    "insufficient_scope": (403, "Insufficient scope", "requires_buyer_input"),
    "terms": (403, "Not allowed by the provider's terms", "unrecoverable"),
    "payment_required": (402, "Payment required", "requires_buyer_input"),
    "not_found": (404, "Not found", "unrecoverable"),
    "method_not_allowed": (405, "Method not allowed", "unrecoverable"),
    "not_acceptable": (406, "Not acceptable", "unrecoverable"),
    "quote_expired": (410, "Quote expired", "recoverable"),
    "length_required": (411, "Content-Length required", "recoverable"),
    "payload_too_large": (413, "Body too large", "recoverable"),
    "unsupported_media_type": (415, "Unsupported media type", "recoverable"),
    "idempotency_key_reused": (422, "Idempotency-Key reused with a different request", "unrecoverable"),
    "quote_required": (422, "A firm quote is required", "recoverable"),
    "needs_input": (422, "More input needed", "requires_buyer_input"),
    "no_match": (422, "This provider does not capture this", "unrecoverable"),
    "outside_coverage": (422, "Place not covered", "unrecoverable"),
    "too_slow": (422, "Cannot meet the deadline", "recoverable"),
    "over_max_total": (422, "Over max_total", "requires_buyer_review"),
    "rights_unavailable": (422, "Rights not available", "requires_buyer_review"),
    "proof_unavailable": (422, "Proof level not available", "requires_buyer_review"),
    "person_targeting_refused": (422, "Person targeting refused", "unrecoverable"),
    "request_in_progress": (409, "Request in progress", "retry_later"),
    "not_cancellable": (409, "Too late to cancel", "unrecoverable"),
    "rate_limited": (429, "Too many requests", "retry_later"),
    "provider_error": (500, "Provider error", "retry_later"),
    "provider_unavailable": (503, "Provider unavailable", "retry_later"),
}


class Problem(Exception):
    """An RFC 9457 problem. `body()` is the application/problem+json object; `headers` go on the response."""

    def __init__(self, code, detail=None, *, status=None, title=None, headers=None, **ext):
        st, ti, res = PROBLEMS.get(code, (status or 400, code.replace("_", " ").capitalize(), "unrecoverable"))
        self.code, self.status, self.title, self.resolution = code, status or st, title or ti, res
        self.detail, self.ext, self.headers = detail, {k: v for k, v in ext.items() if v is not None}, dict(headers or {})
        super().__init__(f"{self.status} {code}: {detail or self.title}")

    def body(self, instance=None):
        typ = "policy" if self.code.startswith("policy:") else self.code
        out = {"type": PROBLEM_BASE + typ, "title": self.title, "status": self.status, "code": self.code,
               "resolution": self.resolution}
        if self.detail:
            out["detail"] = self.detail
        if instance:
            out["instance"] = instance
        out.update(self.ext)
        return out


# ---------------------------------------------------------------- time, ids, money

def now_dt():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now_dt()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def new_id(prefix, nbytes=12):
    """Unguessable id (>= 96 random bits)."""
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


_MONEY = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")


def parse_money(m, field="money"):
    """Strict money parse: {'amount': decimal string, 'currency': 'USD'} -> (Decimal, currency). Raises Problem."""
    if not isinstance(m, dict) or not isinstance(m.get("amount"), str) or not _MONEY.match(m["amount"]) \
            or not re.match(r"^[A-Z]{3}$", str(m.get("currency", ""))):
        raise Problem("invalid_request", f"{field} must be a decimal-string amount and an ISO 4217 currency",
                      errors=[{"path": f"$.{field}", "code": "money"}])
    try:
        return Decimal(m["amount"]), m["currency"]
    except InvalidOperation as e:  # pragma: no cover - the pattern already excludes this
        raise Problem("invalid_request", f"{field} is not a number") from e


def money(amount, currency="USD"):
    """Decimal-like -> money object; whole cents get two places ("12.00"), finer amounts keep their digits."""
    d = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    if not d.is_finite() or d < 0:
        raise ValueError("money must be finite and non-negative")
    d = d.quantize(Decimal("0.01")) if d.as_tuple().exponent >= -2 else d.normalize()
    return {"amount": format(d, "f"), "currency": currency}


# ---------------------------------------------------------------- schemas

def schema_dir():
    env = os.environ.get("IRLPLS_SCHEMA_DIR")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parents[2] / "schema"


_REGISTRY = None
_VALIDATORS = {}
try:  # optional dependency
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    HAVE_JSONSCHEMA = False


def _load_all():
    d = schema_dir()
    docs = {}
    for f in list(d.glob("*.schema.json")) + list((d / "protocol").glob("*.schema.json")):
        s = json.loads(f.read_text())
        docs[s["$id"]] = s
    return docs


def registry():
    global _REGISTRY
    if _REGISTRY is None:
        reg = Registry()
        for sid, s in _load_all().items():
            reg = reg.with_resource(sid, Resource.from_contents(s))
        _REGISTRY = reg
    return _REGISTRY


def _ref(target):
    return target if target.startswith("http") else SCHEMA_BASE + "protocol/" + target


def _jsonpath(parts):
    out = "$"
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(p)) else f"[{json.dumps(str(p))}]")
    return out


def _fallback_errors(instance, target):
    """Without jsonschema: top-level type and required members only."""
    if not isinstance(instance, dict):
        return [{"path": "$", "detail": "must be an object"}]
    docs = _load_all()
    sid, _, ptr = _ref(target).partition("#")
    node = docs.get(sid, {})
    for part in [p for p in ptr.split("/") if p]:
        node = node.get(part, {})
    if "$ref" in node and not ptr:
        sub = node["$ref"].lstrip("#/").split("/")
        for part in sub:
            node = node.get(part, {})
    return [{"path": f"$.{k}", "detail": "is required"} for k in node.get("required", []) if k not in instance]


def errors(instance, target):
    """Validate `instance` against a schema id ('order.json', 'quote.json#/$defs/quote_request', or a full URL).

    Returns a list of {"path": RFC 9535 JSONPath, "detail": message}; empty means valid.
    """
    if not HAVE_JSONSCHEMA:
        return _fallback_errors(instance, target)
    ref = _ref(target)
    v = _VALIDATORS.get(ref)
    if v is None:
        v = _VALIDATORS[ref] = Draft202012Validator({"$ref": ref}, registry=registry(),
                                                    format_checker=Draft202012Validator.FORMAT_CHECKER)
    out = []
    for e in sorted(v.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        out.append({"path": _jsonpath(list(e.absolute_path)), "detail": e.message[:300]})
    return out[:20]


def require_valid(instance, target):
    errs = errors(instance, target)
    if errs:
        raise Problem("invalid_request", "The body does not match the schema; see errors[].", errors=errs)
    return instance
