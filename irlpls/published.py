"""Provider-published Capture Cards: read once a day from https://<domain>/.well-known/irlpls, checked, bound to the
compiled card for that domain, and shown on the site in place of the compiled card while they stay valid
(docs/research/strategy/PROVIDER-EXPERIENCE.md; STANDARD Section 2.4).

    python3 tools/fetch_published.py          # the daily read (run by tools/healthcheck.py, so by its LaunchAgent)

The read, per domain, is polite: one conditional GET (If-None-Match / If-Modified-Since) of
https://<host>/.well-known/irlpls, HTTPS only, public hosts only, no redirects followed, a 10 s timeout, a 256 KB cap,
no compression, max-age honoured up to 24 h, and a User-Agent naming irlpls and a contact. The hosts are the
`provider_url` hosts of the compiled cards, plus irlpls/data/published-domains.txt (domains the owner added by hand).

A served card is accepted only when all of these hold:
- it parses, and the schema and the standard's own rules pass (irlpls.validate; the small built-in schema check when
  jsonschema is not installed);
- its id names a compiled card, and the serving host owns that card: `psl.same_owner(serving host, compiled
  provider_url host)` and `psl.same_owner(serving host, the served card's provider_url host)`. A subdomain, a
  lookalike, a suffix trick or a neighbour under a shared suffix (bob.github.io) never binds;
- it has not expired, and its status is provider-maintained (or unclaimed: served from its own domain, it becomes
  provider-maintained, Section 2.4).

A card from a domain with no compiled card is never added: it goes to the owner's board (docs/research/BOARD.json, one
'info' update per domain and id). A card claiming a compiled id from a domain that does not own it is rejected and
also goes to the board.

States, per card id (public, in site/published.json):
- live: the last read was accepted. The site shows the published card.
- stale: the last read failed for a network reason (timeout, 5xx, first 404). The last good copy is kept up to
  KEEP_DAYS days. The site shows it, with a grey badge.
- expired: the copy's `expires` has passed. The site falls back to the compiled card at once.
- gone: 404 (or 410) on GONE_404_DAYS days in a row (withdrawn), or unreadable for more than KEEP_DAYS days.
- rejected: the owner's domain served something that does not validate. The site falls back to the compiled card.

Files: irlpls/data/published/state.json (private: the public entries plus per-domain fetch state and new-domain
notes), irlpls/data/published/<id>.json (the last good copy), irlpls/data/published/rejects.log (private, JSON lines:
what was refused and why). All gitignored. site/published.json is the public part of state.json; build.py writes it on
every build, and tools/fetch_published.py writes it right after a read too.
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

from . import psl, validate
from . import http as _http

HERE = pathlib.Path(__file__).resolve().parent
DIR = HERE / "data" / "published"
DOMAINS_TXT = HERE / "data" / "published-domains.txt"
SCHEMA = HERE.parent / "schema" / "capture-card.schema.json"

MAX_BYTES = 256 * 1024
TIMEOUT_S = 10
KEEP_DAYS = 7
GONE_404_DAYS = 2
COOLDOWN_DAYS = 30          # a card re-enters the "Recently published" strip at most once per 30 days
MAX_AGE_CAP_S = 86400
MAX_CARDS_PER_DOC = 50
MAX_CHANGED = 5
USER_AGENT = "irlpls-cardreader/0.1 (+https://irlpls.com/providers.html; hello@irlpls.com)"
SCHEDULE = {"every": "day", "read_at": "06:10 (the Mac running the preview)", "live_within_minutes": 30}
SHOWN = ("live", "stale")   # states in which the site shows the provider's copy
WELL_KNOWN = "/.well-known/irlpls"


def state_dir():
    return pathlib.Path(os.environ.get("IRLPLS_PUBLISHED") or DIR)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def host_of(url):
    try:
        h = urllib.parse.urlparse(str(url or "")).hostname
    except ValueError:
        return None
    return (h or "").lower().rstrip(".") or None


# ------------------------------------------------------------------ fetching

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def default_fetch(url, headers):
    """One GET, no redirects, HTTPS and public hosts only (irlpls.http.check_url), no compression, TIMEOUT_S, MAX_BYTES.
    Returns (status, headers with lower-case names, body bytes). Raises on network errors and on an oversized body."""
    _http.check_url(url, allow_http=False)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    try:
        r = urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        status = getattr(r, "status", None) or r.code
        h = {k.lower(): v for k, v in (r.headers or {}).items()}
        body = _http._read_capped(r, MAX_BYTES) if status == 200 else b""
    return status, h, body


# ------------------------------------------------------------------ checking

def _type_ok(t, v):
    return {"object": lambda: isinstance(v, dict), "array": lambda: isinstance(v, list),
            "integer": lambda: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda: isinstance(v, (int, float)) and not isinstance(v, bool),
            "null": lambda: v is None, "boolean": lambda: isinstance(v, bool), "string": lambda: isinstance(v, str)}.get(t, lambda: True)()


def mini_schema(s, v, path="", errs=None):
    """The JSON Schema keywords capture-card.schema.json uses (the same subset check.html checks), for when jsonschema
    is not installed. Returns [(path, message)]."""
    errs = [] if errs is None else errs
    if not isinstance(s, dict):
        return errs
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_type_ok(t, v) for t in ts):
            errs.append((path, f"should be {' or '.join(ts)}"))
            return errs
    if "const" in s and v != s["const"]:
        errs.append((path, f"must be {s['const']!r}"))
    if "enum" in s and v not in s["enum"]:
        errs.append((path, "is not one of the allowed values"))
    if isinstance(v, str):
        if "pattern" in s and not re.search(s["pattern"], v):
            errs.append((path, "is not in the expected form"))
        if s.get("format") == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            errs.append((path, "should be a date written YYYY-MM-DD"))
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append((path, f"is longer than {s['maxLength']}"))
    if isinstance(v, (int, float)) and not isinstance(v, bool) and "minimum" in s and v < s["minimum"]:
        errs.append((path, f"must be at least {s['minimum']}"))
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errs.append((path, f"needs at least {s['minItems']} items"))
        if s.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in v}) != len(v):
            errs.append((path, "has repeated items"))
        if "items" in s:
            for i, x in enumerate(v):
                mini_schema(s["items"], x, f"{path}.{i}", errs)
    if isinstance(v, dict):
        for k in s.get("required") or []:
            if k not in v:
                errs.append((f"{path}.{k}".lstrip("."), "is missing"))
        props = s.get("properties") or {}
        for k, x in v.items():
            q = f"{path}.{k}".lstrip(".")
            if k in props:
                mini_schema(props[k], x, q, errs)
            elif s.get("additionalProperties") is False:
                errs.append((q, "is not a field of the card"))
            elif isinstance(s.get("additionalProperties"), dict):
                mini_schema(s["additionalProperties"], x, q, errs)
    for sub in s.get("allOf") or []:
        mini_schema(sub, v, path, errs)
    if s.get("anyOf") and not any(not mini_schema(sub, v, path, []) for sub in s["anyOf"]):
        errs.append((path, "matches none of the allowed shapes"))
    if "if" in s and not mini_schema(s["if"], v, path, []) and "then" in s:
        mini_schema(s["then"], v, path, errs)
    return errs


def card_errors(card, fetched_from):
    """Every 'error' validate.problems finds, plus the built-in schema check when jsonschema is not installed (a
    published card is shown only when it validates, so the schema is never skipped)."""
    out = [f"{w}: {m}" for sev, w, m in validate.problems(card, fetched_from=fetched_from) if sev == "error"]
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        out += [f"{p or 'card'}: {m}" for p, m in mini_schema(json.loads(SCHEMA.read_text()), card)]
    return out


def expired(card, today):
    try:
        return datetime.date.fromisoformat(card.get("expires")) < today
    except (TypeError, ValueError):
        return True


# ------------------------------------------------------------------ what changed

def _levels(c, group="verification"):
    return {k: (g or {}).get("level") for k, g in (c.get(group) or {}).items() if isinstance(g, dict)}


def _num(c, *path):
    v = c
    for p in path:
        v = (v or {}).get(p) if isinstance(v, dict) else None
    return v


def material(c):
    """What counts as a real change for the strip's order: price, coverage, rights, speed, a stage level, automation.
    Whitespace, wording and `updated` do not count."""
    return json.dumps({
        "price": [_num(c, "pricing", "from_usd"), _num(c, "pricing", "unit"), _num(c, "access", "cost")],
        "coverage": sorted(map(str, _num(c, "coverage", "areas") or [])),
        "rights": [_num(c, "licensing", k) for k in ("ai_training", "resale", "buyer_owns_output", "vendor_trains_on_data")],
        "speed": [_num(c, "delivery", "speed"), sorted(map(str, _num(c, "delivery", "modes") or []))],
        "levels": [_levels(c), _levels(c, "provenance")],
        "automation": _num(c, "access", "automation_allowed"),
    }, sort_keys=True)


GUARANTEE_WORD = {"human": "Person", "application": "App", "camera": "Camera", "time": "Time", "location": "Place",
                  "content": "Content", "corroboration": "Cross-check", "custody": "Custody record", "receipt": "Delivery receipt"}
RIGHT_WORD = {"allowed": "allowed", "forbidden": "not allowed", None: "not stated"}
LEVEL_WORD = {"evidenced": "evidenced", "claimed": "claimed", "none": "not stated", None: "not stated"}


def _price_words(c):
    p, u = _num(c, "pricing", "from_usd"), str(_num(c, "pricing", "unit") or "")
    cost = _num(c, "access", "cost")
    if cost == "free":
        return "free"
    if isinstance(p, (int, float)) and p > 0:
        u = re.split(r"\s*\(|;|,\s", re.sub(r"^per ", "", u))[0].strip()
        return (f"${p:g}" + (f" / {u}" if u else ""))[:40]
    return "not stated"


def changed(card, compiled):
    """[{field, before, after, beyond}] where the published card differs from the compiled one, in plain words, at most
    MAX_CHANGED rows (anything validate.overclaims flags as stated beyond the compiled card first). Public: buyers see it."""
    rows = []
    beyond = {w for w, *_ in validate.overclaims(card, compiled)}
    add = lambda f, key, a, b: rows.append({"field": f, "before": b, "after": a, "beyond": key in beyond}) if a != b else None  # noqa: E731
    add("Price", "pricing.from_usd", _price_words(card), _price_words(compiled))
    for k, w in (("ai_training", "AI training"), ("resale", "Resale")):
        add(w, f"licensing.{k}", RIGHT_WORD.get(_num(card, "licensing", k), "not stated"),
            RIGHT_WORD.get(_num(compiled, "licensing", k), "not stated"))
    ca, cb = _num(card, "coverage", "areas") or [], _num(compiled, "coverage", "areas") or []
    if sorted(map(str, ca)) != sorted(map(str, cb)):
        fmt = lambda xs: ", ".join(map(str, xs[:4])) + (f" +{len(xs) - 4}" if len(xs) > 4 else "") if xs else "not stated"  # noqa: E731
        add("Coverage", "coverage.areas", fmt(ca), fmt(cb))
    add("Speed", "delivery.speed", str(_num(card, "delivery", "speed") or "not stated"), str(_num(compiled, "delivery", "speed") or "not stated"))
    a_auto, b_auto = _num(card, "access", "automation_allowed"), _num(compiled, "access", "automation_allowed")
    word = {True: "allowed", False: "not allowed", None: "not stated"}
    add("Agents", "access.automation_allowed", word.get(a_auto, "not stated"), word.get(b_auto, "not stated"))
    for group in ("verification", "provenance"):
        la, lb = _levels(card, group), _levels(compiled, group)
        for k in validate.GUARANTEES if group == "verification" else validate.PROVENANCE:
            add(GUARANTEE_WORD[k], f"{group}.{k}", LEVEL_WORD.get(la.get(k), "not stated"), LEVEL_WORD.get(lb.get(k), "not stated"))
    ns, nc = len(card.get("sources") or []), len(compiled.get("sources") or [])
    if ns != nc:
        add("Sources", "sources", f"{ns} listed", f"{nc} compiled")
    rows.sort(key=lambda r: not r["beyond"])
    return rows[:MAX_CHANGED], max(0, len(rows) - MAX_CHANGED)


# ------------------------------------------------------------------ state

def empty_state():
    return {"irlpls_published": 1, "checked_at": None, "next_read": None, "schedule": SCHEDULE, "cards": {}, "domains": {}, "new": {}}


def load_state(d=None):
    p = (d or state_dir()) / "state.json"
    try:
        s = json.loads(p.read_text())
        return s if isinstance(s, dict) and s.get("irlpls_published") == 1 else empty_state()
    except (OSError, ValueError):
        return empty_state()


def public(state):
    """site/published.json: the per-card entries and when irlpls reads next. Never the per-domain fetch state, the
    rejected bodies or the domains waiting for review."""
    return {k: state.get(k) for k in ("irlpls_published", "checked_at", "next_read", "schedule")} | {
        "cards": {i: {k: v for k, v in e.items() if not k.startswith("_")} for i, e in sorted((state.get("cards") or {}).items())}}


def save(state, d=None, site=None):
    d = d or state_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    if site is not None and pathlib.Path(site).is_dir():
        (pathlib.Path(site) / "published.json").write_text(json.dumps(public(state), indent=1) + "\n")


def copy_path(cid, d=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(cid)):
        raise ValueError("bad card id")
    return (d or state_dir()) / f"{cid}.json"


def load_copy(cid, d=None):
    try:
        return json.loads(copy_path(cid, d).read_text())
    except (OSError, ValueError):
        return None


def effective(entry, today):
    """The state as of `today`: a kept copy turns gone after KEEP_DAYS days unread; a copy past its expires is expired."""
    st = (entry or {}).get("state")
    if st in SHOWN:
        if entry.get("expires") and entry["expires"] < today.isoformat():
            return "expired"
        last = (entry.get("last_ok") or "")[:10]
        if st == "stale" and last and (today - datetime.date.fromisoformat(last)).days > KEEP_DAYS:
            return "gone"
    return st


def overrides(cards, state=None, d=None, today=None):
    """[cards with each live or stale published copy in place of its compiled card], {id: effective state}. A copy is
    used only while it is shown (live, stale within KEEP_DAYS, not expired) and still parses as the same id."""
    state = state if state is not None else load_state(d)
    today = today or datetime.date.today()
    eff, out = {}, []
    for c in cards:
        e = (state.get("cards") or {}).get(c["id"])
        if not e:
            out.append(c)
            continue
        s = eff[c["id"]] = effective(e, today)
        copy = load_copy(c["id"], d) if s in SHOWN else None
        if copy and copy.get("id") == c["id"]:
            out.append({**copy, "status": "provider-maintained"})
        else:
            if s in SHOWN:
                eff[c["id"]] = "gone"   # the copy is missing: never show a card that isn't on disk
            out.append(c)
    return out, eff


# ------------------------------------------------------------------ the daily read

def _log(rejects, host, url, reason, detail=None, cid=None, now=None):
    rejects.append({"at": iso(now or utcnow()), "host": host, "url": url, "id": cid, "reason": reason,
                    "detail": [str(x)[:300] for x in (detail or [])][:10]})


def _board_item(kind, key, text, link="/portal"):
    return {"id": f"u-published-{kind}-{re.sub(r'[^a-z0-9.-]+', '-', key.lower())[:80]}", "kind": "info", "text": text, "link": link}


def targets(compiled, extra=()):
    """{host: [compiled ids served from it]}: every compiled card's provider_url host (one GET each), plus extra hosts."""
    t = {}
    for c in compiled:
        if c.get("status") in ("sample", "defunct"):
            continue
        h = host_of(c.get("provider_url"))
        if h and psl.registrable_domain(h):
            t.setdefault(h, []).append(c["id"])
    for h in extra:
        h = host_of("https://" + h.strip()) if h and h.strip() else None
        if h:
            t.setdefault(h, [])
    return t


