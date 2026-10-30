"""Conformance checker for a provider's live Capture Card (docs/STANDARD.md, Sections 2.4 and 8) and, with
--protocol, its wire protocol endpoint (Section 14).

    python -m irlpls.conformance https://provider.example
    python -m irlpls.conformance provider.example --against skyfi
    python -m irlpls.conformance provider.example --ucp
    python -m irlpls.conformance provider.example --protocol [--token T] [--need "..."]
    python -m irlpls.conformance sandbox.provider.example --protocol --sandbox
    python -m irlpls.conformance <card id | card file | domain> --evidence [--tsa-trust FILE]

Card checks are read-only: one GET of https://<domain>/.well-known/irlpls, plus one GET of /.well-known/ucp with --ucp.
The card's api.docs_url and api.base_url are never called.

--protocol adds checks against the card's protocol.endpoint that have no side effects: version negotiation, a quote,
reading it back, and problem shapes for a bad version, a bad body and unknown ids. It NEVER places an order unless
--sandbox is given AND the card declares protocol.sandbox: true; then it also orders, replays the Idempotency-Key,
polls status, reads the delivery, checks media hashes, verifies receipts against jwks_uri and tries cancel.
Credentials (--token, --header) are sent only to the endpoint and are never printed. Exit code 0 only when there are
no fails.

--evidence fetches every evidence_url on the card (a registry id, a local file, or the card served at the domain) and
runs the verifier for its type (irlpls/evidence.py): verified, failed or not checkable, with reasons and the SHA-256 and
retrieval time of everything read. A failed check on an `evidenced` entry is a fail; nothing here changes a card's level.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from . import http as _http
from . import validate

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
CAPABILITY = "com.irlpls.capture.card"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url, timeout=20, allow_private=False, allow_http=False):
    """Default fetcher: one GET, redirects not followed, through irlpls.http.check_url first (https, public hosts after
    DNS, fail closed; a test opts out with allow_private/allow_http). Returns (status, headers with lowercase keys,
    body bytes)."""
    _http.check_url(url, allow_private=allow_private, allow_http=allow_http)
    req = urllib.request.Request(url, headers={"User-Agent": "irlpls-conformance/0.1 (+https://irlpls.com)",
                                               "Accept": "application/json", "Accept-Encoding": "gzip"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:  # size-capped, and gzip decompression capped too (no decompression bomb)
        body = _http._decode(_http._read_capped(r, _http.MAX_BYTES), r.headers, _http.MAX_BYTES)
        h = {k.lower(): v for k, v in r.headers.items()}
        return r.status if hasattr(r, "status") else r.code, h, body


def normalize(target):
    """(domain, card url) from 'provider.example', 'https://provider.example' or a full card URL."""
    t = target.strip()
    if "://" not in t:
        t = "https://" + t
    p = urllib.parse.urlparse(t)
    return p.netloc, f"https://{p.netloc}/.well-known/irlpls" if p.path in ("", "/", "/.well-known/irlpls") else t


def _max_age(cc):
    m = re.search(r"max-age\s*=\s*\"?(\d+)", cc or "", re.I)
    return int(m.group(1)) if m else None


def check(target, fetcher=fetch, against=None, ucp=False, today=None):
    """Run every check. Returns [(status, name, detail)]. `fetcher(url)` -> (status, headers, body); `against` is an
    audited card dict."""
    today = today or date.today()
    out = []
    add = lambda s, n, d="": out.append((s, n, d))
    domain, url = normalize(target)
    if target.strip().startswith("http://"):
        add(FAIL, "https", "cards are served over HTTPS only")
        return out
    try:
        status, headers, body = fetcher(url)
    except Exception as e:
        add(FAIL, "fetch", f"{url}: {e}")
        return out
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    if 300 <= status < 400:
        add(FAIL, "no redirects", f"{url} answered {status} to {headers.get('location', '?')}; the card must be served directly")
        return out
    if status != 200:
        add(FAIL, "fetch", f"{url} answered {status}")
        return out
    add(PASS, "fetch", f"200 from {url} with no redirect")
    ct = (headers.get("content-type") or "").split(";")[0].strip().lower()
    add(PASS if ct == "application/json" else FAIL, "content type", ct or "missing; must be application/json")
    cc = headers.get("cache-control")
    age = _max_age(cc)
    if not cc:
        add(FAIL, "cache-control", "missing; needs 'public, max-age=60' or more")
    elif not re.search(r"\bpublic\b", cc, re.I):
        add(FAIL, "cache-control", f"{cc!r} lacks 'public'")
    elif age is None:
        add(FAIL, "cache-control", f"{cc!r} has no max-age")
    elif age < 60:
        add(FAIL, "cache-control", f"max-age={age} is below the 60 second minimum")
    else:
        add(PASS, "cache-control", cc)
    add(PASS if headers.get("etag") else WARN, "etag", headers.get("etag") or "none; conditional requests would save brokers bandwidth")
    try:
        card = json.loads(body.decode("utf-8-sig"))
        if not isinstance(card, dict):
            raise ValueError("top level is not an object")
    except (ValueError, UnicodeDecodeError) as e:
        add(FAIL, "json", f"body does not parse as a JSON object: {e}")
        return out
    add(PASS, "json", "parses")
    ps = validate.problems(card, fetched_from=url)
    errs = [p for p in ps if p[0] == "error"]
    for sev, where, msg in ps:
        if where == "expires" and sev == "warn":
            continue  # reported by the dates check below
        add(FAIL if sev == "error" else WARN, f"validate {where}", msg)
    if not errs:
        add(PASS, "validate", "card passes irlpls.validate")
    try:
        upd, exp = date.fromisoformat(card["updated"]), date.fromisoformat(card["expires"])
    except (KeyError, ValueError, TypeError):
        add(FAIL, "expires", "updated and expires must both be present as YYYY-MM-DD dates")
    else:
        if exp > upd + timedelta(days=366):
            add(FAIL, "expires", f"{exp} is more than a year after updated {upd}")
        elif exp < today:
            add(FAIL, "expires", f"expired on {exp}; brokers MUST skip it")
        else:
            add(PASS, "expires", f"{exp}, {(exp - today).days} days left")
        if upd > today:
            add(WARN, "updated", f"{upd} is in the future")
    own = urllib.parse.urlparse(card.get("provider_url") or "").hostname or ""
    host = domain.split(":")[0]
    ok = _same_owner(host, own)
    if ok:
        add(PASS, "provider_url", f"{own} matches serving host {host}" + ("; earns provider-maintained" if card.get("status") == "provider-maintained" else ""))
    elif card.get("status") == "provider-maintained":
        add(FAIL, "provider_url", f"provider-maintained but provider_url host {own or 'missing'} is not {host}")
    else:
        add(WARN, "provider_url", f"{own or 'missing'} does not match {host}; the card stays unclaimed")
    api = card.get("api") or {}
    if api.get("docs_url") or api.get("base_url"):
        add(PASS, "api", "listed but not called (read-only discovery)")
    if against is not None:
        over = validate.overclaims(card, against)
        if over:
            for where, say, found in over:
                add(WARN, f"overclaim {where}", f"card says {say!r}, audited card has {found!r}")
        else:
            add(PASS, "overclaims", "nothing claimed beyond the audited card")
    if ucp:
        _check_ucp(fetcher, domain, url, add)
    return out


def _check_ucp(fetcher, domain, card_url, add):
    u = f"https://{domain}/.well-known/ucp"
    try:
        status, _, body = fetcher(u)
        if status != 200:
            add(WARN, "ucp", f"{u} answered {status}")
            return
        prof = json.loads(body.decode("utf-8-sig"))
    except Exception as e:
        add(WARN, "ucp", f"{u}: {e}")
        return
    caps = prof.get("capabilities") or prof.get("ucp", {}).get("capabilities") or []
    if isinstance(caps, dict):
        caps = [dict(v, name=k) if isinstance(v, dict) else {"name": k} for k, v in caps.items()]
    found = [c for c in caps if isinstance(c, dict) and c.get("name") == CAPABILITY]
    if not found:
        add(WARN, "ucp", f"profile does not list {CAPABILITY}")
    elif any((c.get("config") or {}).get("card") == card_url for c in found):
        add(PASS, "ucp", f"lists {CAPABILITY} with config.card = {card_url}")
    else:
        add(FAIL, "ucp", f"{CAPABILITY} config.card is {[(c.get('config') or {}).get('card') for c in found]}, not {card_url}")

# ---------------------------------------------------------------------------------------------------------------------
# Wire protocol checks (STANDARD Section 14)

PROTOCOL_VERSION = "2026-10-26"
SCHEMA_ROOT = pathlib.Path(__file__).resolve().parent.parent / "schema"
PSCHEMA = f"https://irlpls.com/{PROTOCOL_VERSION}/schemas/protocol/"
STATES = ("submitted", "in_flight", "delivered", "failed")
TERMINAL = ("delivered", "failed")
NEXT = {"submitted": {"submitted", "in_flight", "delivered", "failed"},
        "in_flight": {"in_flight", "delivered", "failed"}, "delivered": {"delivered"}, "failed": {"failed"}}
STAGE_GUARANTEES = {"enrolment": {"human"}, "capture": {"camera", "location"}, "device_attestation": {"application"},
                    "signing": {"camera", "location", "content"}, "timestamp": {"time"}, "custody": set(),
                    "appraisal": {"corroboration"}, "delivery": set()}
UA = "irlpls-conformance/0.2 (+https://irlpls.com)"


def transport(method, url, headers=None, body=None, timeout=20, max_bytes=_http.MAX_BYTES):
    """Default protocol transport: HTTPS only, public hosts only (irlpls.http.check_url), no redirects, size-capped.
    Returns (status, headers with lowercase keys, body bytes). `body` is bytes or None."""
    _http.check_url(url, allow_http=False)
    h = {"User-Agent": UA, "Accept": "application/json, application/problem+json", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        r = urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        data = _http._decode(_http._read_capped(r, max_bytes), r.headers, max_bytes)
        return (r.status if hasattr(r, "status") else r.code), {k.lower(): v for k, v in r.headers.items()}, data


def jcs(o):
    """RFC 8785 canonical JSON (UTF-8 bytes): the one implementation in irlpls.protocol.receipts."""
    from .protocol.receipts import jcs as _jcs
    return _jcs(o)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64ud(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _validator():
    """fn(ref, instance) -> [error strings], or None when jsonschema (with referencing) or the schemas are missing."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None
    files = list(SCHEMA_ROOT.glob("*.schema.json")) + list((SCHEMA_ROOT / "protocol").glob("*.schema.json"))
    if not any(f.parent.name == "protocol" for f in files):
        return None
    reg = Registry()
    for f in files:
        sch = json.loads(f.read_text())
        reg = reg.with_resource(sch["$id"], Resource.from_contents(sch))

    def run(ref, inst):
        v = Draft202012Validator({"$ref": ref if ref.startswith("http") else PSCHEMA + ref}, registry=reg,
                                 format_checker=Draft202012Validator.FORMAT_CHECKER)
        return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message[:160]}" for e in
                sorted(v.iter_errors(inst), key=lambda e: list(map(str, e.path)))]
    return run


