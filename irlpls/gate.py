"""Offline card gate: the checks that need no network (SCALE-CONTRIBUTION-ARCH Section 2, tiers T0, T1, T2, T9).

    from irlpls.gate import gate_card, gate_files
    r = gate_card(card_dict)      # GateResult(ok, errors=[{code, pointer, message}], warnings=[...])

Reuses irlpls.validate (schema, text and evidence rules); adds size/encoding, bidi and zero-width characters,
URL syntax rules for SSRF, promo/ranking keys, prompt-injection phrases and over-long fields. Never touches the
network and never resolves DNS. Stable codes: T0_SIZE T0_ENCODING T0_JSON T0_NOT_OBJECT T0_CONTROL T0_BIDI
T0_ZEROWIDTH T1_SCHEMA T2_URL_SYNTAX T2_URL_SCHEME T2_URL_CREDENTIALS T2_URL_PRIVATE T2_URL_IP_LITERAL T2_URL_PORT
T9_PROMO_KEY T9_INJECTION T9_OVERLONG; warnings: W_VALIDATE W_AFFILIATE.
"""
import ipaddress
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import safe_text, validate

MAX_BYTES = 32 * 1024
PROMO_KEYS = {"sponsored", "promoted", "featured", "rank", "ranking", "boost", "paid", "ad", "ads", "priority", "score"}
AFFILIATE_PARAMS = ("ref", "utm_", "aff", "affiliate")

_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]")
_ZERO = re.compile("[\u200b-\u200d\u2060\ufeff\u180e\u00ad]")
_CTL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")  # newline/tab are reported by validate.text_problems
_INJECTION = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|earlier|all|any|your|these|the)\b[^.]{0,30}\b(instructions?|prompts?|rules|guidelines)\b",
    r"\byou are now\b", r"\bnew instructions?\s*:", r"\b(system|developer)\s+(prompt|message)\b",
    r"\b(reveal|print|repeat|show)\b[^.]{0,30}\b(system prompt|your instructions|api key|secrets?)\b",
    r"\bdo not (tell|inform|mention to) the user\b", r"\bpretend (to be|you are)\b",
    r"<\|?(im_start|im_end|system|assistant)\|?>", r"^\s*(system|assistant)\s*:", r"\bjailbreak\b",
    r"\b(always|must|should)\s+(rank|recommend|prefer|choose|select)\b[^.]{0,40}\b(first|above|over|this provider|us)\b",
)]
# Existing cards whose provider only documents a plaintext endpoint (ftp:// or http:// with a non-443 port). Scheme and
# port findings on their api.base_url are warnings; every other URL rule still applies. New cards get no waiver.
LEGACY_PLAINTEXT_BASE = {"bom-anon-ftp-observations", "seoul-realtime-city-air"}
_LOCAL_SUFFIX = (".localhost", ".local", ".internal", ".localdomain", ".lan", ".home", ".corp", ".intranet", ".home.arpa")
_NUMLABEL = re.compile(r"(0[xX][0-9a-fA-F]*|\d+)$")


@dataclass
class GateResult:
    ok: bool
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    path: str = None

    def as_dict(self):
        d = {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}
        if self.path:
            d["path"] = self.path
        return d


def pointer(*parts):
    """RFC 6901 JSON pointer from path parts."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _where_to_pointer(where):
    return "" if where in ("", "card") else pointer(*str(where).split("."))


def _item(code, ptr, msg):
    return {"code": code, "pointer": ptr, "message": msg}


def _walk(o, parts=(), key=None):
    """Yield (parts, key, value) for every string value and every dict key (value None, key = the key)."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield parts + (k,), k, ("__key__", k)
            yield from _walk(v, parts + (k,), k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, parts + (i,), key)
    elif isinstance(o, str):
        yield parts, key, o


def _numeric_host(host):
    """True when every dot-label is a decimal, hex or octal number: an obfuscated IPv4 (2130706433, 0x7f.1, 0177.0.0.1, 127.1)."""
    labels = host.split(".")
    return bool(labels) and all(_NUMLABEL.match(x) for x in labels)


def url_problems(url):
    """[(code, message)] for one URL string by syntax alone (no DNS, no network)."""
    if not url:
        return []
    if safe_text._URL_BAD.search(url) or len(url) > validate.MAX_URL:
        return [("T2_URL_SYNTAX", "URL has whitespace, control characters, quotes, angle brackets or is over-long")]
    try:
        p = urllib.parse.urlparse(url)
        host, port = p.hostname, p.port
    except ValueError:
        return [("T2_URL_SYNTAX", "URL does not parse")]
    out = []
    if p.scheme.lower() != "https":
        out.append(("T2_URL_SCHEME", "endpoints must be https:// URLs"))
    if p.username is not None or p.password is not None or "@" in (p.netloc or ""):
        out.append(("T2_URL_CREDENTIALS", "URL must not carry credentials (user:pass@)"))
    if not host:
        out.append(("T2_URL_SYNTAX", "URL has no host"))
        return out
    h = host.lower().rstrip(".")
    ip = None
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        pass
    if ip is not None:
        mapped = getattr(ip, "ipv4_mapped", None) or ip
        if not mapped.is_global:
            out.append(("T2_URL_PRIVATE", "host is a loopback, private, link-local, metadata or otherwise non-public IP"))
        else:
            out.append(("T2_URL_IP_LITERAL", "IP-literal hosts are not allowed; use a DNS name"))
    elif _numeric_host(h):
        out.append(("T2_URL_PRIVATE", "host is a numeric (decimal, hex or octal) IP form"))
    elif h == "localhost" or h.endswith(_LOCAL_SUFFIX) or "." not in h:
        out.append(("T2_URL_PRIVATE", "host is localhost-style or a single-label internal name"))
    if port not in (None, 443):
        out.append(("T2_URL_PORT", f"port {port} not allowed; https on 443 only"))
    return out