def read_domains_txt(path=DOMAINS_TXT):
    try:
        return [l.split("#")[0].strip() for l in pathlib.Path(path).read_text().splitlines() if l.split("#")[0].strip()]
    except OSError:
        return []


def _max_age(headers):
    m = re.search(r"max-age\s*=\s*\"?(\d+)", headers.get("cache-control") or "", re.I)
    return int(m.group(1)) if m else None


def run(compiled, state=None, fetch=default_fetch, now=None, extra=(), d=None, evidence=None):
    """Read every target host once and update `state` in place. Returns (state, rejects, board_items).

    compiled: the compiled cards (cards/*.json). fetch(url, headers) -> (status, headers, body); default_fetch in
    production, a fake in tests. evidence(card) -> [{where, verdict}] for a card's sample packs (None skips it)."""
    state = state if state is not None else empty_state()
    for k, v in empty_state().items():
        state.setdefault(k, v)
    now = now or utcnow()
    today = now.date()
    d = d or state_dir()
    by_id = {c["id"]: c for c in compiled}
    rejects, board = [], []
    for host, ids in sorted(targets(compiled, extra).items()):
        _read_one(host, ids, state, fetch, now, today, by_id, d, rejects, board, evidence)
    state["checked_at"] = iso(now)
    state["next_read"] = iso(now + datetime.timedelta(days=1))
    state["schedule"] = SCHEDULE
    return state, rejects, board