def money(m):
    """Decimal from a protocol money object, or None when it is missing or not a valid decimal string."""
    try:
        a = m["amount"]
        if not isinstance(a, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?", a):
            return None
        return Decimal(a)
    except (KeyError, TypeError, InvalidOperation):
        return None


def _site(host):
    """Registrable domain from the vendored Public Suffix List (irlpls.psl); the host itself for IPs and single labels."""
    from . import psl
    h = (host or "").lower().rstrip(".")
    return psl.registrable_domain(h) or h


def _same_site(a, b):
    from . import psl
    return psl.same_site(a, b)


def _same_owner(host, domain):
    """A card served from `host` speaks for `domain` only when they are one host or "www." twins (irlpls.psl)."""
    from . import psl
    return psl.same_owner(host, domain)


def _origin(url):
    """RFC 6454 ASCII origin; the scheme's default port omitted."""
    u = urllib.parse.urlsplit(url or "")
    port = f":{u.port}" if u.port and u.port != {"https": 443, "http": 80}.get(u.scheme) else ""
    return f"{u.scheme}://{(u.hostname or '').lower()}{port}"


def _parse_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def verify_receipt(receipt, jwks, expect):
    """Check one receipt (STANDARD 14.7) with irlpls.protocol.receipts.verify_receipt. `expect` holds iss, order_id,
    item_id, nonce, request_sha256, media_sha256 and item (each optional; item adds the media, time and place
    bindings). Returns [(status, detail)]; any FAIL means the receipt must not be relied on."""
    out = []
    try:
        hdr = json.loads(b64ud(str((receipt or {}).get("jws", "")).split(".")[0]))
    except (ValueError, TypeError, AttributeError):
        hdr = None
    if isinstance(hdr, dict) and {"jwk", "jku", "x5u"} & set(hdr):
        out.append((WARN, "header carries jwk/jku/x5u; ignored, the key comes only from jwks_uri"))
    try:
        from .protocol import receipts as R
        R._crypto()
    except (ImportError, RuntimeError):
        return out + [(WARN, "cryptography is not installed; receipt not checked (pip install cryptography)")]
    kw = {k: expect[k] for k in ("iss", "order_id", "item_id", "nonce", "request_sha256", "media_sha256", "item")
          if expect.get(k) is not None}
    try:
        R.verify_receipt(receipt, jwks, **kw)
    except Exception as e:  # noqa: BLE001 - ReceiptError and anything malformed alike
        return out + [(FAIL, f"receipt does not verify: {str(e)[:200] or type(e).__name__}")]
    alg = hdr.get("alg") if isinstance(hdr, dict) else "?"
    return out + [(PASS, f"{alg} signature verifies with key {hdr.get('kid') if isinstance(hdr, dict) else '?'} over the "
                         "RFC 8785 payload; iss, order, item, nonce, request hash, media, time and place match")]


class _Probe:
    """One protocol run: holds the transport, headers, schema validator and the result list."""

    def __init__(self, transport, auth_headers, add, validator):
        self.t, self.auth, self.add, self.v = transport, dict(auth_headers or {}), add, validator

    def call(self, method, url, body=None, headers=None, version=PROTOCOL_VERSION, auth=True):
        h = {"Irlpls-Version": version} if version else {}
        if auth:
            h.update(self.auth)
        h.update(headers or {})
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        status, rh, raw = self.t(method, url, h, data)
        rh = {k.lower(): v for k, v in (rh or {}).items()}
        try:
            obj = json.loads(raw.decode("utf-8-sig")) if raw else None
        except (ValueError, UnicodeDecodeError):
            obj = None
        return status, rh, obj, raw

    def schema(self, name, ref, inst):
        if self.v is None:
            return True
        errs = self.v(ref, inst)
        if errs:
            self.add(FAIL, f"{name} schema", f"{ref}: " + "; ".join(errs[:4]))
            return False
        self.add(PASS, f"{name} schema", f"valid against {ref}")
        return True

    def success(self, name, status, rh, obj, want):
        if status not in want:
            return False
        ct = (rh.get("content-type") or "").split(";")[0].strip().lower()
        if ct != "application/json":
            self.add(FAIL, f"{name} content type", f"{ct or 'missing'}; successful responses are application/json")
        if not isinstance(obj, dict):
            self.add(FAIL, name, f"{status} but the body is not a JSON object")
            return False
        echo = rh.get("irlpls-version")
        if echo is None:
            self.add(WARN, f"{name} version echo", "no Irlpls-Version response header")
        elif echo != PROTOCOL_VERSION:
            self.add(FAIL, f"{name} version echo", f"echoed {echo!r}, sent {PROTOCOL_VERSION}")
        return True

    def problem(self, name, status, rh, obj, codes, statuses=None):
        """Check an error response is a well-formed problem with one of `codes`. Returns the problem or None."""
        ct = (rh.get("content-type") or "").split(";")[0].strip().lower()
        if not isinstance(obj, dict):
            self.add(FAIL, name, f"HTTP {status}: body is not a JSON problem object")
            return None
        bad = []
        if ct != "application/problem+json":
            bad.append(f"content type {ct or 'missing'}, not application/problem+json")
        if obj.get("status") != status:
            bad.append(f"problem status {obj.get('status')!r} differs from HTTP {status}")
        code = obj.get("code")
        tail = "policy" if str(code).startswith("policy:") else code
        if not re.fullmatch(r"https://irlpls\.com/\d{4}-\d{2}-\d{2}/problems/" + re.escape(str(tail)), str(obj.get("type", ""))):
            bad.append(f"type {obj.get('type')!r} is not https://irlpls.com/<version>/problems/{tail}")
        if self.v is not None:
            bad += self.v("problem.json", obj)[:3]
        if codes and code not in codes:
            bad.append(f"code {code!r}, expected {' or '.join(codes)}")
        if statuses and status not in statuses:
            bad.append(f"HTTP {status}, expected {' or '.join(map(str, statuses))}")
        if bad:
            self.add(FAIL, name, "; ".join(bad))
        else:
            self.add(PASS, name, f"{status} {code} problem")
        return obj


def default_quote_request(card, need=None, place=None, lat=None, lon=None, max_total=None):
    """A minimal, harmless quote request built from the card (or the caller's need)."""
    modes = (card.get("delivery") or {}).get("modes") or []
    tasks = (card.get("capabilities") or {}).get("tasks") or []
    req = {"need": need or ("conformance check: " + (tasks[0] if tasks else card.get("name", "observation"))),
           "quantity": 1, "mode": "live" if "live" in modes else "commission"}
    areas = [a for a in (card.get("coverage") or {}).get("areas") or [] if re.fullmatch(r"[A-Z]{2}", a)]
    if areas:
        req["country_code"] = areas[0]
    if place:
        req["place"] = place
    if lat is not None and lon is not None:
        req["lat"], req["lon"] = round(float(lat), 2), round(float(lon), 2)  # about 1 km: quotes carry coarse places
    body = {"request": req}
    if max_total is not None:
        body["constraints"] = {"max_total": {"amount": str(max_total), "currency": "USD"}}
    return body


def check_protocol(target, transport=transport, sandbox=False, auth_headers=None, quote_request=None, need=None,
                   place=None, lat=None, lon=None, max_total=None, wait=30, sleep=time.sleep, clock=time.monotonic):
    """Check a provider's protocol endpoint (STANDARD Section 14). Returns [(status, name, detail)].

    Never POSTs /orders or /cancel unless `sandbox` is True AND the card declares protocol.sandbox: true."""
    out = []
    add = lambda s, n, d="": out.append((s, n, d))
    v = _validator()
    if v is None:
        add(WARN, "schemas", "jsonschema/referencing or schema/protocol/ missing; message shapes not schema-checked")
    p = _Probe(transport, auth_headers, add, v)
    domain, card_url = normalize(target)
    if target.strip().startswith("http://"):
        add(FAIL, "https", "the protocol runs over HTTPS only")
        return out

    # discover
    try:
        status, rh, doc, _ = p.call("GET", card_url, auth=False, version=None)
    except Exception as e:  # noqa: BLE001
        add(FAIL, "discover", f"{card_url}: {e}")
        return out
    if status != 200 or not isinstance(doc, dict):
        add(FAIL, "discover", f"{card_url} answered {status}" + ("" if status != 200 else " with no JSON object"))
        return out
    host = domain.split(":")[0].lower()
    card = doc
    if isinstance(doc.get("cards"), list):
        cands = [c for c in doc["cards"] if isinstance(c, dict) and c.get("protocol")]
        own = [c for c in cands if _same_owner(host, urllib.parse.urlsplit(c.get("provider_url") or "").hostname)]
        card = (own or cands or [{}])[0]
        add(PASS if card else FAIL, "discover", f"{len(doc['cards'])} cards at {card_url}" + (f"; checking {card.get('id')}" if card else "; none has a protocol block"))
        if not card:
            return out
    else:
        add(PASS, "discover", f"card {card.get('id')!r} from {card_url}")
    proto = card.get("protocol")
    if not isinstance(proto, dict):
        add(FAIL, "protocol block", "card has no protocol block: a Card-only provider (STANDARD 14.13); nothing to order from")
        return out
    p.schema("protocol block", "common.json#/$defs/protocol_block", proto)
    ep = proto.get("endpoint") or ""
    own_host = (urllib.parse.urlsplit(card.get("provider_url") or "").hostname or "").lower()
    ep_host = (urllib.parse.urlsplit(ep).hostname or "").lower()
    if not ep.startswith("https://") or not ep_host:
        add(FAIL, "endpoint", f"{ep!r} is not an https URL; nothing else is called")
        return out
    if ep.endswith("/"):
        add(FAIL, "endpoint", f"{ep} has a trailing slash")
        ep = ep.rstrip("/")
    if not own_host or not _same_site(ep_host, own_host):
        add(FAIL, "endpoint", f"{ep_host} is not on the registrable domain of provider_url ({own_host or 'missing'}); refusing to call it")
        return out
    add(PASS, "endpoint", f"{ep} on {_site(own_host)}")
    if not _same_owner(host, own_host):
        add(WARN, "discover", f"card fetched from {host} names provider_url host {own_host}: unclaimed unless you accept it")
    vers = proto.get("versions") or []
    add(PASS if PROTOCOL_VERSION in vers else FAIL, "versions", f"{vers}" + ("" if PROTOCOL_VERSION in vers else f"; this checker speaks {PROTOCOL_VERSION}"))
    jwks_uri = proto.get("jwks_uri")
    if proto.get("webhooks") is True and not (jwks_uri or (proto.get("operator") or {}).get("jwks_uri")):
        add(FAIL, "jwks_uri", "webhooks is true but the card publishes no jwks_uri to verify them with")
    if jwks_uri:
        add(PASS if _origin(jwks_uri) == _origin(card.get("provider_url")) else FAIL, "jwks_uri",
            f"{jwks_uri}" + ("" if _origin(jwks_uri) == _origin(card.get("provider_url")) else f" is not same-origin with {_origin(card.get('provider_url'))}"))
    if proto.get("operator"):
        add(WARN, "operator", f"endpoint run by {(proto['operator'] or {}).get('name')!r} ({(proto['operator'] or {}).get('role')}), not the provider")
    if PROTOCOL_VERSION not in vers:
        return out

    # problem shapes with no side effects
    quotes = ep + "/quotes"
    s, rh, o, _ = p.call("POST", quotes, {"request": {"need": "conformance check"}}, version="1900-01-01")
    anon = not p.auth
    pr = p.problem("unsupported version", s, rh, o, ["unsupported_version"] + (["unauthorized"] if anon else []), [400] + ([401] if anon else []))
    if pr is not None and pr.get("code") == "unsupported_version" and PROTOCOL_VERSION not in (pr.get("supported_versions") or []):
        add(WARN, "unsupported version", f"supported_versions {pr.get('supported_versions')!r} does not list {PROTOCOL_VERSION}")
    s, rh, o, _ = p.call("POST", quotes, {"request": {"quantity": "many"}})
    if s == 401 and not p.auth:
        p.problem("invalid quote body", s, rh, o, ["unauthorized"], [401])
    else:
        pr = p.problem("invalid quote body", s, rh, o, ["invalid_request"], [400])
        if pr is not None and not pr.get("errors"):
            add(WARN, "invalid quote body", "no errors[] with JSONPath paths")
    ghost = "conformance-" + secrets.token_hex(8)
    for name, url in (("unknown quote", f"{quotes}/{ghost}"), ("unknown order", f"{ep}/orders/{ghost}")):
        s, rh, o, _ = p.call("GET", url)
        p.problem(name, s, rh, o, ["not_found", "unauthorized"] if not p.auth else ["not_found"], [404, 401] if not p.auth else [404])

    # quote
    qreq = quote_request or default_quote_request(card, need, place, lat, lon, max_total)
    s, rh, quote, _ = p.call("POST", quotes, qreq, headers={"Idempotency-Key": str(uuid.uuid4())})
    if not p.success("quote", s, rh, quote, (201, 200)):
        if s == 401 and not proto.get("public_quotes"):
            p.problem("quote", s, rh, quote, ["unauthorized"], [401])
            if not rh.get("www-authenticate"):
                add(WARN, "quote", "401 without a WWW-Authenticate header (RFC 6750, RFC 9728)")
            add(SKIP, "order", "quotes need credentials (public_quotes is not true); pass --token or --header")
            return out
        if s in (200, 201):
            return out
        pr = p.problem("quote", s, rh, quote, None)
        if pr is not None and pr.get("code") == "needs_account" and not pr.get("continue_url"):
            add(FAIL, "quote", "needs_account without continue_url")
        add(SKIP, "order", f"no offer to order (quote answered {s} {(pr or {}).get('code')}); try --need/--place for a request it serves")
        return out
    add(PASS if s == 201 else WARN, "quote status", f"{s}" + ("" if s == 201 else "; a new quote is 201 Created"))
    p.schema("quote", "quote.json", quote)
    loc = rh.get("location")
    want_loc = f"{quotes}/{quote.get('id')}"
    if not loc:
        add(WARN, "quote location", "no Location header")
    elif urllib.parse.urljoin(quotes, loc) != want_loc:
        add(WARN, "quote location", f"{loc}, expected {want_loc}")
    try:
        want_hash = hashlib.sha256(jcs(qreq["request"])).hexdigest()
        ok = quote.get("request_sha256") == want_hash
        add(PASS if ok else FAIL, "request_sha256", "matches the RFC 8785 form of the request" if ok else f"{quote.get('request_sha256')!r}, expected {want_hash}")
    except (TypeError, ValueError) as e:
        add(WARN, "request_sha256", f"could not canonicalise the request: {e}")
    offers = quote.get("offers") if isinstance(quote.get("offers"), list) else []
    if len(offers) != 1:
        add(FAIL, "offers", f"{len(offers)} offers; a provider returns exactly one")
    if not offers:
        add(SKIP, "order", "no offer")
        return out
    offer = offers[0]
    ok = offer.get("order_url") == ep + "/orders"
    add(PASS if ok else FAIL, "order_url", f"{offer.get('order_url')!r}" + ("" if ok else f" is not {ep}/orders; a client must never order there"))
    if (offer.get("provider") or {}).get("card_id") not in (None, card.get("id")):
        add(WARN, "offer provider", f"card_id {(offer.get('provider') or {}).get('card_id')!r} is not {card.get('id')!r}")
    total = money(offer.get("total")) if offer.get("total") is not None else None
    if offer.get("total") is not None and total is None:
        add(FAIL, "offer total", f"{offer.get('total')!r} is not a decimal-string money value")
    cap = money(((qreq.get("constraints") or {}).get("max_total")) or {})
    if cap is not None and total is not None:
        add(PASS if total <= cap else FAIL, "offer total", f"{total} against max_total {cap}")
    if offer.get("firm"):
        exp = _parse_time(offer.get("expires_at") or quote.get("expires_at"))
        if not exp or exp <= datetime.now(timezone.utc):
            add(FAIL, "offer expiry", f"firm offer expires_at {offer.get('expires_at')!r} is missing or past")
    s, rh, back, _ = p.call("GET", want_loc)
    if p.success("read quote", s, rh, back, (200,)):
        add(PASS if back.get("id") == quote.get("id") else FAIL, "read quote", f"GET {want_loc} returns quote {back.get('id')!r}")
    else:
        p.problem("read quote", s, rh, back, None)
    if not ok:
        add(SKIP, "order", "order_url is wrong; no order is attempted")
        return out

    # order: sandbox only
    order_body = {"quote_id": quote.get("id"), "offer_id": offer.get("id"), "nonce": b64u(secrets.token_bytes(16)),
                  "client_reference": "irlpls-conformance"}
    if not sandbox:
        add(SKIP, "order", f"dry run: would POST {ep}/orders with offer {offer.get('id')!r}; nothing was ordered "
                           "(pass --sandbox against an endpoint whose card declares protocol.sandbox: true)")
        return out
    if proto.get("sandbox") is not True:
        add(WARN, "sandbox", "--sandbox was given but the card does not declare protocol.sandbox: true; no order placed")
        return out
    add(PASS, "sandbox", "card declares protocol.sandbox: true; placing a test order")
    orders = ep + "/orders"
    s, rh, o, _ = p.call("POST", orders, order_body)
    if 200 <= s < 300:
        add(FAIL, "idempotency key required", f"POST /orders without Idempotency-Key answered {s}: it may have placed an order")
    else:
        p.problem("idempotency key required", s, rh, o, ["idempotency_key_required"], [400])
    key = str(uuid.uuid4())
    s, rh, order, _ = p.call("POST", orders, order_body, headers={"Idempotency-Key": key})
    if not p.success("order", s, rh, order, (200, 201, 202)):
        p.problem("order", s, rh, order, None)
        return out
    p.schema("order", "status.json", order)
    if s == 200 and order.get("state") != "delivered":
        add(FAIL, "order status", f"200 for a {order.get('state')!r} order; a new order is 202 unless already delivered")
    elif s != 202 and order.get("state") != "delivered":
        add(WARN, "order status", f"{s}; a new order is 202 Accepted")
    else:
        add(PASS, "order status", f"{s} {order.get('state')}")
    oid = order.get("id")
    want_rh = quote.get("request_sha256")
    add(PASS if order.get("request_sha256") == want_rh else FAIL, "order request_sha256",
        "equals the accepted quote's" if order.get("request_sha256") == want_rh
        else f"{order.get('request_sha256')!r}, the quote said {want_rh!r}")
    if s == 202 and not rh.get("location"):
        add(WARN, "order location", "202 without a Location header")
    charged, offered = money(order.get("total") or {}), total
    if order.get("total") is not None and charged is None:
        add(FAIL, "order total", f"{order.get('total')!r} is not a decimal-string money value")
    elif charged is not None and offered is not None:
        add(PASS if charged <= offered else FAIL, "order total", f"charged {charged}, offered {offered}")
    s2, rh2, again, _ = p.call("POST", orders, order_body, headers={"Idempotency-Key": key})
    if 200 <= s2 < 300 and isinstance(again, dict) and again.get("id") == oid:
        add(PASS, "idempotent replay", f"same key and body returned order {oid} again")
    else:
        add(FAIL, "idempotent replay", f"replay answered {s2} with order {(again or {}).get('id')!r}, not {oid!r}: a retry could double-order")
    s3, rh3, o3, _ = p.call("POST", orders, dict(order_body, nonce=b64u(secrets.token_bytes(16))), headers={"Idempotency-Key": key})
    if 200 <= s3 < 300:
        add(FAIL, "idempotency key reuse", f"same key, different body answered {s3}")
    else:
        p.problem("idempotency key reuse", s3, rh3, o3, ["idempotency_key_reused"], [422])

    # status
    self_url = (order.get("links") or {}).get("self") or f"{orders}/{oid}"
    if _origin(self_url) != _origin(ep) or not self_url.startswith(ep + "/"):
        add(FAIL, "order links", f"links.self {self_url!r} is not under {ep}; using {orders}/{oid}")
        self_url = f"{orders}/{oid}"
    state, hist, deadline, polls = order.get("state"), order.get("status_history") or [], clock() + wait, 0
    bad_transition = None
    while True:
        s, rh, cur, _ = p.call("GET", self_url)
        polls += 1
        if not p.success("status" if polls == 1 else f"status poll {polls}", s, rh, cur, (200,)):
            p.problem("status", s, rh, cur, None)
            return out
        if polls == 1:
            p.schema("status", "status.json", cur)
        elif v is not None and v("status.json", cur):
            p.schema(f"status poll {polls}", "status.json", cur)
        new = cur.get("state")
        if new not in STATES or state not in STATES or new not in NEXT[state]:
            bad_transition = bad_transition or f"{state!r} -> {new!r}"
        h2 = cur.get("status_history") or []
        if h2[:len(hist)] != hist:
            add(FAIL, "status_history", "not append-only: earlier entries changed")
        state, hist, order = new, h2, cur
        if state in TERMINAL or clock() >= deadline:
            break
        try:
            ra = float(rh.get("retry-after") or 2)
        except ValueError:
            ra = 2
        sleep(max(0.0, min(ra, deadline - clock())))
    add(FAIL if bad_transition else PASS, "state transitions", bad_transition or f"{polls} poll(s); ended {state}")
    if state not in TERMINAL:
        add(WARN, "status", f"still {state} after {wait}s; delivery checked as partial")
    if state == "failed":
        add(PASS if order.get("reason_code") else FAIL, "failed reason", order.get("reason_code") or "failed without reason_code")
    if state == "delivered" and not (order.get("links") or {}).get("delivery"):
        add(FAIL, "order links", "delivered without links.delivery")

    # deliver
    if state != "failed":
        dl = (order.get("links") or {}).get("delivery") or f"{orders}/{oid}/delivery"
        if not dl.startswith(ep + "/"):
            add(FAIL, "delivery link", f"{dl!r} is not under {ep}")
            dl = f"{orders}/{oid}/delivery"
        s, rh, dv, _ = p.call("GET", dl)
        if not p.success("deliver", s, rh, dv, (200,)):
            p.problem("deliver", s, rh, dv, None)
        else:
            _check_delivery(p, add, dv, oid, state, card, proto, order_body["nonce"], quote.get("request_sha256"))

    # cancel
    if proto.get("cancel"):
        cu = (order.get("links") or {}).get("cancel") or f"{orders}/{oid}/cancel"
        if not cu.startswith(ep + "/"):
            cu = f"{orders}/{oid}/cancel"
        s, rh, o, _ = p.call("POST", cu, {})
        if 200 <= s < 300:
            add(FAIL, "cancel key required", f"cancel without Idempotency-Key answered {s}")
        else:
            p.problem("cancel key required", s, rh, o, ["idempotency_key_required"], [400])
        s, rh, c, _ = p.call("POST", cu, {}, headers={"Idempotency-Key": str(uuid.uuid4())})
        if p.success("cancel", s, rh, c, (200,)):
            p.schema("cancel", "status.json", c)
            if state in TERMINAL and c.get("state") != state:
                add(FAIL, "cancel", f"cancelling a {state} order changed it to {c.get('state')!r}; terminal states never change")
            else:
                add(PASS, "cancel", f"200, order {c.get('state')}")
        else:
            p.problem("cancel", s, rh, c, ["not_cancellable"], [409])
    else:
        add(SKIP, "cancel", "card says protocol.cancel is not true")
    return out


def _expected_media(it):
    """media.sha256 a receipt must carry: the item's sha256, or the value-item binding (PROTOCOL-DRAFT 5.4)."""
    from .protocol.receipts import expected_media_sha256
    try:
        return expected_media_sha256(it)
    except (ValueError, TypeError):
        return "<not canonicalisable>"


def _check_delivery(p, add, dv, oid, state, card, proto, nonce, request_sha256):
    p.schema("delivery", "delivery.json", dv)
    if dv.get("order_id") != oid:
        add(FAIL, "delivery order_id", f"{dv.get('order_id')!r} is not {oid!r}")
    if state == "delivered" and dv.get("complete") is not True:
        add(FAIL, "delivery complete", "order is delivered but the delivery says complete: false")
    elif state != "delivered" and dv.get("complete") is True:
        add(WARN, "delivery complete", f"complete: true while the order is {state}")
    stages = ((dv.get("provenance") or {}).get("stages")) or []
    wrong = [f"{st.get('stage')}:{g}" for st in stages if isinstance(st, dict)
             for g in (st.get("guarantees") or {}) if g not in STAGE_GUARANTEES.get(st.get("stage"), set())]
    ids = [st.get("stage") for st in stages if isinstance(st, dict)]
    if wrong:
        add(FAIL, "provenance", f"guarantees on the wrong stage: {', '.join(wrong)}")
    elif len(set(ids)) != len(ids):
        add(FAIL, "provenance", f"a stage appears twice: {ids}")
    else:
        add(PASS, "provenance", f"{len(ids)} stage(s), each guarantee on its own stage")
    levels = {k: (v or {}).get("level") for k, v in (card.get("verification") or {}).items()}
    rank = {"none": 0, "claimed": 1, "evidenced": 2}
    over = [f"{st.get('stage')}:{g}" for st in stages if isinstance(st, dict)
            for g, e in (st.get("guarantees") or {}).items()
            if isinstance(e, dict) and e.get("basis") == "card" and rank.get(e.get("level"), 0) > rank.get(levels.get(g), 0)]
    if over:
        add(FAIL, "provenance basis", f"basis 'card' above the card's own level: {', '.join(over)}")
    items = dv.get("items") if isinstance(dv.get("items"), list) else []
    ids = [it.get("id") for it in items if isinstance(it, dict)]
    if len(set(map(str, ids))) != len(ids):
        add(FAIL, "delivery items", "an item id repeats; ids are unique within an order")
    jwks, jwks_err = None, None
    op = proto.get("operator") if isinstance(proto.get("operator"), dict) else None
    iss = _origin(op.get("url")) if op and op.get("url") else _origin(card.get("provider_url"))
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        if it.get("url") and it.get("sha256"):
            try:
                s, _, raw = p.t("GET", it["url"], {}, None)
                if s != 200:
                    add(WARN, f"media {iid}", f"GET answered {s}; hash not checked")
                else:
                    got = hashlib.sha256(raw).hexdigest()
                    add(PASS if got == it["sha256"] else FAIL, f"media {iid}",
                        "sha256 matches the download" if got == it["sha256"] else f"sha256 mismatch ({got}); a failed item")
                    if isinstance(it.get("bytes"), int) and it["bytes"] != len(raw):
                        add(WARN, f"media {iid}", f"bytes says {it['bytes']}, downloaded {len(raw)}")
            except (_http.UnsafeURL, _http.TooLarge, OSError, ValueError) as e:
                add(WARN, f"media {iid}", f"not downloaded: {e}")
        elif it.get("url"):
            add(FAIL, f"media {iid}", "media url without sha256")
        rc = it.get("receipt")
        if not rc:
            continue
        if jwks is None and jwks_err is None:
            ju = op.get("jwks_uri") if op else proto.get("jwks_uri")
            if not ju or _origin(ju) != iss:
                jwks_err = ("card has no same-origin " + ("operator.jwks_uri" if op else "jwks_uri") + " to verify receipts with")
            else:
                try:
                    s, _, raw = p.t("GET", ju, {"Accept": "application/json"}, None)
                    jwks = json.loads(raw) if s == 200 else None
                    if not isinstance(jwks, dict):
                        jwks_err = f"jwks_uri answered {s} without a JWK Set"
                except Exception as e:  # noqa: BLE001
                    jwks_err = f"jwks_uri: {e}"
        if jwks_err:
            add(FAIL, f"receipt {iid}", jwks_err)
            continue
        res = verify_receipt(rc, jwks, {"iss": iss, "order_id": oid, "item_id": iid, "nonce": nonce,
                                        "request_sha256": request_sha256, "media_sha256": _expected_media(it), "item": it})
        worst = FAIL if any(r[0] == FAIL for r in res) else WARN if any(r[0] == WARN for r in res) else PASS
        add(worst, f"receipt {iid}", "; ".join(d for _, d in res))
    if not items:
        add(WARN if state == "delivered" else PASS, "delivery items", "none yet")


def render(results):
    mark = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "SKIP"}
    lines = [f"[{mark[s]}] {n}" + (f": {d}" if d else "") for s, n, d in results]
    c = {k: sum(1 for r in results if r[0] == k) for k in (PASS, WARN, FAIL, SKIP)}
    lines.append(f"\n{c[PASS]} pass, {c[WARN]} warn, {c[FAIL]} fail" + (f", {c[SKIP]} skipped" if c[SKIP] else ""))
    return "\n".join(lines)


