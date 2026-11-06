"""Validate a Capture Card against the standard: the JSON Schema (when jsonschema is installed) plus the rules the
schema can't state (docs/STANDARD.md, Sections 2.4, 3 and 8).

    irlpls validate cards/skyfi.json
    irlpls validate https://provider.example/.well-known/irlpls
"""
import difflib
import ipaddress
import json
import os
import pathlib
import re
import urllib.parse
from datetime import date, timedelta

from . import paths, psl, safe_text

VERSION = "2026-10-26"
SCHEMA = paths.schema_dir() / "capture-card.schema.json"
GUARANTEES = ("human", "application", "camera", "time", "location", "content", "corroboration")
PROVENANCE = ("custody", "receipt")  # optional, after capture: who held it (custody) and a signed statement of delivery (receipt)


SAMPLE_PACK_NAME = "irlpls-sample.json"  # the sample-pack manifest (STANDARD Section 3.1 note)


def is_sample_pack_url(url):
    """True when the URL names a sample-pack manifest: its path ends in irlpls-sample.json (no fetch here)."""
    return urllib.parse.urlparse(url or "").path.endswith(SAMPLE_PACK_NAME)


def private_host(url):
    """True if the URL's host is localhost, a .local/.internal name or a non-public IP literal (no DNS lookup here)."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".localdomain")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return not ip.is_global


MAX_TEXT = 4000   # longest free-text field; the longest real card text is under 1700
MAX_URL = 2000
LIMITS = {"name": 200, "short": 120, "id": 100}


def text_problems(card):
    """[(where, message)]: control characters (a newline forges a line in llms.txt, ESC drives a terminal) or oversize
    text in any string, and any URL field that is not a plain https URL (a javascript: link is live on a card page)."""
    out = []

    def walk(o, path, key):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k), k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}.{i}", key)
        elif isinstance(o, str):
            if key in safe_text.URL_KEYS:
                if o and not safe_text.is_safe_url(o):  # an empty string means 'none yet' (claimed, no evidence)
                    out.append((path, "must be a plain https URL (no javascript:, data:, http:, spaces or control characters)"))
            elif safe_text.strip_controls(o, multiline=True) != o or "\n" in o or "\t" in o:
                out.append((path, "contains control characters or a line break; card text is single-line"))
            elif len(o) > LIMITS.get(path, MAX_TEXT):
                out.append((path, f"longer than {LIMITS.get(path, MAX_TEXT)} characters"))

    walk(card, "", None)
    return out


def _found(v, n=40):
    """A short, safe rendering of a found value: never the whole of an oversized value."""
    if isinstance(v, str):
        return repr(v if len(v) <= n else v[:n] + "...") + (f" ({len(v)} characters)" if len(v) > n else "")
    if isinstance(v, (dict, list)):
        return f"a {type(v).__name__} with {len(v)} entries"
    return repr(v)[:n]


REQUIRED_HINT = {
    "pricing": "the block needs model, from_usd, unit", "latency": "the block needs typical, notes",
    "delivery": "the block needs modes, speed, basis", "coverage": "the block needs regions, notes",
    "licensing": "the block needs summary (state ai_training and resale too)",
    "capabilities": "the block needs modalities, tasks", "verification": "the block needs the seven guarantees, each {level, unknown}",
    "short": "one line, up to 120 characters; needed to be listed", "access": "needed to be listed; see examples/card-template.md",
    "api": "needed to be listed; see examples/card-template.md",
}
PATTERN_HINT = {
    "id": "lowercase letters, digits and hyphens only",
    "areas": "ISO 3166-1 alpha-2 (US) or ISO 3166-2 (US-OH) codes, or *",
}


def schema_message(e):
    """(where, short actionable message) for one jsonschema error: field path, expected, found; never a raw regex."""
    path = [str(p) for p in e.path]
    last = path[-1] if path else ""
    where = ".".join(path) or "card"
    v = e.validator
    if v == "required":
        missing = re.findall(r"'([^']+)' is a required property", e.message)
        name = missing[0] if missing else "field"
        w = ".".join(path + [name])
        sub = path[-1] if path else ""
        hint = REQUIRED_HINT.get(name) or ("{level, unknown}" if sub == "verification" else "")
        return w, "missing required field" + (f": {hint}" if hint else "")
    if v == "enum":
        return where, f"expected one of {', '.join(map(str, e.validator_value))}; found {_found(e.instance)}"
    if v == "const":
        return where, f"expected {_found(e.validator_value)}; found {_found(e.instance)}"
    if v == "type":
        t = e.validator_value
        t = "/".join(t) if isinstance(t, list) else t
        return where, f"expected {t}; found {type(e.instance).__name__}" + (" (0 means not published)" if last == "from_usd" else "")
    if v == "pattern":
        hint = PATTERN_HINT.get(last) or PATTERN_HINT.get(path[-2] if len(path) > 1 else "")
        if not hint:
            hint = "a plain https:// URL" if last.endswith(("url", "uri")) or last in safe_text.URL_KEYS else "a different format"
        return where, f"expected {hint}; found {_found(e.instance)}"
    if v in ("maxLength", "maxItems"):
        n = len(e.instance)
        return where, f"too long: {n} {'characters' if v == 'maxLength' else 'items'}, at most {e.validator_value}"
    if v in ("minLength", "minItems"):
        return where, "must not be empty"
    if v == "additionalProperties":
        extra = [k for k in (e.instance if isinstance(e.instance, dict) else {}) if k not in (e.schema.get("properties") or {})]
        return where, "unknown key " + ", ".join(repr(k[:40]) for k in extra[:3]) + _suggest(extra[0], list((e.schema.get("properties") or {}))) if extra else "unknown key"
    msg = re.sub(r"'(?:[^'\\]|\\.){41,}'", "'...'", e.message)  # never echo a long value
    return where, msg[:140]


def _suggest(key, known):
    m = difflib.get_close_matches(str(key), known, n=1, cutoff=0.7)
    return f"; did you mean {m[0]!r}?" if m else ""


def unknown_keys(card):
    """[(where, message)]: keys the schema does not define, where the schema does not already forbid them.
    Warnings only: an extra key is dropped by readers, a typo in an optional key is silently lost."""
    out = []
    try:
        schema = json.loads(SCHEMA.read_text())
    except (OSError, ValueError):
        return out

    def walk(o, sc, path):
        if isinstance(o, dict) and isinstance(sc, dict):
            props = sc.get("properties")
            if props is not None and sc.get("additionalProperties") is not False:
                for k in o:
                    if k not in props and not str(k).startswith("x-"):
                        out.append((".".join(path + [str(k)[:40]]), f"unknown key{_suggest(k, list(props))}; not in the standard's schema"))
            for k, v in o.items():
                sub = (props or {}).get(k)
                if sub is None and isinstance(sc.get("additionalProperties"), dict):
                    sub = sc["additionalProperties"]
                if sub is not None:
                    walk(v, sub, path + [str(k)])
        elif isinstance(o, list) and isinstance(sc, dict) and isinstance(sc.get("items"), dict):
            for i, v in enumerate(o):
                walk(v, sc["items"], path + [str(i)])

    walk(card, schema, [])
    return out


DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _bad_date(v):
    try:
        return not (isinstance(v, str) and DATE.fullmatch(v) and date.fromisoformat(v))
    except ValueError:
        return True


def problems(card, fetched_from=None):
    """[(severity, where, message)] where severity is 'error' (the card is not valid) or 'warn' (it is, but weak)."""
    out = []
    err = lambda w, m: out.append(("error", w, m))
    warn = lambda w, m: out.append(("warn", w, m))
    try:
        import jsonschema
        schema = json.loads(SCHEMA.read_text())
        seen = set()
        for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(card), key=lambda e: list(map(str, e.path))):
            where, msg = schema_message(e)
            if (where, msg) not in seen:
                seen.add((where, msg))
                err(where, msg)
    except ImportError:
        warn("schema", "jsonschema is not installed, so only the standard's own rules were checked (pip install jsonschema)")
    except OSError:
        warn("schema", "schema/capture-card.schema.json not found; only the standard's own rules were checked")
    for where, msg in text_problems(card):  # same rules as the schema, so they hold without jsonschema installed
        err(where, msg)
    if card.get("spec_version") != VERSION:
        err("spec_version", f"must be {VERSION!r}, not {card.get('spec_version')!r}")
    if card.get("capability") != "com.irlpls.capture.card":
        err("capability", "must be 'com.irlpls.capture.card'")
    v = card.get("verification") or {}
    for k in GUARANTEES:
        g = v.get(k)
        if not g:
            err(f"verification.{k}", "missing: all seven guarantees are required")
        elif g.get("level") == "evidenced" and not (g.get("method") and g.get("evidence_url")):
            err(f"verification.{k}", "evidenced needs a method and a public evidence_url")
        elif g.get("level") == "evidenced" and urllib.parse.urlparse(str(g["evidence_url"])).scheme != "https":
            err(f"verification.{k}", "evidence_url must be an https URL")
        elif g.get("level") == "evidenced" and private_host(g["evidence_url"]):
            err(f"verification.{k}", "evidence_url points at a private, loopback or local host; proof must be public")
        elif g.get("level") == "evidenced" and g.get("standard") == "c2pa" and not g.get("conforming_product"):
            warn(f"verification.{k}", "standard c2pa but no conforming_product named")
    for k, g in (card.get("provenance") or {}).items():  # optional custody / receipt (Section 2.2); absent = not stated
        if not isinstance(g, dict) or k not in PROVENANCE:
            continue  # the schema reports unknown keys and wrong types
        if g.get("level") == "evidenced" and not (g.get("method") and g.get("evidence_url")):
            err(f"provenance.{k}", "evidenced needs a method and a public evidence_url")
        elif g.get("level") == "evidenced" and urllib.parse.urlparse(str(g["evidence_url"])).scheme != "https":
            err(f"provenance.{k}", "evidence_url must be an https URL")
        elif g.get("level") == "evidenced" and private_host(g["evidence_url"]):
            err(f"provenance.{k}", "evidence_url points at a private, loopback or local host; proof must be public")
        elif g.get("level") not in ("claimed", "evidenced"):
            err(f"provenance.{k}", "level must be claimed or evidenced; leave the entry out when nothing is stated")
    for group, keys in (("verification", GUARANTEES), ("provenance", PROVENANCE)):
        for k in keys:
            g = (card.get(group) or {}).get(k)
            if isinstance(g, dict) and g.get("level") == "evidenced" and isinstance(g.get("evidence_url"), str) \
                    and g["evidence_url"].startswith("https://") and not is_sample_pack_url(g["evidence_url"]):
                warn(f"{group}.{k}", "evidenced, but evidence_url is not a sample pack (a URL ending in "
                     f"{SAMPLE_PACK_NAME}: artefact, proof, key or trust anchor, README; Section 3.1 note), so a "
                     "stranger may have nothing to check")
    rl = (card.get("access") or {}).get("rate_limit")
    for where, tier in (("access.rate_limit", rl), ("access.rate_limit.with_key", (rl or {}).get("with_key") if isinstance(rl, dict) else None)):
        if not isinstance(tier, dict):
            continue  # the schema reports wrong types; absent means the provider publishes no limit
        numbers = [k for k in ("requests", "per_seconds", "burst", "batch_max", "cache_seconds") if k in tier]
        src = tier.get("source_url") or (rl.get("source_url") if tier is not rl else None)
        if numbers and not src:
            err(where, "a stated limit needs source_url: only numbers the provider publishes, never estimates")
        elif src and urllib.parse.urlparse(str(src)).scheme != "https":
            err(where, "source_url must be an https URL")
        if ("requests" in tier) != ("per_seconds" in tier):
            err(where, "requests and per_seconds go together")
        if isinstance(tier.get("burst"), int) and isinstance(tier.get("requests"), int) and tier["burst"] > tier["requests"]:
            warn(where, "burst is larger than requests per window")
    may = (card.get("account") or {}).get("agent_may_act") if isinstance(card.get("account"), dict) else None
    if isinstance(may, dict) and may.get("source_url"):  # the schema requires allowed + source_url
        if urllib.parse.urlparse(str(may["source_url"])).scheme != "https":
            err("account.agent_may_act", "source_url must be an https URL")
        elif private_host(may["source_url"]):
            err("account.agent_may_act", "source_url points at a private, loopback or local host; the terms must be public")
    for i, m in enumerate((card.get("api") or {}).get("auth") or []):
        if isinstance(m, dict) and m.get("type") != "oauth2" and ("cimd" in m or "dynamic_registration" in m):
            err(f"api.auth.{i}", "cimd and dynamic_registration apply only to type oauth2")
    pay = card.get("payment") if isinstance(card.get("payment"), dict) else {}
    for r in ("x402", "mpp"):
        if r in (pay.get("agent_rails") or []) and r not in (pay.get("methods") or []):
            warn("payment.agent_rails", f"{r} is an agent rail but not in payment.methods")
    if card.get("status") == "unclaimed" and not card.get("sources"):
        err("sources", "an unclaimed card must cite a source for its claims")
    dates_ok = True
    for k in ("updated", "expires"):
        if k in card and _bad_date(card[k]):
            warn(k, f"expected a date as YYYY-MM-DD; found {_found(card[k])}")
            dates_ok = False
        elif k not in card:
            dates_ok = False
    if dates_ok:
        upd, exp = date.fromisoformat(card["updated"]), date.fromisoformat(card["expires"])
        if exp > upd + timedelta(days=366):
            err("expires", "must be at most a year after updated")
        if exp < date.today():
            warn("expires", "this card has expired; brokers will skip it")
    elif "updated" not in card or "expires" not in card:
        warn("expires", "updated and expires should both be dates (YYYY-MM-DD) so brokers can tell a card is stale")
    if (card.get("pricing") or {}).get("from_usd") == 0 and (card.get("access") or {}).get("cost") not in ("free", "free with key", "freemium"):
        pass  # 0 means "not published" for paid providers; that is stated in the standard, not an error
    lic = card.get("licensing") or {}
    for k in ("ai_training", "resale"):
        if k not in lic:
            warn(f"licensing.{k}", "not stated: a policy treats an unstated right as not granted")
    areas = (card.get("coverage") or {}).get("areas")
    if areas is None:
        warn("coverage.areas", "no ISO country codes: brokers must guess coverage from the text")
    for k in ("short", "access", "api"):  # not in the schema's required list, but a card is not listed without them
        if not card.get(k):
            warn(k, f"needed to be listed: {REQUIRED_HINT[k]}")
    for group, keys in (("verification", GUARANTEES), ("provenance", PROVENANCE)):
        for k in keys:
            g = (card.get(group) or {}).get(k) if isinstance(card.get(group), dict) else None
            if isinstance(g, dict) and g.get("level") in ("none", "claimed") and not str(g.get("unknown") or "").strip():
                warn(f"{group}.{k}.unknown", f"empty: say in one line what remains uncertain (level is {g['level']})")
    for where, msg in unknown_keys(card):
        warn(where, msg)
    if card.get("status") == "provider-maintained" and not fetched_from:
        warn("status", "provider-maintained applies only to a card served from the provider's own domain; "
             "a file you submit is unclaimed")
    if fetched_from:
        host = urllib.parse.urlparse(fetched_from).hostname or ""
        own = urllib.parse.urlparse(card.get("provider_url") or "").hostname or ""
        if card.get("status") == "provider-maintained" and not psl.same_owner(host, own):
            err("status", f"provider-maintained cards must be served from the provider's own domain ({own or 'no provider_url'}), not {host}")
    return out


def load(target):
    """A card from a file path or an https URL. Returns (card, url or None)."""
    if target.startswith(("http://", "https://")):
        from . import http
        if target.startswith("http://") and os.environ.get("IRLPLS_ALLOW_PRIVATE_HOSTS") != "1":
            raise ValueError("cards are read over HTTPS only. To rehearse on localhost, set IRLPLS_ALLOW_PRIVATE_HOSTS=1 (validate only)")
        return http.get(target), target
    return json.loads(pathlib.Path(target).read_text()), None


LEVEL = {"none": 0, "claimed": 1, "evidenced": 2}
RIGHT = {None: 0, "forbidden": -1, "allowed": 1}


def overclaims(card, audited):
    """Where a self-published `card` says more than the independently audited `audited` card for the same provider.
    [(where, they_say, we_found)]. A provider may say less than we found; saying more is flagged for a person to check.
    The audited card is never changed by this: a provider's own statement is shown beside it, not over it."""
    out = []
    for k in GUARANTEES:
        a, b = (card.get("verification") or {}).get(k) or {}, (audited.get("verification") or {}).get(k) or {}
        if LEVEL.get(a.get("level"), 0) > LEVEL.get(b.get("level"), 0):
            out.append((f"verification.{k}", a.get("level"), b.get("level") or "none"))
    for k in PROVENANCE:
        a, b = (card.get("provenance") or {}).get(k) or {}, (audited.get("provenance") or {}).get(k) or {}
        if LEVEL.get(a.get("level"), 0) > LEVEL.get(b.get("level"), 0):
            out.append((f"provenance.{k}", a.get("level"), b.get("level") or "not stated"))
    la, lb = card.get("licensing") or {}, audited.get("licensing") or {}
    for k in ("ai_training", "resale"):
        if RIGHT.get(la.get(k), 0) > RIGHT.get(lb.get(k), 0):
            out.append((f"licensing.{k}", la.get(k), lb.get(k) or "not stated"))
    if la.get("buyer_owns_output") and lb.get("buyer_owns_output") is not True:
        out.append(("licensing.buyer_owns_output", True, lb.get("buyer_owns_output", "not stated")))
    ca, cb = (card.get("coverage") or {}).get("areas") or [], (audited.get("coverage") or {}).get("areas") or []
    if ca and cb and "*" not in cb and (set(ca) - set(cb) or "*" in ca):
        out.append(("coverage.areas", ca[:6], cb[:6]))
    aa, ab = card.get("access") or {}, audited.get("access") or {}
    if aa.get("automation_allowed") is True and ab.get("automation_allowed") is False:
        out.append(("access.automation_allowed", True, False))
    pa, pb = (card.get("pricing") or {}).get("from_usd"), (audited.get("pricing") or {}).get("from_usd")
    if pa and pb and pa < pb * 0.5:
        out.append(("pricing.from_usd", pa, pb))
    sa, sb = (card.get("delivery") or {}).get("speed"), (audited.get("delivery") or {}).get("speed")
    order = ["minutes", "hours", "days", "weeks"]
    if sa in order and sb in order and order.index(sa) < order.index(sb):
        out.append(("delivery.speed", sa, sb))
    if (card.get("api") or {}).get("available") and not (audited.get("api") or {}).get("available"):
        out.append(("api.available", True, False))
    return out