def _entries_for(state, host):
    return [i for i, e in (state.get("cards") or {}).items() if e.get("domain") == host]


def _fail_entries(state, host, kind, reason, today, now):
    """A read of an owner's domain failed for every card it serves: network -> stale; 404 -> stale, then gone; a bad
    document -> rejected."""
    for cid in _entries_for(state, host):
        e = state["cards"][cid]
        e["last_read"] = iso(now)
        if kind == "missing":
            miss = [x for x in e.get("_missing_days") or [] if x != today.isoformat()] + [today.isoformat()]
            e["_missing_days"] = miss[-GONE_404_DAYS:]
            run_of = all((today - datetime.date.fromisoformat(x)).days < GONE_404_DAYS for x in e["_missing_days"])
            gone = len(e["_missing_days"]) >= GONE_404_DAYS and run_of
            e["state"], e["reason"] = ("gone", f"withdrawn: not found on {today:%-d %b}") if gone else ("stale", f"not found on {today:%-d %b}")
        elif kind == "unreachable":
            e["state"], e["reason"] = "stale", f"couldn't be read on {today:%-d %b}"
        else:
            e["state"], e["reason"] = "rejected", f"didn't validate on {today:%-d %b}: {reason}"
        if kind != "missing":
            e.pop("_missing_days", None)