def _served_card(fetcher, target):
    try:
        status, _, body = fetcher(normalize(target)[1])
        card = json.loads(body.decode("utf-8-sig")) if status == 200 else None
    except Exception:  # noqa: BLE001 - already reported by check()
        return None
    return card if isinstance(card, dict) else None


def check_evidence(card, fetch=None, tsa_trust=None, **options):
    """[(status, name, detail)] for every evidence_url on `card` (irlpls/evidence.py). verified -> pass; failed -> fail
    on an evidenced entry, warn on a claimed one; not checkable -> warn on an evidenced entry, skip otherwise. Never
    changes the card."""
    from . import evidence as _evidence
    opts = dict(_evidence.options_from_env(tsa_trust), **options)
    out = []
    res = _evidence.check_card(card, fetch=fetch, **opts)
    for r in res:
        ev = r["level"] == "evidenced"
        status = {_evidence.VERIFIED: PASS, _evidence.FAILED: FAIL if ev else WARN,
                  _evidence.NOT_CHECKABLE: WARN if ev else SKIP}[r["verdict"]]
        detail = f"{r['verdict']} ({r['type'] or 'unknown type'}{', sample pack' if r['sample_pack'] else ''}): " + \
            "; ".join(r["reasons"])
        if r["trust"]:
            detail += f"; trust: {r['trust']}"
        if r["fetched"]:
            f = r["fetched"][0]
            detail += f"; {f['url'][:80]} sha256 {f['sha256'][:16]}... at {f['retrieved_at']}"
        if r["verdict"] == _evidence.VERIFIED and not ev:
            detail += "; the card stays claimed until its provider changes it"
        out.append((status, f"evidence {r['where']}", detail))
    if not res:
        out.append((SKIP, "evidence", "no evidence_url on this card"))
    return out