def _affiliate(url):
    try:
        q = [k.lower() for k, _ in urllib.parse.parse_qsl(urllib.parse.urlparse(url).query, keep_blank_values=True)]
    except ValueError:
        return False
    return any(k == "ref" or k.startswith(("utm_", "aff")) for k in q)


def gate_card(card, raw_size=None):
    """Run every offline check on a parsed card. Returns GateResult."""
    errs, warns, seen = [], [], set()

    def add(code, ptr, msg):
        if (code, ptr) not in seen:
            seen.add((code, ptr))
            errs.append(_item(code, ptr, msg))

    if not isinstance(card, dict):
        return GateResult(False, [_item("T0_NOT_OBJECT", "", "a card must be a JSON object")])
    size = raw_size if raw_size is not None else len(json.dumps(card, ensure_ascii=False).encode("utf-8", "surrogatepass"))
    if size > MAX_BYTES:
        add("T0_SIZE", "", f"card is {size} bytes, at most {MAX_BYTES}")

    # T0 characters, T2 URLs, T9 promo keys and injection phrases, one walk over keys and strings
    for parts, key, val in _walk(card):
        ptr = pointer(*parts)
        if isinstance(val, tuple):  # a dict key
            k = val[1]
            if isinstance(k, str):
                if k.lower() in PROMO_KEYS:
                    add("T9_PROMO_KEY", ptr, f"promo/ranking key {k!r} is not allowed (no ranking for money)")
                if _BIDI.search(k) or _ZERO.search(k) or _CTL.search(k):
                    add("T0_CONTROL", ptr, "key contains control, bidi or zero-width characters")
            continue
        s = val
        if _BIDI.search(s):
            add("T0_BIDI", ptr, "contains bidirectional override/isolate characters")
        if _ZERO.search(s):
            add("T0_ZEROWIDTH", ptr, "contains zero-width or invisible characters")
        if _CTL.search(s):
            add("T0_CONTROL", ptr, "contains control characters")
        is_url = key in safe_text.URL_KEYS or str(key).endswith(("_url", "_uri")) or re.fullmatch(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S*", s)
        if is_url and not (key == "base_url" and re.fullmatch(r"[A-Za-z ]+", s)):  # 'Not public' is a statement, not a URL
            legacy = card.get("id") in LEGACY_PLAINTEXT_BASE and key == "base_url"
            for code, msg in url_problems(s):
                if legacy and code in ("T2_URL_SCHEME", "T2_URL_PORT"):
                    warns.append(_item("W_" + code[3:], ptr, msg + " (documented plaintext provider endpoint, grandfathered)"))
                else:
                    add(code, ptr, msg)
            if s and _affiliate(s):
                warns.append(_item("W_AFFILIATE", ptr, "URL has referral/affiliate-style parameters (ref, utm_)"))
        else:
            for rx in _INJECTION:
                if rx.search(s):
                    add("T9_INJECTION", ptr, "free text contains a phrase addressed to an AI agent (prompt-injection pattern)")
                    break

    # T1 schema and policy: reuse validate.problems; classify its text rules into stable codes
    for sev, where, msg in validate.problems(card):
        ptr = _where_to_pointer(where)
        if sev == "warn":
            warns.append(_item("W_VALIDATE", ptr, msg))
        elif "control characters" in msg:
            add("T0_CONTROL", ptr, msg)
        elif msg.startswith("longer than"):
            add("T9_OVERLONG", ptr, msg)
        elif "plain https URL" in msg:
            add("T2_URL_SCHEME", ptr, msg)
        elif "private, loopback or local host" in msg:
            add("T2_URL_PRIVATE", ptr, msg)
        else:
            add("T1_SCHEMA", ptr, msg)
    return GateResult(not errs, errs, warns)


def gate_bytes(data):
    """T0 on raw bytes (size, UTF-8, JSON), then gate_card."""
    if len(data) > MAX_BYTES:
        return GateResult(False, [_item("T0_SIZE", "", f"file is {len(data)} bytes, at most {MAX_BYTES}")])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return GateResult(False, [_item("T0_ENCODING", "", "file is not valid UTF-8")])
    try:
        card = json.loads(text)
    except ValueError as e:
        return GateResult(False, [_item("T0_JSON", "", f"not valid JSON ({str(e)[:80]})")])
    return gate_card(card, raw_size=len(data))


def gate_files(paths):
    """{path: GateResult} for each file path."""
    out = {}
    for p in paths:
        try:
            r = gate_bytes(Path(p).read_bytes())
        except OSError as e:
            r = GateResult(False, [_item("T0_JSON", "", f"cannot read file ({e.__class__.__name__})")])
        r.path = str(p)
        out[str(p)] = r
    return out