def _read_one(host, ids, state, fetch, now, today, by_id, d, rejects, board, evidence):
    url = f"https://{host}{WELL_KNOWN}"
    dom = state["domains"].setdefault(host, {})
    last = parse_iso(dom.get("fetched_at"))
    if last and dom.get("max_age") and (now - last).total_seconds() + 3600 < min(dom["max_age"], MAX_AGE_CAP_S):
        return   # the provider asked us not to re-read yet
    headers = {}
    if dom.get("etag"):
        headers["If-None-Match"] = dom["etag"]
    if dom.get("last_modified"):
        headers["If-Modified-Since"] = dom["last_modified"]
    try:
        status, h, body = fetch(url, headers)
    except _http.TooLarge as e:
        dom.update(fetched_at=iso(now), status="too_large")
        _log(rejects, host, url, "oversized", [e], now=now)
        _fail_entries(state, host, "rejected", f"larger than {MAX_BYTES // 1024} KB", today, now)
        return
    except Exception as e:  # network, DNS, TLS, timeouts, refused hosts: not the provider's card
        dom.update(fetched_at=iso(now), status=type(e).__name__)
        _fail_entries(state, host, "unreachable", None, today, now)
        return
    h = {k.lower(): v for k, v in (h or {}).items()}
    dom.update(fetched_at=iso(now), status=status, max_age=_max_age(h))
    if status == 304:
        for cid in _entries_for(state, host):
            e, copy = state["cards"][cid], load_copy(cid, d)
            e["last_read"] = iso(now)
            if copy and not expired(copy, today) and e.get("state") in ("live", "stale"):
                e.update(state="live", last_ok=iso(now), reason=None)
                e.pop("_missing_days", None)
            elif copy and expired(copy, today):
                e.update(state="expired", reason=f"expired on {copy.get('expires')}")
        return
    if status in (404, 410):
        _fail_entries(state, host, "missing", None, today, now)
        return
    if 300 <= status < 400:
        _log(rejects, host, url, "redirect", [h.get("location", "")], now=now)
        _fail_entries(state, host, "rejected", "served by a redirect", today, now)
        return
    if status != 200:
        _fail_entries(state, host, "unreachable", None, today, now)
        return
    if len(body or b"") > MAX_BYTES:
        _log(rejects, host, url, "oversized", now=now)
        _fail_entries(state, host, "rejected", f"larger than {MAX_BYTES // 1024} KB", today, now)
        return
    ctype = (h.get("content-type") or "").split(";")[0].strip().lower()
    text = (body or b"").decode("utf-8", errors="replace").lstrip("﻿").lstrip()
    if not text.startswith(("{", "[")) and "json" not in ctype:
        _fail_entries(state, host, "missing", None, today, now)   # an HTML catch-all page: no card here
        return
    try:
        doc = json.loads(text)
    except ValueError as e:
        _log(rejects, host, url, "invalid_json", [e], now=now)
        _fail_entries(state, host, "rejected", "not valid JSON", today, now)
        return
    if ctype != "application/json":
        _log(rejects, host, url, "content_type", [ctype or "none"], now=now)
        _fail_entries(state, host, "rejected", "not served as application/json", today, now)
        return
    cards = doc.get("cards") if isinstance(doc, dict) and isinstance(doc.get("cards"), list) else [doc]
    if len(cards) > MAX_CARDS_PER_DOC:
        _log(rejects, host, url, "too_many_cards", [len(cards)], now=now)
        cards = cards[:MAX_CARDS_PER_DOC]
    dom.update(etag=h.get("etag"), last_modified=h.get("last-modified"))
    single = len(cards) == 1
    seen = set()
    for card in cards:
        _take(card, host, url, body if single else None, state, now, today, by_id, d, rejects, board, evidence, seen, h)
    for cid in _entries_for(state, host):   # a card this host used to serve and no longer lists
        if cid not in seen:
            _fail_entries_one(state, cid, today, now)