def main(argv=None, fetcher=None, transport=transport, sleep=time.sleep, evidence_fetch=None):
    ap = argparse.ArgumentParser(prog="irlpls.conformance", description=__doc__.split("\n")[0])
    ap.add_argument("target", help="provider domain or URL")
    ap.add_argument("--against", help="audited card id (cards/<id>.json) or a file path")
    ap.add_argument("--ucp", action="store_true", help="also GET /.well-known/ucp once")
    ap.add_argument("--protocol", action="store_true", help="also check the card's protocol endpoint (STANDARD 14)")
    ap.add_argument("--sandbox", action="store_true",
                    help="with --protocol: place a test order, only if the card declares protocol.sandbox: true")
    ap.add_argument("--token", help="bearer token for the endpoint (or env IRLPLS_PROTOCOL_TOKEN); never printed")
    ap.add_argument("--header", action="append", default=[], metavar="'Name: value'", help="extra auth header, e.g. an API key")
    ap.add_argument("--need", help="the need to quote (default: built from the card)")
    ap.add_argument("--place", help="place for the quote request")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--max-total", help="constraints.max_total in USD, as a decimal string")
    ap.add_argument("--wait", type=float, default=30, help="seconds to poll a sandbox order (default 30)")
    ap.add_argument("--evidence", action="store_true",
                    help="fetch every evidence_url and run its verifier; the target may also be a card id or file")
    ap.add_argument("--tsa-trust", help="with --evidence: file of trusted TSA certificate SHA-256 fingerprints")
    a = ap.parse_args(argv)
    if a.sandbox and not a.protocol:
        ap.error("--sandbox needs --protocol")
    local_card = None
    if a.evidence and not a.target.startswith(("http://", "https://")):
        from . import evidence as _evidence
        local_card = _evidence.load_card(a.target)
        if local_card is not None and (a.protocol or a.ucp or a.against):
            ap.error("--protocol, --ucp and --against need a provider domain, not a local card")
    against = None
    if a.against:
        p = pathlib.Path(a.against)
        if not p.exists():
            p = pathlib.Path(__file__).resolve().parent.parent / "cards" / f"{a.against}.json"
        try:
            against = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            print(f"cannot read audited card {a.against}: {e}", file=sys.stderr)
            return 2
    if fetcher is None:
        fetcher = fetch if transport is globals()["transport"] else (lambda url: transport("GET", url, {}, None))
    if local_card is not None:
        results = check_evidence(local_card, fetch=evidence_fetch, tsa_trust=a.tsa_trust)
        print(render(results))
        return 1 if any(r[0] == FAIL for r in results) else 0
    results = check(a.target, fetcher=fetcher, against=against, ucp=a.ucp)
    if a.evidence:
        card = _served_card(fetcher, a.target)
        if card is None:
            results.append((SKIP, "evidence", "no card to check (the card fetch failed above)"))
        else:
            results += check_evidence(card, fetch=evidence_fetch, tsa_trust=a.tsa_trust)
    if a.protocol:
        auth = {}
        token = a.token or os.environ.get("IRLPLS_PROTOCOL_TOKEN")
        if token:
            auth["Authorization"] = f"Bearer {token}"
        for h in a.header:
            name, sep, val = h.partition(":")
            if not sep or not name.strip():
                ap.error(f"--header needs 'Name: value', got {h!r}")
            auth[name.strip()] = val.strip()
        results += check_protocol(a.target, transport=transport, sandbox=a.sandbox, auth_headers=auth, need=a.need,
                                  place=a.place, lat=a.lat, lon=a.lon, max_total=a.max_total, wait=a.wait, sleep=sleep)
    print(render(results))
    return 1 if any(r[0] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