def to_provider_file(card):
    """The card as a provider would host it at /.well-known/irlpls, plus what only the provider can settle.
    Returns (document, checklist). The document keeps status "unclaimed" and the compiled sources: it becomes
    provider-maintained only when it is served from the provider's own domain (Section 2.4), and the provider
    decides what to correct. Nothing here is a claim irlpls makes for them."""
    from datetime import date, timedelta
    doc = json.loads(json.dumps(card))
    today = date.today()
    doc["updated"], doc["expires"] = today.isoformat(), (today + timedelta(days=365)).isoformat()
    todo = []
    for k in GUARANTEES:
        g = (doc.get("verification") or {}).get(k) or {}
        todo.append(f"verification.{k}: currently '{g.get('level')}'. {g.get('unknown', '')} Raise it only with a method and a public evidence_url.")
    lic = doc.get("licensing") or {}
    for k in ("ai_training", "resale", "buyer_owns_output", "vendor_trains_on_data"):
        if k not in lic:
            todo.append(f"licensing.{k}: not stated. State it if your terms do; an unstated right counts as not granted.")
    if not (doc.get("coverage") or {}).get("areas"):
        todo.append("coverage.areas: list the ISO country codes you serve, so brokers can match places exactly.")
    if (doc.get("pricing") or {}).get("from_usd") == 0 and (doc.get("access") or {}).get("cost") not in ("free", "free with key"):
        todo.append("pricing.from_usd: 0 means 'not published'. Publish a starting price if you can.")
    if (doc.get("access") or {}).get("automation_allowed") is None:
        todo.append("access.automation_allowed: say whether your terms let agents use your API (false rules you out for agents).")
    if not (doc.get("requester") or {}).get("checks") and (doc.get("capture") or {}).get("by") in ("people", "contributors"):
        todo.append("requester.checks: say what you check about who commissions, since people are sent to places.")
    todo.append(f"sources: {len(doc.get('sources') or [])} compiled from public pages. Remove any that are wrong; add your own.")
    todo.append("Then serve the file at https://<your domain>/.well-known/irlpls (HTTPS, no redirects, Cache-Control: public, max-age >= 60). "
                "It counts as provider-maintained only when served from the domain in provider_url.")
    return doc, todo