def _fail_entries_one(state, cid, today, now):
    e = state["cards"][cid]
    e["last_read"] = iso(now)
    e["state"], e["reason"] = "gone", f"withdrawn: no longer listed on {today:%-d %b}"


def _take(card, host, url, body, state, now, today, by_id, d, rejects, board, evidence, seen, h):
    if not isinstance(card, dict) or not isinstance(card.get("id"), str):
        _log(rejects, host, url, "no_id", now=now)
        return
    cid = card["id"]
    comp = by_id.get(cid)
    own = host_of(card.get("provider_url"))
    if comp is None:   # never auto-added: the owner reviews it first
        note = {"id": cid, "name": str(card.get("name") or "")[:80], "first_seen": state["new"].get(f"{host} {cid}", {}).get("first_seen") or iso(now),
                "last_read": iso(now), "valid": not card_errors(card, url)}
        state["new"][f"{host} {cid}"] = note
        board.append(_board_item("new", f"{host}-{cid}",
                                 f"{host} publishes a Capture Card for '{note['name'] or cid}' (id {cid}) and irlpls has no compiled "
                                 f"card for it. It is not listed. Review it, then add a compiled card if it belongs."))
        return
    comp_host = host_of(comp.get("provider_url"))
    if not (comp_host and psl.same_owner(host, comp_host) and own and psl.same_owner(host, own)):
        _log(rejects, host, url, "not_owner", [f"{cid} belongs to {comp_host}", f"served card names {own}"], cid=cid, now=now)
        if psl.registrable_domain(host) != psl.registrable_domain(comp_host or ""):
            board.append(_board_item("collision", f"{cid}-{host}",
                                     f"{host} serves a Capture Card using the id '{cid}', which belongs to {comp_host}. "
                                     f"Rejected; the {comp.get('name') or cid} page is unchanged. Possible impersonation."))
        return   # the real owner's entry is never touched by someone else's domain
    seen.add(cid)
    errs = card_errors(card, url)
    e = state["cards"].get(cid)
    if card.get("status") not in ("provider-maintained", "unclaimed"):
        errs.append(f"status: {card.get('status')!r} cannot be published")
    if errs:
        _log(rejects, host, url, "invalid_card", errs, cid=cid, now=now)
        if e:
            e.update(last_read=iso(now), state="rejected", reason=f"didn't validate on {today:%-d %b}: {len(errs)} problem{'s' if len(errs) != 1 else ''}")
        return
    if expired(card, today):
        if e:
            e.update(last_read=iso(now), state="expired", reason=f"expired on {card.get('expires')}")
        return
    raw = body if body is not None else json.dumps(card, sort_keys=True, separators=(",", ":")).encode()
    sha = hashlib.sha256(raw).hexdigest()
    prev = load_copy(cid, d)
    mat = material(card)
    first = e is None
    e = state["cards"].setdefault(cid, {"first_seen": iso(now)})
    if first or prev is None or material(prev) != mat:
        e["last_material_change"] = iso(now)
        listed = parse_iso(e.get("listed_at"))
        if not listed or (now - listed).days >= COOLDOWN_DAYS:
            e["listed_at"] = iso(now)
    rows, more = changed(card, comp)
    e.update(domain=host, url=url, name=str(card.get("name") or comp.get("name"))[:80], last_read=iso(now), last_ok=iso(now),
             sha256=sha, etag=h.get("etag"), state="live", reason=None, changed=rows, changed_more=more,
             expires=card.get("expires"), updated=card.get("updated"))
    e.pop("_missing_days", None)
    if evidence:
        try:
            e["evidence"] = [{"where": r.get("where"), "verdict": r.get("verdict"), "checked_at": iso(now)} for r in evidence(card)]
        except Exception as x:  # an evidence check never blocks the card
            e["evidence"] = [{"where": None, "verdict": "not checkable", "checked_at": iso(now), "why": type(x).__name__}]
    else:
        e.setdefault("evidence", [])
    d.mkdir(parents=True, exist_ok=True)
    copy_path(cid, d).write_text(json.dumps({**card, "status": "provider-maintained"}, indent=2) + "\n")


def board_add(items, board_path):
    """Append 'info' updates to the owner's board (docs/research/BOARD.json), deduped by id. Returns the ids added."""
    p = pathlib.Path(board_path)
    if not items or not p.exists():
        return []
    b = json.loads(p.read_text())
    have = {u.get("id") for u in b.get("updates") or []}
    add, seen = [], set()
    for u in items:
        if u["id"] not in have and u["id"] not in seen:
            add.append(u)
            seen.add(u["id"])
    if add:
        b["updates"] = add + list(b.get("updates") or [])
        p.write_text(json.dumps(b, indent=1))
    return [u["id"] for u in add]


def append_rejects(rejects, d=None):
    if not rejects:
        return
    d = d or state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "rejects.log", "a") as f:
        for r in rejects:
            f.write(json.dumps(r) + "\n")
