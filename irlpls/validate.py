"""Validate a Capture Card against the standard: the JSON Schema (when jsonschema is installed) plus the rules the
schema can't state (docs/STANDARD.md, Sections 2.4, 3 and 8).

    irlpls validate cards/skyfi.json
    irlpls validate https://provider.example/.well-known/irlpls
"""
import ipaddress
import json
import pathlib
import urllib.parse
from datetime import date, timedelta

from . import psl

VERSION = "2026-10-26"
SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "schema" / "capture-card.schema.json"
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


def problems(card, fetched_from=None):
    """[(severity, where, message)] where severity is 'error' (the card is not valid) or 'warn' (it is, but weak)."""
    out = []
    err = lambda w, m: out.append(("error", w, m))
    warn = lambda w, m: out.append(("warn", w, m))
    try:
        import jsonschema
        schema = json.loads(SCHEMA.read_text())
        for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(card), key=lambda e: list(map(str, e.path))):
            err(".".join(map(str, e.path)) or "card", e.message[:160])
    except ImportError:
        warn("schema", "jsonschema is not installed, so only the standard's own rules were checked (pip install jsonschema)")
    except OSError:
        warn("schema", "schema/capture-card.schema.json not found; only the standard's own rules were checked")
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
    try:
        upd, exp = date.fromisoformat(card["updated"]), date.fromisoformat(card["expires"])
        if exp > upd + timedelta(days=366):
            err("expires", "must be at most a year after updated")
        if exp < date.today():
            warn("expires", "this card has expired; brokers will skip it")
    except (KeyError, ValueError):
        warn("expires", "updated and expires should both be dates so brokers can tell a card is stale")
    if (card.get("pricing") or {}).get("from_usd") == 0 and (card.get("access") or {}).get("cost") not in ("free", "free with key", "freemium"):
        pass  # 0 means "not published" for paid providers; that is stated in the standard, not an error
    lic = card.get("licensing") or {}
    for k in ("ai_training", "resale"):
        if k not in lic:
            warn(f"licensing.{k}", "not stated: a policy treats an unstated right as not granted")
    areas = (card.get("coverage") or {}).get("areas")
    if areas is None:
        warn("coverage.areas", "no ISO country codes: brokers must guess coverage from the text")
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