def family_fix(card_id):
    """The plain, one-line fix for a card that is in no family (shared by build.py and tools/check_cards.py)."""
    from .catalog import FAMILIES
    ex = FAMILIES[0]
    ids = ", ".join(f[0] for f in FAMILIES[:6])
    return (f"{card_id}: not in any family. Fix: add \"{card_id}\" to FAMILIES in irlpls/catalog.py, in the last list of the closest "
            f"family, for example: [{', '.join(repr(x).replace(chr(39), chr(34)) for x in ex[4][:2])}, \"{card_id}\"]),   "
            f"(family ids: {ids}, ...)")


def repo_problems(cards_dir):
    """[str]: what the repo gate and build.py both refuse, in plain words, naming the card.
    Not valid JSON, id != file name, duplicate id, and a listed card that belongs to no family."""
    from .catalog import FAMILIES, usable
    out, seen, cards = [], {}, []
    for f in sorted(pathlib.Path(cards_dir).glob("*.json")):
        try:
            c = json.loads(f.read_text())
        except ValueError as e:
            out.append(f"{f.stem}: not valid JSON ({str(e)[:80]})")
            continue
        cid = c.get("id") if isinstance(c, dict) else None
        if cid != f.stem:
            out.append(f"{f.stem}: id {str(cid)[:60]!r} must equal the file name; rename the file to {str(cid)[:60]}.json or fix the id")
        if cid in seen:
            out.append(f"{f.stem}: duplicate id {str(cid)[:60]!r}, already used by cards/{seen[cid]}.json; ids are unique")
        seen[cid] = f.stem
        if isinstance(c, dict):
            cards.append(c)
    in_family = {p for f in FAMILIES for p in f[4]}
    for c in usable(cards):
        if c.get("id") not in in_family:
            out.append(family_fix(c.get("id")))
    return out
