"""The broker: one request in, a job out.

A requester (an agent, or a person through their agent) states a need in natural language, ideally with why.
irlpls is inference-free: the requester's own model writes the need and answers any questions; irlpls parses it,
asks only what changes the outcome, plans against the Capture Cards, enforces the requester's policy, and executes
through the provider adapters. Whether the data comes from a live feed or a commissioned capture is decided here,
from the need, not by the requester.

    job = irlpls.request("is the Guadalupe at Comfort, TX good for tubing right now", policy={...})
    job.questions          # anything blocking, e.g. no place given
    job.plan               # ranked options: provider, route (live / commission), fit, gaps, policy violations
    job.ruled_out          # every other provider: reason code (no_match, outside_coverage, too_slow, policy:<rule>,
                           # needs_key), a 1-2 word label, a short human `why`, and every code that applies
    job.submit()           # live routes run now (free); commissions return a quote unless confirm=True
    job.results            # what came back, with the provider's proof levels attached
    job.warnings           # what an adapter tried and couldn't do (a rate-limited camera, a fallback endpoint)

Providers whose card declares a `protocol` block (docs/protocol/PROTOCOL-DRAFT.md) are called through the protocol
client (irlpls/protocol/client.py) instead of an adapter: submit() quotes at the provider's own endpoint, submit(confirm=
True) orders there (never through a hosted irlpls), order states become job states one to one, and results carry the
provider's per-stage provenance and the outcome of checking each item's receipt against the card's keys.

Lifecycle (docs/STANDARD.md, section 6): clarifying -> proposed -> delivered (live) | quoted -> submitted (commission);
refresh() then maps the provider's own status to in_flight, delivered or failed. Policy violations end in failed
(or quoted, when the quote is over budget); they are reported, never raised.
"""
import json
import os
import pathlib
import time
import uuid
from urllib.parse import urlsplit

from . import adapters
from .policy import Policy, PolicyViolation, as_decimal, is_free, valid_money
from . import health
from .readiness import rank as readiness_rank, readiness, readiness_for

def _order_identity(card):
    """What a person approved along with a price: where the order goes and whose keys sign for it."""
    p = (card or {}).get("protocol") or {}
    return (p.get("endpoint"), p.get("jwks_uri"), json.dumps(p.get("operator"), sort_keys=True))


# problem codes the reference library makes itself (never normalised as a provider's): STANDARD 14.9 client-local codes
_LOCAL_CODES = ("no_offer", "unsafe_endpoint", "media_mismatch", "no_protocol", "price_changed", "request_mismatch")

class JobError(ValueError):
    """A request problem with a stable `code` and the next step said for each interface (`hints`: mcp, cli, python), so
    no interface tells its user to make another interface's call. str() is the plain message."""
    def __init__(self, code, message, mcp=None, cli=None, python=None, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra
        self.hints = {k: v for k, v in (("mcp", mcp), ("cli", cli), ("python", python)) if v}

    def hint(self, interface):
        return self.hints.get(interface) or self.hints.get("python") or ""


def _key_needed(what="placing a real order"):
    return JobError("idempotency_key_required", f"{what} needs an idempotency_key: any unique string, reused on a retry so it "
                    "never happens twice", mcp="pass idempotency_key together with confirm=true",
                    cli="add --idempotency-key KEY to --confirm", python="pass idempotency_key= with confirm=True")


HOME = pathlib.Path(os.environ.get("IRLPLS_HOME", pathlib.Path.home() / ".irlpls"))
_JOBS = {}


# -- operator ceiling -----------------------------------------------------------------------------------------------
# Set by whoever runs the process (environment variables), read once at import, never by a tool argument or a policy.
#   IRLPLS_MAX_ORDER_USD   most any single order may cost. Unset: no operator ceiling for the library, 0 for the MCP server.
#                          0 (or a bad value) means no order at all.
#   IRLPLS_ALLOW_SUBMIT    1/true/yes/on lets the MCP server place orders, approve prices and cancel. Unset: it cannot.
# A job's own policy can only be stricter: the effective ceiling is the smaller of the two, and auto_submit never lifts it.
def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _env_ceiling(raw):
    """The operator's ceiling as a Decimal, None when unset; anything that is not a finite non-negative number is 0."""
    if raw is None or not str(raw).strip():
        return None
    from decimal import Decimal
    try:
        d = Decimal(str(raw).strip())
    except Exception:
        return 0
    return d if valid_money(d) else 0


OPERATOR_ALLOW_SUBMIT = _truthy(os.environ.get("IRLPLS_ALLOW_SUBMIT"))
OPERATOR_MAX_ORDER_USD = _env_ceiling(os.environ.get("IRLPLS_MAX_ORDER_USD"))


def operator_limits(strict=False):
    """(may_order, ceiling): strict is the MCP server (orders off unless IRLPLS_ALLOW_SUBMIT; ceiling defaults to 0)."""
    ceiling = OPERATOR_MAX_ORDER_USD
    if strict and ceiling is None:
        ceiling = 0
    ok = (OPERATOR_ALLOW_SUBMIT or not strict) and (ceiling is None or ceiling > 0)
    return ok, ceiling


def operator_refusal(strict=False, what="placing an order"):
    ok, ceiling = operator_limits(strict)
    if ok:
        return None
    if strict and not OPERATOR_ALLOW_SUBMIT:
        return (f"{what} is disabled on this server: the operator has not set IRLPLS_ALLOW_SUBMIT. irlpls plans and quotes; the "
                "person approves and pays in their own client or at the provider")
    return f"{what} is disabled: the operator's IRLPLS_MAX_ORDER_USD is {ceiling or 0}; set it above 0 to allow orders"


# -- private, atomic, bounded state ---------------------------------------------------------------------------------
JOB_MAX_AGE_DAYS = 30      # saved job files older than this are pruned
JOB_MAX_COUNT = 200        # ... and at most this many are kept (the newest)
STATE_MAX_BYTES = 1_000_000  # a saved file over this is trimmed (log, ruled_out, plan) before it is written
_last_prune = [0.0]


def _private_dir(p):
    p = pathlib.Path(p)
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _atomic_write(path, text):
    """Write text to path through a temp file in the same directory (mode 0600), then rename over it."""
    path = pathlib.Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune_jobs(keep=None):
    now = time.time()
    if now - _last_prune[0] < 60:
        return
    _last_prune[0] = now
    try:
        files = sorted(((f.stat().st_mtime, f) for f in (HOME / "jobs").iterdir() if f.is_file() and f.name.endswith((".json", ".lock"))),
                       key=lambda t: t[0], reverse=True)
    except OSError:
        return
    for i, (mt, f) in enumerate(files):
        if f.name.split(".")[0] == keep:
            continue
        if i >= JOB_MAX_COUNT or now - mt > JOB_MAX_AGE_DAYS * 86400:
            try:
                f.unlink()
            except OSError:
                pass


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _OrderLock:
    """Serialises check-then-act on a job's orders: a process-wide re-entrant lock, plus an flock on <job>.lock so two
    processes sharing ~/.irlpls cannot both pass the check. Falls back to the in-process lock where no file lock exists."""
    _mem = __import__("threading").RLock()

    def __init__(self, job_id):
        self.path = HOME / "jobs" / f"{job_id}.lock"
        self.fd = None

    def __enter__(self):
        self._mem.acquire()
        try:
            _private_dir(HOME / "jobs")
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX)
            except ImportError:
                try:
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_LOCK, 1)
                except (ImportError, OSError):
                    pass
        except OSError:
            self.fd = None
        return self

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)   # closing drops the flock
        finally:
            self._mem.release()
        return False


def _fingerprint(card_id, spec):
    import hashlib
    body = {k: v for k, v in (spec or {}).items() if k != "idempotency_key"}
    return hashlib.sha256(json.dumps([card_id, body], sort_keys=True, default=str).encode()).hexdigest()


def _reused(key):
    return JobError("idempotency_key_reused", f"the idempotency key {key!r} was already used for a different order",
                    mcp="use a new idempotency_key for a different order", cli="use a new --idempotency-key",
                    python="use a new idempotency_key")


# Protocol client settings. `client` is passed to irlpls.protocol.client.Client: HTTPS and public hosts only unless a
# test (or a deliberate local setup) opts out explicitly with {"allow_http": True, "allow_private": True}. `auth` maps a
# card id to the headers carrying the person's own credentials for that provider (they never leave this process).
PROTOCOL = {"client": {}, "auth": {}, "discover": True}


def _protocol(card):
    """The card's protocol block, when it declares one the client can use."""
    from .protocol.client import protocol_block
    return protocol_block(card)


def _route(card, req):
    """live if the provider has a live feed and the need is about now; otherwise commission (if it can)."""
    modes = (card.get("delivery") or {}).get("modes", [])
    if req.mode == "live" and "live" in modes:
        return "live"
    if "commission" in modes:
        return "commission"
    return "live" if "live" in modes else None


# Why a provider is not in the plan. A provider gets every code that applies; `reason` is the first in this order:
# the requester's own rules, then facts from the card (coverage, speed), then the keyword match (the least certain).
POLICY_LABELS = {"policy:allow": "not allowed", "policy:deny": "denied", "policy:include_unclaimed": "unconfirmed card",
                 "policy:require": "weak proof", "policy:max_unit_price_usd": "over budget", "policy:free_only": "paid",
                 "policy:need_ai_training": "no AI rights", "policy:need_resale": "no resale rights",
                 "policy:require_c2pa": "no C2PA", "policy:min_assurance_level": "weak C2PA",
                 "policy:allow_person_targeting": "targets a person"}
REASON_ORDER = ("unsupported", "policy", "terms", "outside_coverage", "too_slow", "no_match", "needs_key")
# A job irlpls refuses or redirects: the reason is stated on the job (refusal, reason_code), not only in ruled_out counts.
REFUSALS = {
    "person_targeting": ("Refused: this asks to watch, follow or photograph a specific person. irlpls never sends people "
                         "to do that unless the person's own policy sets allow_person_targeting, and never on a standing mandate.",
                         None),
    "web_fact": ("Not an irlpls ask: this is already published on the web (a menu, opening hours, prices, a phone number). "
                 "Use a web search or the place's own website; irlpls only gets new observations of the physical world.",
                 "web search"),
    "history": ("Not an irlpls ask: irlpls only gets new observations of the physical world, never a past reading. A past "
                "reading is an archive question: ask the archive (USGS for river levels, a weather service for weather) or the "
                "provider's own archive.", "the provider's own archive"),
    "about_provider": ("This asks about a provider, not for new data: its Capture Card states the answer.", "the card page"),
    "no_provider": ("No provider in the catalog covers this.", None),
}


def _dataset(req):
    """A dataset ask ('collect': many examples, or dataset words): captured wherever contributors are, so no single place."""
    import re
    from .match import DATASET
    return (req.quantity or 1) > 1 or bool(re.search(DATASET, (req.need or "").lower()))


def continue_url(card):
    """Where a person goes to do what an agent can't (sign up, get a key, ask for a quote)."""
    api = card.get("api") or {}
    return api.get("docs_url") or card.get("provider_url")


CALLABLE_MARGIN = 15   # a callable option is chosen over a better-fitting directory-only one only within this many fit points
WEAK_FIT = 30          # below this best fit, best() is the best-fitting option: callability never outbids a weak match
LIVE_MARGIN = 10       # ... or this many on a live route
READY_MARGIN = 5       # one-yes readiness (irlpls.readiness) breaks a near-tie only: within this many fit points of the pick


def _by_hand(opt, card):
    """What a person does when the best fit has no adapter for its route: order it from the provider directly. Carries
    the provider's docs link, the ready call when one exists, and the card's own documented endpoint for the step."""
    out = {"msg": f"{opt['name']} fits this ask best, but this SDK can't {'read' if opt['route'] == 'live' else 'order'} it: "
                  "order it by hand from the provider" + (f" ({opt['continue_url']})." if opt.get("continue_url") else "."),
           "docs_url": opt.get("continue_url")}
    if opt.get("prepared_call"):
        out["prepared_call"] = opt["prepared_call"]
    api = (card or {}).get("api") or {}
    want = ("search", "list", "fetch") if opt["route"] == "live" else ("quote", "order")
    ep = next((e for a in want for e in api.get("endpoints") or [] if e.get("action") == a and e.get("path")), None)
    if ep and api.get("base_url"):
        out["card_endpoint"] = {k: v for k, v in {"action": ep["action"], "method": ep.get("method"),
                                                  "url": api["base_url"].rstrip("/") + ep["path"] if ep["path"].startswith("/") else ep["path"],
                                                  "params": ep.get("params"), "notes": ep.get("notes")}.items() if v}
    return out


def _price(card):
    """The card's starting price: 0 for a provider that is free to use, None when the price isn't published."""
    if is_free(card):
        return 0
    p = card["pricing"].get("from_usd")
    return p if valid_money(p) and p else None


def _reasons(card, m, req, policy):
    """[(code, label, why)] for a provider: everything that rules it out for this request, best reason first."""
    from . import geo
    from .match import MIN_DESCRIPTION, timing
    out = []
    if (card.get("access") or {}).get("automation_allowed") is False:
        out.append(("terms", "bans agents", "its terms don't allow agents or automated use"))
    if req.targets_person and (card.get("capture") or {}).get("by") in ("people", "contributors") \
            and not (policy and policy.allow_person_targeting):
        out.append(("policy:allow_person_targeting", "targets a person", "sends people to watch or photograph a specific person"))
    for code, text in (policy.violations(card) if policy else []):
        label = POLICY_LABELS.get(code, "policy")
        if code == "policy:max_order_usd":
            label = "paid" if not valid_money(policy.max_order_usd) or policy.max_order_usd <= 0 else "over budget"
        out.append((code, label, text))
    parts = m["parts"]
    if parts.get("outside_coverage"):
        wrong_state = bool(req.state) and geo.covers(card, req.country_code) is not False
        where = req.state.title() if wrong_state else req.country_code.upper()
        out.append(("outside_coverage", "wrong state" if wrong_state else "wrong country", f"doesn't cover {where}"))
    t = timing(card, req)
    if t[1] and t[1] != "delivery time not published" and (t[0] == 0 or m["fit"] == 0):
        out.append(("too_slow", "too slow", t[1].replace("capture takes unknown", "no capture time published")))
    d = parts.get("description")
    if d is not None and d < MIN_DESCRIPTION:
        missed = [w for w in req.terms if w not in parts.get("matched", []) and w not in parts.get("custom", [])]
        out.append(("no_match", "no match", ("its card doesn't mention " + ", ".join(missed)) if missed
                    else "doesn't offer the kind of data asked for"))
    a = adapters.get(card["id"])
    if a and a.env and not a.configured() and not _protocol(card):
        out.append(("needs_key", "needs key", f"set {a.env} to call it"))
    return sorted(out, key=lambda r: REASON_ORDER.index(r[0].split(":")[0]))


def _blocked(card, req, policy):
    """Rules that keep a provider out of the plan whatever its fit: terms that ban agents, a request that targets a
    specific person going to a people-based provider (unless the policy allows it)."""
    out = []
    if (card.get("access") or {}).get("automation_allowed") is False:
        out.append("terms")
    if req.targets_person and (card.get("capture") or {}).get("by") in ("people", "contributors") \
            and not (policy and policy.allow_person_targeting):
        out.append("policy:allow_person_targeting")
    return out


def plannable(card, req, policy=None):
    """Whether a ranked provider goes into the plan at all: not blocked (_blocked), a route for the mode asked, and a
    timing above 0 (a capture can't answer "now"). Evidence never changes it. Mirrored by IRLMatch.plannable (src/match.js)."""
    from .match import timing
    return not _blocked(card, req, policy) and bool(_route(card, req)) and timing(card, req)[0] != 0


_ANSWERABLE = ("place", "lat", "lon", "within_minutes", "quantity", "country_code", "state", "mode")
_KEY_OK = __import__("re").compile(r"[\x21-\x7e]{1,255}")


def _check_key(key):
    """An idempotency key is 1-255 printable ASCII characters with no spaces; anything else could let two orders share a key."""
    if not isinstance(key, str) or not _KEY_OK.fullmatch(key):
        raise JobError("invalid_idempotency_key", "the idempotency key must be 1-255 printable ASCII characters without spaces",
                       mcp="pass a short id such as order-2026-10-29-1 as idempotency_key", cli="--idempotency-key order-1",
                       python="idempotency_key='order-1'")
    return key


def _quoted_total(dry):
    """The quote's firm USD total as a trusted Decimal, or None: a real int/float (not a bool), finite and not negative,
    taken by its shortest repr (never through float arithmetic), in USD (adapters.base.quote_price). A price in any
    other currency is never a USD total, so it fails every max_order_usd check."""
    return adapters.base.usd_total(dry)


def _price_shown(dry):
    """The dry run's provider price as shown to a person: {amount (decimal string), currency, firm}, or None. Firm only
    in USD; EUR, KRW, GBP, credits or USDC stay indicative and keep their own currency."""
    amt, cur, firm = adapters.base.quote_price(dry)
    if amt is None:
        return None
    return {"amount": format(amt.normalize(), "f"), "currency": cur or "unstated", "firm": firm}


def _prepared(a, req, route, operation=None):
    """The adapter's ready call for this ask (offline; credentials only named), or None. A failing hook never breaks a plan."""
    prep = getattr(a, "prepare", None) if a else None
    if not callable(prep) or req is None:
        return None
    try:
        rd = _req_dict(req)
        pc = prep(rd, route) if operation is None else prep(rd, route, operation=operation)
    except Exception:
        return None
    return pc if isinstance(pc, dict) and pc.get("url") else None


def _sdk_needs(a):
    """What the reference library's adapter needs for readiness.readiness_for: {env, wallet, note}, or None (no adapter)."""
    if not a:
        return None
    return {"env": a.env, "wallet": bool(getattr(a, "needs_wallet", False)), "note": getattr(a, "readiness_note", None)}


def _req_dict(req):
    """The request as adapters see it (ready calls, dry runs and orders alike): Request.to_dict() plus `deliver_by`, the
    deadline as ISO 8601 UTC, fixed once per within_minutes so the ready call and the order carry the same date."""
    d = req.to_dict()
    wm = getattr(req, "within_minutes", None)
    if wm and isinstance(wm, (int, float)) and wm > 0:
        if getattr(req, "_deadline_for", None) != wm:
            req._deadline_for = wm
            req._deliver_by = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60 * wm))
        d["deliver_by"] = req._deliver_by
    return d


def _missing_questions(card_id, missing):
    """An order's missing details as blocking questions answered through submit's spec (or the place, for an address)."""
    out = []
    for m in missing or []:
        field_, _, what = str(m).partition(":")
        field_ = field_.strip() or "spec"
        q = {"field": field_, "blocking": True, "ask": (what.strip() or str(m)), "in": "spec",
             "why": f"{card_id} can't be ordered without it; nothing is sent until it is given."}
        if field_ == "address":
            q["hint"] = "answer place with the full street address (irlpls_answer / irlpls answer --place), or pass spec.address"
        else:
            q["hint"] = f"pass spec.{field_} to irlpls_submit (CLI: irlpls submit JOB --spec '{{\"{field_}\": ...}}')"
        out.append(q)
    return out


def _card_price(card, quantity):
    """An indicative total from the card's published starting price, when the provider gave no quote: {amount, currency,
    firm: false, source: "card"}, or None when the card publishes none."""
    p = (card or {}).get("pricing") or {}
    fu = p.get("from_usd")
    if is_free(card or {}):
        return {"amount": "0", "currency": "USD", "firm": False, "source": "card"}
    if not valid_money(fu) or not fu:
        return None
    from decimal import Decimal
    q = quantity if isinstance(quantity, int) and quantity > 0 else 1
    amt = (Decimal(repr(fu)) if isinstance(fu, float) else Decimal(fu)) * q
    out = {"amount": format(amt.normalize(), "f"), "currency": "USD", "firm": False, "source": "card"}
    if p.get("unit"):
        out["unit"] = str(p["unit"])[:80]
    return out


def _dec(x):
    """Money from a number, a Decimal or a decimal string (an MCP argument), or None. Never NaN, inf or negative."""
    if isinstance(x, str):
        from decimal import Decimal, InvalidOperation
        try:
            x = Decimal(x.strip())
        except InvalidOperation:
            return None
    return as_decimal(x)


def _takes_operation(a):
    import inspect
    try:
        ps = inspect.signature(a.prepare).parameters
    except (TypeError, ValueError):
        return False
    return "operation" in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())


def _prepared_all(a, req, route):
    """{operation: ready call} for every operation the adapter builds for this route (read, quote, order, status). A call
    is kept only when it says it is that operation, so an adapter that ignores `operation` adds nothing twice."""
    if not a or req is None or not _takes_operation(a):
        return {}
    out = {}
    for op in adapters.base.OPERATIONS:
        pc = _prepared(a, req, route, op)
        if pc and pc.get("operation") == op:
            out[op] = pc
    return out


# Bare place names that exist in many countries: a geocoder picks one arbitrarily, so the person must say which.
AMBIGUOUS_PLACES = frozenset((
    "springfield", "georgetown", "franklin", "clinton", "greenville", "bristol", "salem", "columbia", "arlington",
    "richmond", "kingston", "alexandria", "cambridge", "newcastle", "birmingham", "manchester", "victoria",
    "santiago", "san jose", "san juan", "santa cruz", "san antonio", "la paz", "córdoba", "cordoba", "valencia",
    "aurora", "auburn", "lincoln", "madison", "washington", "troy", "oxford", "perth", "melbourne", "dublin",
    "durham", "hamilton", "newport", "windsor", "portsmouth", "rochester", "glasgow", "lancaster", "york",
))


def ambiguous_place(r):
    """True when the place is a bare, many-countries name with no country, state or coordinates to settle it."""
    if r is None or not isinstance(r.place, str) or r.lat is not None or getattr(r, "country_code", None):
        return False
    if getattr(r, "state", None) or getattr(r, "anywhere", False):
        return False
    return r.place.strip().lower() in AMBIGUOUS_PLACES


# Text that tries to steer the agent reading the plan instead of describing a need. Flagged, never obeyed.
_INJECTION = __import__("re").compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|directions)|"
    r"disregard (?:all |any |the )?(?:previous|prior|above|earlier)|forget (?:your|all|the) (?:instructions|rules)|"
    r"\b(?:place|submit|confirm|approve|authori[sz]e)\s+(?:the|an?|this|my)\s+order\b|"
    r"\bpay\s+(?:me\s+)?\$\s*\d|\bsystem prompt\b|\byou must (?:now )?(?:pay|order|buy|send)\b", __import__("re").I)


def injection_in(*texts):
    for t in texts:
        m = isinstance(t, str) and _INJECTION.search(t)
        if m:
            return m.group(0)
    return None


_RE = __import__("re")
_NUM = r"(\d+(?:[.,]\d+)?)"
_BUDGET = [
    _RE.compile(r"\b(?:max(?:imum)?|budget(?:\s+of|\s+is)?|up\s*to|under|below|at\s+most|no\s+more\s+than|less\s+than|cap(?:ped)?\s+at|spend(?:ing)?)"
                r"\s*(?:of\s+)?(?:\$\s*" + _NUM + r"|" + _NUM + r"\s*(?:\$|usd|dollars?|bucks)\b)", _RE.I),
    _RE.compile(r"\$\s*" + _NUM + r"\s*(?:max(?:imum)?|limit|cap|tops|or\s+(?:less|under))\b", _RE.I),
]
_FREE = _RE.compile(r"\bfor\s+free\b|\bfree\s+of\s+charge\b|\bat\s+no\s+cost\b|\bzero\s+budget\b|\bno\s+budget\b", _RE.I)


def budget_in(text):
    """The ceiling the ask itself states ('max $20', 'budget $10', '$1 max', 'under 5 dollars', 'for free'), as a
    Decimal of US dollars, else None. Only an amount with a $ or a currency word counts ('under 30 minutes' is not money)."""
    t = str(text or "")
    for rx in _BUDGET:
        m = rx.search(t)
        if m:
            raw = next(g for g in m.groups() if g)
            return __import__("decimal").Decimal(raw.replace(",", "."))
    return __import__("decimal").Decimal(0) if _FREE.search(t) else None


ABOUT_SELF = ("irlpls is a registry and router: it lists real-world data providers with what each one states about itself "
              "(its Capture Card), matches your ask to them, and shows you the options. It verifies nothing itself: providers "
              "state their own evidence, and a card marks each claim 'claimed' or 'evidenced'. It captures no data, holds no "
              "media, and has no overall quality score. Nothing can buy rank. Read the standard and how provenance is carried "
              "before you rely on it: https://irlpls.com/standard.html and https://irlpls.com/chain.html")
_TRUST_Q = _RE.compile(r"\b(?:real|legit(?:imate)?|genuine|fake|scam|trust(?:ed|worthy)?|reliable|verified|safe|actually|who(?:'s| is) behind)\b", _RE.I)
_QSTART = _RE.compile(r"\W*(?:is|are|does|do|how|what|can|could|who|why|should)\b", _RE.I)


def about_extra(text, cards):
    """A trust question the matcher's about_provider does not catch: ('self', None) for irlpls itself, (card, 'verification')
    for 'is this real? is the photo from Prolific actually taken by a person', else None."""
    t = str(text or "")
    if not (_QSTART.match(t) and _TRUST_Q.search(t)):
        return None
    low = t.lower()
    if _RE.search(r"(?<![a-z0-9])irlpls(?![a-z0-9])", low):
        return "self", None
    for c in cards:
        names = {c["id"].lower(), c["id"].lower().replace("-", " "), c["name"].lower(), c["name"].lower().split(" (")[0]}
        if any(len(n) > 3 and _RE.search(r"(?<![a-z0-9])" + _RE.escape(n) + r"(?![a-z0-9])", low) for n in names):
            return c, "verification"
    return None


_PARTS = (  # kind -> (pattern, label, route class that plans it)
    ("read", r"\b(?:weather|temperature|air quality|pm2\.5|river|water level|wave|tide|ship|vessel|traffic|wind|rain|snow|fires?|earthquakes?)\b", "a live reading", "live"),
    ("capture", r"\b(?:photos?|pictures?|photographs?|videos?|footage|drone|human|someone|count(?:ing)? people|gate)\b", "a photo or on-site capture", "commission"),
)


def multi_part(need, best):
    """The multi_part warning when one ask holds a live reading and a commissioned capture: names the part planned and the
    part to send separately. None for a single-part ask."""
    low = str(need or "").lower()
    found = [(k, label, route) for k, rx, label, route in _PARTS if _RE.search(rx, low)]
    if len(found) < 2:
        return None
    planned = next((f for f in found if best and f[2] == best.get("route")), None)
    other = [f for f in found if f is not planned]
    head = f"Planned: {planned[1]} ({best['card_id']})" if planned else "Planned: none of the parts"
    return {"code": "multi_part",
            "msg": f"This ask has more than one part. {head}. Not planned: "
                   f"{' and '.join(o[1] for o in other)}. Send the other part as a separate request; irlpls plans one kind of "
                   "observation per request and never drops a part silently."}


GEOCODE_BUDGET_S = 2.5  # planning waits this long for the geocoder; a slower one leaves the place un-located, said aloud


def _geocode_timeboxed(place, budget=None):
    """geo.geocode in a worker thread, abandoned after the budget. (result, timed_out). The thread finishes into
    geo's cache, so the next plan of the same place is instant. A real error is returned as an unlocated place."""
    import threading
    from . import geo
    box = {}

    def run():
        try:
            box["g"] = geo.geocode(place)
        except Exception:
            box["g"] = None
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(GEOCODE_BUDGET_S if budget is None else budget)
    slow = t.is_alive()
    g = box.get("g")
    if g is None and slow:   # over budget: the offline gazetteer gets a turn, flagged approximate
        try:
            from . import places
            g = places.lookup(place)
            g = dict(g, approximate=True) if g else None
        except Exception:
            g = None
    return g, slow


class Job:
    def __init__(self, need, req, policy, why=None):
        self.id = uuid.uuid4().hex[:12]
        self.need, self.why, self.request, self.policy = need, why, req, policy
        self.state, self.plan, self.questions, self.choice = "clarifying", [], [], None
        self.quote, self.provider_response, self.results, self.log = None, None, [], []
        self.ruled_out, self.warnings, self.pending = [], [], []  # pending: asks still queued by a rate limit
        self.status_response, self.provider_status = None, None
        self.approvals = []   # a placed order's second, paid step waiting for the person (adapter.pending_approvals)
        self.orders = {}  # idempotency key -> the provider's response: a replay returns this and never orders again
        self.fps = {}     # idempotency key -> fingerprint of the order it was first used for (a different spec is refused)
        self.operator_strict = False  # set by the MCP server on its jobs: orders need IRLPLS_ALLOW_SUBMIT
        self.error, self.reason_code, self.protocol = None, None, None  # protocol: quote/order/delivery as exchanged
        self._pclients = {}
        if self.request is not None and not self.request.targets_person:
            from .need import detect_targets_person
            # the need may be split across the need, the why and the request fields
            self.request.targets_person = detect_targets_person(need, why)
        self._note(f"received: {need!r}")

    def _note(self, msg):
        self.log.append({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg})

    # -- clarify ---------------------------------------------------------------------------------
    def _clarify(self):
        r, qs = self.request, []
        if getattr(self, "refusal", None):   # refused or redirected: nothing to ask
            self.questions = []
            return True
        if r.lat is None and not r.place and not getattr(r, "anywhere", False):
            if _dataset(r):  # a dataset is captured wherever contributors are: the place is a preference, not a block
                qs.append({"field": "place", "blocking": False, "ask": "Where should it be captured? Say 'anywhere' if it doesn't matter.",
                           "why": "A dataset needs no single place; a place only narrows the providers. Assuming anywhere."})
            else:
                off = __import__("re").search(r"\b(mars|moon|venus|jupiter|saturn|neptune|uranus|pluto|titan|europa)\b", r.need or "", __import__("re").I)
                qs.append({"field": "place", "blocking": True,
                           "ask": "Where on Earth? A place name or lat/lon." if off else "Where? A place name or lat/lon.",
                           "why": (f"irlpls covers places on Earth only; {off.group(1)!r} is not one it can plan for." if off else
                                   "Every provider covers specific places; without one nothing can be matched or dispatched.")})
        if ambiguous_place(r):
            qs.append({"field": "place", "blocking": True,
                       "ask": f"Which {r.place}? Give the country (and state or region), or lat and lon.",
                       "why": f"{r.place!r} exists in several countries; irlpls will not guess one and rank providers for the wrong place."})
        elif r.mode == "live" and r.place and r.lat is None and not getattr(r, "anywhere", False):
            qs.append({"field": "lat", "blocking": False, "ask": f"Where exactly is {r.place!r}? Give lat and lon.",
                       "why": "The place name could not be put on the map (small places are not in the offline gazetteer, or "
                              "the geocoder was unreachable), so a live read has no coordinates to query yet."})
        if r.mode == "commission" and r.within_minutes is None:
            qs.append({"field": "within_minutes", "blocking": False, "ask": "By when do you need it?",
                       "why": "Changes which providers can deliver in time; assuming 7 days."})
        if r.mode == "commission" and (self.policy is None or self.policy.max_order_usd is None) and budget_in(self.need) is None:
            qs.append({"field": "max_order_usd", "blocking": False, "ask": "What's the most you'll spend on this order?",
                       "why": "Without a ceiling, orders always stop at a quote for a human to approve."})
        self.questions = qs
        return not any(q["blocking"] for q in qs)

    def answer(self, **fields):
        """Answer questions (from the requester's own model or a person): place, lat, lon, within_minutes, quantity, max_order_usd."""
        from . import geo
        for k, v in fields.items():
            if k == "max_order_usd":
                if not valid_money(v):
                    raise ValueError(f"max_order_usd must be a finite number >= 0, not {v!r}")
                self.policy = self.policy or Policy()
                self.policy.max_order_usd = as_decimal(v)  # Decimal from here on; never float arithmetic on money
            elif k == "targets_person":
                if v:  # monotonic: an answer can raise the flag, only a policy (allow_person_targeting) unlocks it
                    self.request.targets_person = True
                else:
                    self._note("ignored answer targets_person=False: only policy allow_person_targeting unlocks people-based providers")
            elif k in _ANSWERABLE:
                setattr(self.request, k, v)
            else:
                self._note(f"ignored answer {k!r}: not an answerable field")
        from .need import _where, anywhere, check_request, detect_targets_person
        if any(isinstance(v, str) for v in fields.values()) and detect_targets_person(*[v for v in fields.values() if isinstance(v, str)]):
            self.request.targets_person = True
        r = self.request
        if "place" in fields:
            # a new place replaces the old one: its coordinates, country and state go with it (unless given now)
            given = {k for k in ("lat", "lon", "country_code", "state") if fields.get(k) is not None}
            for k in ("lat", "lon", "country_code", "state", "subdivision"):
                if k not in given:
                    setattr(r, k, None)
            r.anywhere = False
            r.place_approximate = False
            self.warnings = [w for w in self.warnings if w.get("code") != "place_approximate"]
            if isinstance(fields["place"], str) and anywhere(fields["place"]):   # "anywhere": no place limit, never geocoded
                r.place, r.anywhere = None, True
        check_request(r)
        if r.place and r.lat is None and "place" in fields:
            g, _slow = _geocode_timeboxed(r.place)
            if g:
                r.lat, r.lon, r.country_code, r.state = g["lat"], g["lon"], g["country_code"], g.get("state")
                r.subdivision = g.get("subdivision")
                r.place_approximate = bool(g.get("approximate"))
            elif not r.country_code:   # offline: at least the country or state named in it ("Leeds, UK", "Austin, TX")
                _where(r.place, r)
        self._note(f"answered: {fields}")
        out = self._plan()
        _save(self)   # the CLI answers in its own process: the answered state must survive for choose and submit
        return out

    # -- propose ---------------------------------------------------------------------------------
    def _plan(self, cards=None):
        from . import card as get_card, registry
        from .match import in_scope, rank, score
        from .need import web_fact
        r = self.request
        self.refusal = None
        from .need import history
        if history(r.need):
            self.refusal = {"code": "history", "msg": REFUSALS["history"][0], "instead": REFUSALS["history"][1]}
        elif web_fact(r.need):
            self.refusal = {"code": "web_fact", "msg": REFUSALS["web_fact"][0], "instead": REFUSALS["web_fact"][1]}
        elif r.targets_person and not (self.policy and self.policy.allow_person_targeting):
            self.refusal = {"code": "person_targeting", "msg": REFUSALS["person_targeting"][0]}
        ready = self._clarify()
        hit = injection_in(r.need, self.why)
        if hit and not any(w.get("code") == "prompt_injection" for w in self.warnings):
            msg = (f"the request contains instruction-like text ({hit!r}); it was treated as plain text. irlpls never places "
                   "or pays for an order because a request says so: only submit(confirm=True) from the person does.")
            self.warnings.append({"code": "prompt_injection", "msg": msg})
            self._note(f"warning: prompt_injection: {msg}")
        if getattr(r, "place_approximate", False) and r.lat is not None and not any(w.get("code") == "place_approximate" for w in self.warnings):
            msg = (f"the place {r.place!r} was matched from an offline list of larger places and may be off: check the "
                   "coordinates, or pass lat and lon, before you rely on this plan.")
            self.warnings.append({"code": "place_approximate", "msg": msg})
            self._note(f"warning: place_approximate: {msg}")
        from .policy import cautions   # sensitive sites, children, drones near airports: plain notes, never a refusal
        for c in cautions(r.need, self.why):
            if not any(w.get("code") == c["code"] for w in self.warnings):
                self.warnings.append(c)
                self._note(f"caution: {c['code']}: {c['msg']}")
        self.plan, out = [], []
        cards = [c for c in (cards or registry().cards) if in_scope(c)]
        by_id = {c["id"]: c for c in cards}
        checks = health.load()   # the last daily read check (tools/healthcheck.py)
        ranked = rank(cards, self.request)
        from .match import about_answer, about_provider, unsupported
        from .need import ask_problem
        about = about_provider(r.need, cards)
        extra = None if about else about_extra(r.need, cards)
        if extra and not self.refusal and extra[0] == "self":
            self.refusal = {"code": "about_provider", "msg": ABOUT_SELF, "instead": "https://irlpls.com/standard.html"}
            self.questions, ready = [], True
        elif extra and not self.refusal:
            about = (extra[0], "verification")
        if about and not self.refusal:   # "Is Prolific verified?": answered from that card, never by routing to another provider
            self.refusal = {"code": "about_provider", "msg": about_answer(*about), "instead": f"https://irlpls.com/c/{about[0]['id']}.html"}
            self.questions, ready = [], True
        elif not self.refusal and not ranked and not ambiguous_place(r) and (ask_problem(r) or not any(q["blocking"] for q in self.questions)) and unsupported(r, ranked):
            # nothing ranks at all (the matcher ruled every card out): say so rather than name a weak default
            self.refusal = {"code": "no_provider", "msg": unsupported(r, ranked) + (" irlpls cannot get this today." if unsupported(r, ranked).startswith("No provider") else ""), "instead": None}
            self.questions, ready, ranked = [], True, []
        kept = {m["id"] for m in ranked}
        if r.lat is None and not r.place and not getattr(r, "anywhere", False) and __import__("re").search(
                r"\b(mars|moon|venus|jupiter|saturn|neptune|uranus|pluto|titan|europa)\b", r.need or "", __import__("re").I):
            ranked, kept = [], set()   # "weather on Mars": the question says why; no Earth provider is offered meanwhile
        if (self.refusal or {}).get("code") in ("web_fact", "history", "about_provider") or ambiguous_place(r):
            ranked, kept = [], set()   # ambiguous place: rank nothing until the person says which one   # a web page answers it: no provider is planned, least of all a person sent to look
        for m in ranked:
            c = get_card(m["id"]) or by_id[m["id"]]
            if not plannable(c, self.request, self.policy):
                kept.discard(c["id"])
                continue
            route = _route(c, self.request)
            a = adapters.get(c["id"])
            proto = _protocol(c)
            callable_ = bool(proto) or bool(a and ((route == "live" and a.can_live) or (route == "commission" and a.can_commission)))
            if proto:
                a = None  # the protocol replaces the adapter for this provider
            self.plan.append({
                "card_id": c["id"], "name": c["name"], "route": route, "fit": m["fit"], "gaps": m["gaps"],
                "policy": self.policy.check(c) if self.policy else [],
                "price": _price(c), "price_unit": c["pricing"].get("unit"),
                "speed": c["delivery"]["speed"],
                "proof": {k: c["verification"][k]["level"] for k in ("time", "location", "content")},
                "sdk": {"callable": callable_, "needs_key": bool(a and (a.env or a.needs_wallet) and not a.configured()),
                        "needs_wallet": bool(a and a.needs_wallet), "env": a.env if a else None,
                        "protocol": proto["endpoint"] if proto else None},
                "continue_url": continue_url(c),
                "readiness": readiness_for(c, _sdk_needs(a)),
            })
            rc = health.summary(c["id"], checks)
            if rc:
                self.plan[-1]["read_check"] = rc
            pc = _prepared(a, self.request, route)
            if pc:
                self.plan[-1]["prepared_call"] = pc
            pcs = _prepared_all(a, self.request, route)
            if pc and pc.get("operation") not in pcs:
                pcs[pc["operation"]] = pc
            if pcs:
                self.plan[-1]["prepared_calls"] = pcs
        # Everyone else, with why. Closest misses first: fewest reasons, then best description match.
        for c in cards:
            if c["id"] in kept:
                continue
            m = score(c, self.request)
            rs = _reasons(c, m, self.request, self.policy) or [("no_match", "no match", "doesn't fit this request")]
            if (self.refusal or {}).get("code") == "web_fact":
                rs = [("unsupported", "web fact", "already published on the web: use a web search")] + rs
            elif (self.refusal or {}).get("code") == "history":
                rs = [("unsupported", "history", "a past reading is an archive question, not a new observation")] + rs
            o = {"card_id": c["id"], "name": c["name"], "reason": rs[0][0], "label": rs[0][1], "why": rs[0][2], "reasons": [r[0] for r in rs]}
            if "needs_key" in o["reasons"]:
                o["continue_url"] = continue_url(c)
            out.append((o, len(rs), -(m["parts"].get("description") or 0)))
        # the refusal's own reason first (a person-targeting ask: the people-based providers it was kept from)
        first = {"person_targeting": "policy:allow_person_targeting", "web_fact": "unsupported", "history": "unsupported"}.get((self.refusal or {}).get("code"))
        self.ruled_out = [o for o, *_ in sorted(out, key=lambda x: (x[0]["reason"] != first, x[1], x[2], x[0]["card_id"]))]
        self.state = "proposed" if ready else "clarifying"
        if self.choice is not None:   # a re-plan keeps the person's choice when it is still an option
            self.choice = next((o for o in self.plan if o["card_id"] == self.choice["card_id"]), None)
        best = self.best()
        if (not self.plan and not self.refusal and not ambiguous_place(r) and not any(q["blocking"] for q in self.questions)
                and not self._policy_emptied(cards, ranked)):
            # nobody can do it: a refusal, not a 'proposed' job that only asks for lat/lon
            from .match import NO_PROVIDER
            self.refusal = {"code": "no_provider", "msg": NO_PROVIDER + " irlpls cannot get this today.", "instead": None}
            self.questions, ready = [], True
        if self.refusal:
            self.reason_code = self.refusal["code"]
            if self.refusal["code"] in ("web_fact", "history", "about_provider", "no_provider", "person_targeting"):
                if self.refusal["code"] == "person_targeting":
                    self.plan, best, self.choice = [], None, None   # a refusal lists nothing: no provider is offered for it
                self.state = "failed"
            elif not best:
                self.state = "failed"   # nothing compliant to propose: say why instead of an empty plan
            self._note(f"{'refused' if self.refusal['code'] == 'person_targeting' else 'redirected'}: {self.refusal['msg']}")
        elif self.reason_code in REFUSALS:
            self.reason_code = None
        if best and not best["sdk"]["callable"]:
            best["by_hand"] = _by_hand(best, by_id.get(best["card_id"]) or get_card(best["card_id"]))
            self._note(f"best fit {best['card_id']} has no {best['route']} adapter: order it by hand ({best.get('continue_url')})")
        self._read_check_warning(best)
        self.warnings = [w for w in self.warnings if w.get("code") != "multi_part"]
        mp = multi_part(r.need, best)
        if mp:
            self.warnings.append(mp)
            self._note(f"warning: multi_part: {mp['msg']}")
        dr = self.default_reason(best)
        if dr:
            self._note(f"default: {dr}")
        self._note(f"{self.state}: {len(self.plan)} options, {len(self.ruled_out)} ruled out"
                   + (f", best {best['card_id']} via {best['route']}" if best else ""))
        return self

    def _policy_emptied(self, cards, ranked):
        """True when the plan is empty only because the person's policy excluded every ranked provider (then ruled_out says
        so, and it is not 'nobody does this')."""
        if not self.policy or not ranked:
            return False
        by_id = {c["id"]: c for c in cards}
        return any(self.policy.check(by_id[m["id"]]) for m in ranked if m["id"] in by_id)

    def default_reason(self, opt=None):
        """Why the default (best) option is not the top plan entry, or None when it is. Said aloud, never silent."""
        opt = opt or self.best()
        ok = [o for o in self.plan if not o["policy"]]
        if not opt or not ok:
            return None
        top = max(ok, key=lambda o: o["fit"] or 0)
        if opt is top or opt["card_id"] == top["card_id"]:
            return None
        if self.choice is not None and opt is self.choice:
            return f"you chose {opt['card_id']}"
        why = []
        if not top["sdk"]["callable"]:
            why.append(f"{top['card_id']} (fit {top['fit']}) has no {top['route']} adapter in this SDK, so it is order-by-hand")
        elif top["sdk"]["needs_key"] and not opt["sdk"]["needs_key"]:
            why.append(f"{top['card_id']} (fit {top['fit']}) needs a key you have not set")
        else:
            why.append(f"{opt['card_id']} is within a few fit points of {top['card_id']} and is closer to one yes")
        return f"{opt['card_id']} (fit {opt['fit']}) is the default instead of {top['card_id']}: " + why[0] + ". Choose any option with choose()."

    def best(self):
        """Best policy-compliant option. One this SDK can call wins only when its fit is close to the best fit overall
        (within CALLABLE_MARGIN, or LIVE_MARGIN on a live route); otherwise the best-fitting option wins even if it is
        directory-only, and _plan marks it `by_hand` (order it from the provider yourself). Among the callable ones, a
        provider you can call right now wins a near-tie (within 5 fit, or 10 on a live route, where an answer now beats a
        slightly better match that first needs an account). Last, one-yes readiness breaks a near-tie (READY_MARGIN):
        a provider the person can use with one yes beats one that needs an account first, never a much better fit.
        A provider picked with choose() is what best() returns from then on (it is what submit() runs)."""
        chosen = getattr(self, "choice", None)
        if chosen is not None and any(o is chosen for o in self.plan):
            return chosen
        ok = [o for o in self.plan if not o["policy"]]
        if not ok:
            return None
        fit = lambda o: o["fit"] or 0

        def nearest_yes(pool, pick):
            """Within READY_MARGIN fit of the pick, the option closest to one yes (readiness); the pick keeps any tie."""
            near = [o for o in pool if fit(o) >= fit(pick) - READY_MARGIN]
            rr = lambda o: readiness_rank((o.get("readiness") or {}).get("level"))
            return min(near, key=lambda o: (rr(o), -fit(o), o is not pick))

        top = max(ok, key=fit)   # the first option with the highest fit (plan order breaks ties)
        # A near-tie never crosses route class: a collect ask must not default to a live camera because it is callable.
        # So the default is always the best-fitting option or a close callable one of the same route (plan[0]'s class).
        same = [o for o in ok if o.get("route") == top.get("route")]
        ok = same or ok
        if fit(top) < WEAK_FIT:   # nothing fits: don't trade the best-fitting option for a merely callable weak one
            return top
        callable_ = [o for o in ok if o["sdk"]["callable"]
                     and fit(o) >= fit(top) - (LIVE_MARGIN if o.get("route") == "live" else CALLABLE_MARGIN)]
        if not callable_:
            return nearest_yes(ok, top)
        lead = fit(callable_[0])
        win = 10 if callable_[0].get("route") == "live" else 5
        ready = [o for o in callable_ if not o["sdk"]["needs_key"] and fit(o) >= lead - win]
        return nearest_yes(ready, ready[0]) if ready else nearest_yes(callable_, callable_[0])

    def choose(self, card_id):
        self.choice = next((o for o in self.plan if o["card_id"] == card_id), None)
        if self.choice is None:
            raise JobError("not_in_plan", f"{card_id!r} is not in this job's plan; options: {', '.join(o['card_id'] for o in self.plan) or 'none'}",
                           mcp="pick a card_id from the job's plan (ruled_out says why the others are out)",
                           cli="irlpls choose JOB CARD_ID with a card_id from the plan", python="job.choose(card_id) with an id from job.plan")
        if self.protocol is None or self.protocol.get("card_id") != card_id:
            self.protocol = {"card_id": card_id, "chosen": True} if self.choice["sdk"].get("protocol") else self.protocol
        self._note(f"chose {card_id}")
        self._read_check_warning(self.choice)
        _save(self)   # the CLI runs choose and submit as separate processes: the choice must survive
        return self

    def _check_live_results(self, opt, kw=None):
        """After a live read. (1) A made-to-order ask (a new image, a capture by a deadline) answered from a live feed
        takes only observations made after the ask: an older scene is an archive item, warned about and not delivered as
        new (AGENTS 2: not an archive search). (2) No result at all ends in a plain warning saying where and how far back
        the provider looked, never a silent empty 'delivered'."""
        from datetime import datetime, timezone

        def when(x):
            try:
                t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

        r, name = self.request, opt.get("name") or opt["card_id"]
        if r.mode == "commission" and self.results:
            asked = when(self.log[0]["t"]) if self.log else None
            old = [o for o in self.results if asked and (when(o.get("phenomenon_time")) is None or when(o.get("phenomenon_time")) < asked)]
            if old:
                times = [o.get("phenomenon_time") for o in old if when(o.get("phenomenon_time"))]
                newest = max(times, key=when) if times else None
                self.results = [o for o in self.results if o not in old]
                self.warnings.append({"code": "not_new", "card_id": opt["card_id"], "items": len(old),
                                      "msg": f"{name} has nothing captured after your ask: its newest item"
                                             + (f" is from {newest}" if newest else " has no capture time")
                                             + ", before you asked (an archive item, not a new capture). Order a new capture "
                                               "from a made-to-order option instead."})
                self._note(f"warning: not_new: {len(old)} item(s) from {opt['card_id']} predate the ask; not delivered as new")
                if not self.results:
                    self.state, self.reason_code = "failed", "no_new_observation"
                return
        if not self.results:
            import inspect
            a, kw = adapters.get(opt["card_id"]), dict(kw or {})
            try:
                ps = inspect.signature(a.live).parameters if a else {}
            except (TypeError, ValueError):
                ps = {}
            d = lambda k: kw.get(k, ps[k].default if k in ps and ps[k].default is not inspect.Parameter.empty else None)  # noqa: E731
            radius = d("radius_km")
            days, max_age = d("days") or d("day_range"), d("max_age")
            back = (f" in the last {days} day{'s' if days != 1 else ''}" if days else
                    f" in the last {max_age // 3600} h" if isinstance(max_age, int) and max_age >= 3600 else " reporting now")
            where = r.place or (f"{r.lat:.3f}, {r.lon:.3f}" if r.lat is not None else "the place asked")
            msg = (f"No readings from {name}" + (f" within {radius:g} km of {where}" if isinstance(radius, (int, float)) else f" near {where}")
                   + back + ". That is what it reported, not proof that nothing is there; try another option in the plan.")
            self.warnings.append({"code": "no_results", "card_id": opt["card_id"], "msg": msg})
            self._note(f"warning: no_results: {msg}")

    def _read_check_warning(self, opt):
        """A plan warning when the provider picked (best or chosen) failed its last daily read check: the call may be
        broken right now. Once per provider per job; it never blocks or reranks."""
        rc = (opt or {}).get("read_check")
        if not rc or rc.get("ok") is not False:
            return
        if rc.get("result") == "degraded":
            msg = (f"{opt['name']}'s last read check answered, but not as expected ({str(rc.get('checked_at') or '')[:10]}): "
                   f"the reply was empty or missing fields. We cannot tell whether it changed or has nothing now; expect gaps, or pick another option.")
        else:
            msg = (f"{opt['name']}'s last read check failed ({health.why(rc)}, {str(rc.get('checked_at') or '')[:10]}): "
                   f"a real read through its adapter did not work then. It may be down or changed; expect errors, or pick another option.")
        opt["warning"] = msg
        if not any(w.get("code") == "read_check_failed" and w.get("card_id") == opt["card_id"] for w in self.warnings):
            self.warnings.append({"code": "read_check_failed", "card_id": opt["card_id"], "msg": msg})
            self._note(f"warning: read_check_failed: {msg}")

    # -- execute ---------------------------------------------------------------------------------
    def submit(self, spec=None, confirm=False, idempotency_key=None, **kw):
        """Live routes run immediately (free). Commission routes return a quote/dry run unless confirm=True, and never
        send an order that violates the policy. A policy with max_order_usd and auto_submit lets the broker confirm
        orders within budget on its own (a standing mandate). Placing an order (confirm=True) needs an idempotency_key,
        so a retry can never order twice; a standing mandate uses one derived from this job."""
        from . import card as get_card, commission, live
        if self.state == "clarifying":
            raise JobError("needs_answer", "answer the blocking questions first: " + "; ".join(q["ask"] for q in self.questions if q["blocking"]),
                           mcp="call irlpls_answer with the field named in each question", cli="irlpls answer JOB --place '...' (or --lat/--lon, --within-minutes, --quantity)",
                           python="job.answer(place=...)", questions=[q for q in self.questions if q["blocking"]])
        opt = self.choice or self.best()
        if not opt:
            self.state = "failed"
            self._note("no policy-compliant provider")
            return self
        if opt["policy"]:  # never sent; reported, not raised (STANDARD.md section 5, rule 2)
            self.choice, self.state = opt, "failed"
            self._note(f"not sent: {opt['card_id']} violates policy: " + "; ".join(opt["policy"]))
            _save(self)
            return self
        if not opt["sdk"]["callable"]:
            self.state = "failed"
            self._note(f"{opt['card_id']} has no {opt['route']} adapter yet: order it by hand from the provider"
                       + (f" ({opt['continue_url']})" if opt.get("continue_url") else "") + "; see its card's api block")
            return self
        if opt["sdk"].get("protocol"):
            return self._submit_protocol(opt, spec, confirm, idempotency_key)
        r = self.request
        if opt["route"] == "live":
            asks = kw.pop("asks", None)
            if asks is not None:  # fan-out: many places, one provider; batched and paced (irlpls/ratelimit.py)
                from . import live_many
                many = live_many(opt["card_id"], asks, **kw)
                res = adapters.base.Results([dict(o, ask=i) for i, per in enumerate(many) if per for o in per], many.warnings)
                self.pending = list(many.pending)
            else:
                from . import http as _http
                try:
                    res = live(opt["card_id"], lat=r.lat, lon=r.lon, place=None if r.lat is not None else r.place, **kw)
                except (OSError, TimeoutError, _http.HTTPError, adapters.base.MissingCredentials) as e:   # said, not raised
                    code = "needs_key" if isinstance(e, adapters.base.MissingCredentials) else "provider_down"
                    status = getattr(e, "status", None) if isinstance(e, _http.HTTPError) else None
                    why = (f" (HTTP {status})" if status else " (timed out)" if isinstance(e, TimeoutError) or "timed out" in str(e)
                           else f" (no network route to the provider: {str(getattr(e, 'reason', '') or e)[:80]})"
                           if isinstance(e, OSError) and not isinstance(e, _http.HTTPError) else f" ({type(e).__name__})")
                    if code == "needs_key":
                        env = (opt.get("sdk") or {}).get("env")
                        why = (f" ({str(e)[:200]})" if "refused" in str(e) else
                               f" (needs {env}: set it in the environment, or choose a keyless option)" if env
                               else f" ({str(e)[:100] or 'needs an API key'})")
                    msg = (f"{opt['name']} could not be read" + why
                           + (": " if code == "needs_key" else ": your connection may be offline; ")
                           + "pick another option in the plan, or try again later.")
                    self.warnings = [{"code": code, "card_id": opt["card_id"], "msg": msg}]
                    self.choice, self.state, self.results = opt, "failed", []
                    self._note(f"live read from {opt['card_id']} failed: {code}")
                    _save(self)
                    return self
            self.warnings = [_warning(w, opt["card_id"]) for w in getattr(res, "warnings", [])]
            res = _dedupe([adapters.base.normalise(o) for o in res])
            if not res and not self.pending:   # an empty read is said, never passed off as an answer
                self.warnings.append({"code": "empty_read", "card_id": opt["card_id"],
                                      "msg": f"{opt['name']} answered but reported nothing near this place right now; "
                                             "try another option in the plan (irlpls_choose_provider) or a wider place."})
            # what was asked for first ("PM2.5" before PM10, "flow" before level); otherwise the provider's order
            asked = [t.lower() for t in r.terms + r.specifics]
            self.results = sorted(res, key=lambda o: not any(t in str(o.get("label", "")).lower() for t in asked))
            for w in self.warnings:
                self._note(f"warning from {opt['card_id']}: {w['code']}: {w['msg']}")
            c = get_card(opt["card_id"])
            for o in self.results:
                o["provider_proof"] = {k: c["verification"][k]["level"] for k in ("time", "location", "content")}
                o["sensor"] = {"kind": _sensor_kind(c)}
            self.state = "delivered"
            self._note(f"live read from {opt['card_id']}: {len(self.results)} observations")
            self._check_live_results(opt, kw)
        else:
            # one source of truth: the same spec the plan's ready call was built from (adapters.base.spec_from_request),
            # with the requester's own spec on top; never just lat/lon
            spec = {**adapters.base.spec_from_request(adapters.get(opt["card_id"]), _req_dict(r)), **dict(spec or {})}
            from .need import detect_targets_person, text_fields
            if not (self.policy and self.policy.allow_person_targeting) and detect_targets_person(*text_fields(spec)):
                self.state = "failed"
                self._note("not sent: the spec targets a specific person; set policy allow_person_targeting to unlock")
                _save(self)
                return self
            # a standing mandate only counts with a spending ceiling; without one, orders always stop at a quote
            auto = self._may_auto()
            if confirm and not idempotency_key:
                raise _key_needed()
            if confirm:
                self._operator_check()
            if idempotency_key is not None:
                _check_key(idempotency_key)
            self._confirm_requested = bool(confirm)
            guard = _OrderLock(self.id) if (confirm or auto) else _NoLock()
            with guard:   # check-then-act is atomic: two callers with one key cannot both order
                return self._submit_commission(opt, spec, confirm, idempotency_key, auto, commission, get_card, r)
        return self._finish(opt)

    def _submit_commission(self, opt, spec, confirm, idempotency_key, auto, commission, get_card, r):
        if True:
            if confirm or auto:
                key = idempotency_key or f"irlpls-{self.id}-{opt['card_id']}"
                spec.setdefault("idempotency_key", key)
                fp = _fingerprint(opt["card_id"], spec)
                self._load_orders()
                if key in self.orders:  # replay: return what the first call got, never order twice
                    if self.fps.get(key, fp) != fp:
                        raise _reused(key)
                    self._note(f"replayed key {key!r}: order already placed with {opt['card_id']}")
                    return self
                if self.state in ("submitted", "in_flight", "delivered"):
                    self._note(f"not sent: this job already placed an order ({self.state}); start a new job to order again")
                    return self
            try:
                out = self._commission(commission, opt["card_id"], spec, confirm or auto)
            except PolicyViolation as e:  # the quote broke the budget: nothing was placed
                out = {"dry_run": True, "blocked_by_policy": e.violations, "note": "Not sent. This order violates your policy."}
            except adapters.base.MissingDetails as e:  # the adapter refused to build an incomplete order: nothing was sent
                out = {"dry_run": True, "missing": e.missing, "note": "Not sent: " + "; ".join(e.missing)}
            if (confirm or auto) and isinstance(out, dict) and not out.get("dry_run"):
                self.orders[spec["idempotency_key"]] = out
                self.fps[spec["idempotency_key"]] = fp
                self._save_orders()
            self.provider_response = out
            if isinstance(out, dict) and out.get("dry_run"):
                shown = _price_shown(out)
                if shown:
                    if not shown["firm"]:
                        self._note(f"{opt['card_id']} priced it at {shown['amount']} {shown['currency']}: indicative, "
                                   + ("not in USD, never converted" if shown["currency"] != "USD" else "not a firm quote"))
                else:   # no provider price: the card's starting price, indicative; or null, said plainly
                    shown = _card_price(get_card(opt["card_id"]), r.quantity)
                self.quote = dict(out, price=shown)
                if shown is None:
                    self.quote["price_note"] = (f"{opt['name']} gives no price before an order and its card publishes none: "
                                                f"ask the provider ({opt.get('continue_url') or 'see its card'}).")
                if out.get("missing"):   # an incomplete order is never sent: ask for what is missing instead
                    self.quote["complete"] = False
                    self.questions = [q for q in self.questions if q.get("in") != "spec"] + _missing_questions(opt["card_id"], out["missing"])
                    self._note(f"not ready to order from {opt['card_id']}: missing " + "; ".join(out["missing"]))
                self.state = "quoted"
                self._note(f"quoted by {opt['card_id']}" + (f"; blocked: {out['blocked_by_policy']}" if out.get("blocked_by_policy") else ""))
            else:
                self.state = "submitted"
                self._note(f"order placed with {opt['card_id']}")
        return self._finish(opt)

    def _finish(self, opt):
        self.choice = opt
        _save(self)
        return self

    def _operator_check(self, what="placing an order"):
        msg = operator_refusal(self.operator_strict, what)
        if msg:
            raise JobError("submit_disabled", msg, mcp="Nothing was sent. Tell the person to place the order themselves, in their own "
                           "client or at the provider; this server only plans and quotes.",
                           cli="set IRLPLS_ALLOW_SUBMIT=1 and IRLPLS_MAX_ORDER_USD to allow orders",
                           python="set IRLPLS_ALLOW_SUBMIT=1 and IRLPLS_MAX_ORDER_USD to allow orders")

    def _eff_policy(self):
        """The policy the broker enforces: the job's own, with max_order_usd no looser than the operator's ceiling."""
        _, ceiling = operator_limits(self.operator_strict)
        pol = self.policy
        if ceiling is None:
            return pol
        if pol is None:
            return Policy(max_order_usd=ceiling)
        if pol.max_order_usd is not None and not valid_money(pol.max_order_usd):
            return pol   # invalid: fails closed downstream
        mine = as_decimal(pol.max_order_usd)
        if mine is not None and mine <= ceiling:
            return pol
        import dataclasses
        return dataclasses.replace(pol, max_order_usd=ceiling)

    def _may_auto(self):
        """A standing mandate needs a spending ceiling in the policy AND an operator who allows orders; it never lifts either."""
        return bool(self.policy and getattr(self.policy, "auto_submit", False) is True and valid_money(self.policy.max_order_usd)
                    and operator_limits(self.operator_strict)[0])

    def _commission(self, commission, card_id, spec, place):
        """Place (or quote) an order. Before anything is confirmed the broker reads a dry-run quote itself and fails closed:
        the total must be a real, finite, non-negative number within max_order_usd; NaN, negatives and booleans never pass."""
        pol = self._eff_policy()
        if place:   # read the dry run first: an order missing details the person must give is never sent
            dry = commission(card_id, spec, confirm=False, policy=pol)
            if isinstance(dry, dict) and dry.get("missing"):
                return dict(dry, note="Not sent: " + "; ".join(map(str, dry["missing"])))
        if place and pol and pol.max_order_usd is not None:
            if not valid_money(pol.max_order_usd):
                raise PolicyViolation(card_id, [f"invalid max_order_usd {pol.max_order_usd!r}"])
            dry = commission(card_id, spec, confirm=False, policy=pol)
            if isinstance(dry, dict) and dry.get("blocked_by_policy"):
                if self._confirm_requested:
                    raise PolicyViolation(card_id, dry["blocked_by_policy"])
                return dry
            total = _quoted_total(dry)
            if total is None or not (total <= as_decimal(pol.max_order_usd)):
                bad = ["no valid quoted total to check against max_order_usd" if total is None
                       else f"quote ${total:g} over max_order_usd ${pol.max_order_usd:g}"]
                if self._confirm_requested:
                    raise PolicyViolation(card_id, bad)
                out = dict(dry) if isinstance(dry, dict) else {"dry_run": True}
                out["blocked_by_policy"] = bad
                return out
        if place:   # the confirmed call must not cost more than the quote the person was shown
            shown = _quoted_total(self.quote) if isinstance(self.quote, dict) else None
            now = _quoted_total(dry) if isinstance(dry, dict) else None
            if shown is not None and now is not None and now > shown:
                raise PolicyViolation(card_id, [f"price rose from ${shown:g} to ${now:g} since the quote; ask the person again"])
        out = commission(card_id, spec, confirm=place, policy=pol)
        if place and isinstance(out, dict) and not out.get("dry_run") and pol is not None and valid_money(pol.max_order_usd):
            total = _quoted_total(out)   # re-check what the provider says it confirmed
            if total is not None and total > as_decimal(pol.max_order_usd):
                msg = f"the confirmed total ${total:g} is over the ceiling ${as_decimal(pol.max_order_usd):g}: check with the provider"
                self.warnings.append({"code": "over_ceiling", "card_id": card_id, "msg": msg})
                self._note("warning: over_ceiling: " + msg)
        return out

    def _orders_file(self):
        return HOME / "jobs" / f"{self.id}.orders.json"

    def _load_orders(self):
        try:
            d = json.loads(self._orders_file().read_text())
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        fps = d.pop("__fp__", None)
        self.orders = {**d, **self.orders}
        if isinstance(fps, dict):
            self.fps = {**fps, **self.fps}

    def _save_orders(self):
        try:
            _private_dir(HOME / "jobs")
            _atomic_write(self._orders_file(), json.dumps(dict(self.orders, __fp__=self.fps), default=str))
        except OSError:
            pass

    def refresh(self):
        """Ask the provider how a placed order is going (submitted / in_flight jobs only), and map its own status to
        in_flight, delivered or failed. A status the adapter can't map keeps the job in_flight; nothing is guessed."""
        if self.state not in ("submitted", "in_flight"):
            return self
        if self.protocol and self.protocol.get("order"):
            return self._refresh_protocol()
        from . import card as get_card
        cid = self.choice["card_id"]
        a = adapters.get(cid)
        try:
            resp = a.status(self.provider_response)
        except adapters.NotSupported as e:
            self._note(f"no status: {e}")
            return self
        self.status_response = resp
        raw = self.provider_status = a.provider_status(resp)
        state = a.map_status(raw)
        try:
            self.approvals = list(a.pending_approvals(resp) or []) if state not in ("delivered", "failed") else []
        except Exception:
            self.approvals = []
        for x in self.approvals:
            amt = _dec(x.get("amount"))
            x["amount"] = format(amt.normalize(), "f") if amt is not None else None
        if self.approvals:
            self._note(f"{cid} is waiting for your approval of a price ({len(self.approvals)} option(s)); nothing is accepted "
                       "until you call approve(approval_id, confirm=True)")
        if state == "delivered":
            items = _dedupe([adapters.base.normalise(o) for o in (a.deliverables(resp) or [])])
            if not items:  # delivered per the provider, but the status response carries no items: point to the order
                items = [adapters.base.observation(cid, "order", None, None, None, label=f"{cid} reports {raw}; fetch the data from the provider",
                                                   order_id=(self.provider_response or {}).get("id") if isinstance(self.provider_response, dict) else None)]
            c = get_card(cid)
            for o in items:
                o["provider_proof"] = {k: c["verification"][k]["level"] for k in ("time", "location", "content")}
                o["sensor"] = {"kind": _sensor_kind(c)}
            self.results = items
            self.state = "delivered"
        elif state == "failed":
            self.state = "failed"
        else:
            self.state = "in_flight"
        self._note(f"status from {cid}: {raw if raw is not None else 'none given'}"
                   + ("" if state else " (not mapped; still in flight)") + f" -> {self.state}")
        _save(self)
        return self

    # -- protocol providers (docs/protocol/PROTOCOL-DRAFT.md) ---------------------------------------------------------
    def _warn(self, code, msg):
        self.warnings.append({"code": code, "msg": msg})
        self._note(f"warning: {code}: {msg}")

    def _pclient(self, card_id):
        """A protocol client for this provider, built from its card as served at its own /.well-known/irlpls (draft
        section 3). The registry's copy is used only for an unclaimed card the policy accepts (include_unclaimed)."""
        if card_id in self._pclients:
            return self._pclients[card_id]
        from . import card as get_card
        from .protocol import client as pc
        c, fetched_from = get_card(card_id), None
        opts = dict(PROTOCOL.get("client") or {})
        if PROTOCOL.get("discover", True):
            try:
                fresh = pc.discover(pc.origin(c.get("provider_url") or ""), allow_private=opts.get("allow_private", False),
                                    allow_http=opts.get("allow_http", False), timeout=opts.get("timeout", 30))
                if not (isinstance(fresh, dict) and fresh.get("id") == card_id and pc.protocol_block(fresh)):
                    raise pc.ProtocolError({"code": "not_found", "detail": "the provider's /.well-known/irlpls has no protocol block for this card"}, 404)
                c, fetched_from = fresh, pc.origin(c["provider_url"]) + "/.well-known/irlpls"
            except pc.ProtocolError as e:
                if c.get("status") == "unclaimed" and (self.policy is None or self.policy.include_unclaimed):
                    self._warn("card_not_refetched", f"using the registry's unclaimed card for {card_id} ({e.code})")
                else:
                    raise
        cl = pc.Client.from_card(c, fetched_from=fetched_from, auth=(PROTOCOL.get("auth") or {}).get(card_id), **opts)
        self._pclients[card_id] = cl
        return cl

    def _wire_request(self, exact):
        """The Request as sent to a provider: no policy, no why, no requester. Coordinates are coarsened to about 1 km
        and the address left out unless `exact` (a provider the person chose, or an order being placed)."""
        r, d = self.request, {"need": self.request.need}
        for k in ("within_minutes", "quantity", "mode", "terms", "modalities", "require"):
            v = getattr(r, k, None)
            if v not in (None, [], ""):
                d[k] = v
        if r.country_code:
            d["country_code"] = r.country_code.upper()
        if r.lat is not None and r.lon is not None:
            d["lat"], d["lon"] = (r.lat, r.lon) if exact else (round(r.lat, 2), round(r.lon, 2))
        if exact and r.place:
            d["place"] = r.place
        if r.targets_person:
            d["targets_person"] = True
        return d

    def _constraints(self):
        """The policy as hard limits a provider can check (draft section 10). The Policy itself never leaves."""
        from .protocol.client import money
        pol, r, c = self.policy, self.request, {}
        if pol and pol.max_order_usd is not None and valid_money(pol.max_order_usd):
            c["max_total"] = money(pol.max_order_usd)
        rights = (["ai_training"] if pol and pol.need_ai_training else []) + (["resale"] if pol and pol.need_resale else [])
        if rights:
            c["rights_needed"] = rights
        if pol and pol.require:
            c["proof"] = dict(pol.require)
        if r.within_minutes:
            c["deliver_by"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60 * r.within_minutes))
        return c

    def _offer_violations(self, offer, card_id):
        """Re-check an offer against the policy (never trust a provider's claim that it complied): money as Decimal,
        a missing or malformed total is a violation, unstated rights are not granted, proof below what is required."""
        from .policy import LEVELS
        from .protocol.client import amount
        pol, out = self.policy, []
        if (offer.get("provider") or {}).get("card_id") != card_id:
            out.append(f"offer is for {(offer.get('provider') or {}).get('card_id')!r}, not {card_id!r}")
        if not pol:
            return out
        if pol.max_order_usd is not None:
            tot = amount(offer.get("total"))
            if not valid_money(pol.max_order_usd):
                out.append(f"policy:max_order_usd: invalid ceiling {pol.max_order_usd!r}")
            elif tot is None or (offer.get("total") or {}).get("currency") != "USD":
                out.append("policy:max_order_usd: no valid quoted total in USD to check")
            elif tot > as_decimal(pol.max_order_usd):
                out.append(f"policy:max_order_usd: quote ${tot} over ${pol.max_order_usd:g}")
        rights = offer.get("rights") or {}
        if pol.need_ai_training and rights.get("ai_training") != "allowed":
            out.append("policy:need_ai_training: the offer does not grant AI training")
        if pol.need_resale and rights.get("resale") != "allowed":
            out.append("policy:need_resale: the offer does not grant resale")
        proof = offer.get("proof") or {}
        for g, lvl in (pol.require or {}).items():
            have = proof.get(g, "none")
            if LEVELS.get(have, 0) < LEVELS[lvl]:
                out.append(f"policy:require: {g} is {have} in the offer, {lvl} required")
        return out

    def _problem(self, e, during):
        """A provider's problem becomes the job's error (data, never instructions); nothing is raised. A problem the
        provider sent is normalised first (protocol.client.sanitize_problem): known codes only, bounded text, and its
        questions kept only for Request fields and labelled source "provider", never passed off as irlpls's own."""
        from .protocol.client import sanitize_problem
        p = e.problem
        if p.get("source") == "provider" or not p.get("code") or p.get("code") not in _LOCAL_CODES:
            site = None
            try:
                site = urlsplit((self._pclients.get((self.protocol or {}).get("card_id")) or {}).card.get("provider_url") or "").hostname
            except AttributeError:
                pass
            p = sanitize_problem(p, p.get("status"), site)
        self.error = p
        if p.get("code") == "needs_input" and during == "quote":
            # the provider's questions replace the list, each labelled source "provider" by sanitize_problem
            self.questions = [dict(q, blocking=True) for q in p.get("questions") or [] if isinstance(q, dict)]
            self.state = "clarifying"
        elif during == "cancel":
            pass  # the order keeps its state; the error says why the cancel was refused
        else:
            self.state = "failed"
        self._note(f"{during} problem from provider: {p.get('code')} ({p.get('status')})")
        _save(self)
        return self

    def protocol_quote(self, card_id, exact=False):
        """Quote one protocol provider (no money, no dispatch). Returns (quote, offer, violations)."""
        from .protocol import client as pc
        cl = self._pclient(card_id)
        req = self._wire_request(exact)
        q = cl.quote(req, self._constraints() or None)
        for w in cl.warnings:
            self._warn("protocol", w)
        cl.warnings.clear()
        offers = [o for o in q.get("offers", []) if isinstance(o, dict)]
        offer = offers[0] if offers else None
        if offer is None:
            raise pc.ProtocolError({"code": "no_offer", "detail": "the quote holds no offer"}, 422)
        return q, offer, self._offer_violations(offer, card_id), req

    def _submit_protocol(self, opt, spec, confirm, idempotency_key):
        """submit() for a provider whose card declares a protocol block. Live: a direct order with max_total 0, free.
        Commission: a quote; with confirm (or a standing mandate within budget) an order at the provider's own
        endpoint, under an Idempotency-Key derived per provider from the requester's key and reused on retry."""
        if confirm and not idempotency_key:
            raise _key_needed()
        if confirm:
            self._operator_check()
        if idempotency_key is not None:
            _check_key(idempotency_key)
        with (_OrderLock(self.id) if (confirm or self._may_auto()) else _NoLock()):
            return self._submit_protocol_locked(opt, spec, confirm, idempotency_key)

    def _submit_protocol_locked(self, opt, spec, confirm, idempotency_key):
        from .protocol import client as pc
        cid = opt["card_id"]
        if spec:
            from .need import detect_targets_person, text_fields
            if not (self.policy and self.policy.allow_person_targeting) and detect_targets_person(*text_fields(spec)):
                self.state = "failed"
                self._note("not sent: the spec targets a specific person; set policy allow_person_targeting to unlock")
                _save(self)
                return self
            self._note("spec ignored for a protocol provider: the request and constraints carry the brief")
        self.choice = opt
        self.protocol = self.protocol if (self.protocol or {}).get("card_id") == cid else {"card_id": cid}
        auto = self._may_auto()
        pol = self._eff_policy()
        fp = _fingerprint(cid, {"request": _req_dict(self.request) if self.request is not None else None, "spec": spec})
        try:
            if opt["route"] == "live":
                base = idempotency_key or f"irlpls-{self.id}-live"
                key = pc.derive_key(base, cid)
                if key in self.orders:
                    self._note(f"replayed key {base!r}: order already placed with {cid}")
                    return self
                req, nonce = self._wire_request(exact=False), pc.new_nonce()
                body = {"request": req, "constraints": {"max_total": pc.money(0)}, "nonce": nonce}
                self.protocol.update(nonce=nonce, request_sha256=pc.request_sha256(req), key=key, approved_total="0")
                order = self._pclient(cid).order(body, idempotency_key=key)
                self.orders[key] = {"order_id": order["id"], "card_id": cid}
                self._save_orders()
                self._note(f"live order {order['id']} at {cid}'s protocol endpoint")
                return self._take_order(order)
            place = bool(confirm or auto)
            if place:
                base = idempotency_key or f"irlpls-{self.id}-{cid}"
                key = pc.derive_key(base, cid)
                self._load_orders()
                if key in self.orders:  # replay: return what the first call got, never order twice
                    if self.fps.get(key, fp) != fp:
                        raise _reused(base)
                    self._note(f"replayed key {base!r}: order already placed with {cid}")
                    return self
                if self.state in ("submitted", "in_flight", "delivered"):
                    self._note(f"not sent: this job already placed an order ({self.state}); start a new job to order again")
                    return self
                # STANDARD 14.2: refetch the card before ordering; a changed endpoint, key set or operator needs a new yes
                old = self._pclients.pop(cid, None)
                fresh = self._pclient(cid)
                if old is not None and _order_identity(old.card) != _order_identity(fresh.card):
                    self.quote = {"dry_run": True, "protocol": True, "blocked_by_policy": [
                        "the provider's card changed since the quote (endpoint, jwks_uri or operator); ask the person again"]}
                    self.state = "quoted"
                    self._warn("card_changed", "endpoint, jwks_uri or operator changed since the quote; not ordered")
                    _save(self)
                    return self
            exact = place or self.choice is not None and self.protocol.get("chosen")
            held = self.protocol.get("quote")
            reuse = place and held and self.protocol.get("exact") and not _expired(held)
            if reuse:
                q, offer, req = held, self.protocol["offer"], self.protocol["request"]
                bad = self._offer_violations(offer, cid)
            else:
                prior = (self.protocol.get("offer") or {}).get("total")
                q, offer, bad, req = self.protocol_quote(cid, exact=bool(exact))
                # a person approved the earlier price: a higher one on the re-quote needs a new yes
                if place and confirm and prior and pc.amount(offer.get("total")) is not None and pc.amount(prior) is not None \
                        and pc.amount(offer["total"]) > pc.amount(prior):
                    bad = bad + [f"price rose from {prior['amount']} to {offer['total']['amount']} {offer['total'].get('currency', '')}; ask again"]
            if place and not bad:
                bad = self._operator_offer_violations(offer, pol)
            self.protocol.update(quote=q, offer=offer, request=req, exact=bool(exact), request_sha256=pc.request_sha256(req))
            self.quote = {"dry_run": True, "protocol": True, "quote_id": q.get("id"), "offer": offer,
                          "blocked_by_policy": bad or None}
            if not place or bad:
                self.state = "quoted"
                self._note(f"quoted by {cid} via protocol" + (f"; blocked: {bad}" if bad else ""))
                _save(self)
                return self
            if not offer.get("firm"):
                if not (pol and valid_money(pol.max_order_usd)):
                    self.quote["blocked_by_policy"] = ["the offer is indicative (not firm) and no max_order_usd caps the order"]
                    self.state = "quoted"
                    self._note(f"not sent: {cid}'s offer is not firm and there is no spending ceiling")
                    _save(self)
                    return self
                body = {"request": req, "constraints": {"max_total": pc.money(pol.max_order_usd)}}
                approved = body["constraints"]["max_total"]
            else:
                body = {"quote_id": q["id"], "offer_id": offer["id"]}
                approved = offer["total"]
            nonce = pc.new_nonce()
            body.update(nonce=nonce, client_reference=self.id,
                        approval={"by": "person" if confirm else "standing_mandate",
                                  "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            self.protocol.update(nonce=nonce, key=key, approved_total=approved["amount"])
            order = self._pclient(cid).order(body, idempotency_key=key)
            self.orders[key] = {"order_id": order["id"], "card_id": cid}
            self.fps[key] = fp
            self._save_orders()
            self.provider_response = order
            self._note(f"order {order['id']} placed at {cid}'s protocol endpoint")
            return self._take_order(order)
        except pc.ProtocolError as e:
            return self._problem(e, "order" if (opt["route"] == "live" or confirm or auto) and self.protocol.get("key") else "quote")

    def _operator_offer_violations(self, offer, pol):
        """An offer's total against the ceiling in force (the policy's, or the operator's if lower), in USD only."""
        if pol is None or not valid_money(pol.max_order_usd):
            return []
        t = (offer or {}).get("total") or {}
        if t.get("currency") != "USD":
            return [f"policy:max_order_usd: the offer is in {t.get('currency') or 'no stated currency'}, not USD"]
        try:
            amt = __import__("decimal").Decimal(str(t["amount"]))
        except Exception:
            return ["policy:max_order_usd: the offer has no valid total"]
        if not valid_money(amt) or amt > as_decimal(pol.max_order_usd):
            return [f"policy:max_order_usd: {t.get('amount')} over {pol.max_order_usd}"]
        return []

    def _take_order(self, order):
        """Order state -> job state, one to one (failed keeps the provider's reason_code)."""
        from decimal import Decimal

        from .protocol.client import amount
        self.protocol["order"] = self.status_response = order
        self.state = order["state"]
        self.provider_status = order.get("provider_status")
        self.reason_code = order.get("reason_code") if order["state"] == "failed" else None
        want = self.protocol.get("request_sha256")
        if want and order.get("request_sha256") != want and not self.protocol.get("request_mismatch"):
            self.protocol["request_mismatch"] = True
            self._warn("request_mismatch", "the Order's request_sha256 is not the hash of the request this job sent; "
                       "its receipts will not verify")
        tot, ok = amount(order.get("total")), self.protocol.get("approved_total")
        cur = (order.get("total") or {}).get("currency") if isinstance(order.get("total"), dict) else None
        if ok is not None and order["state"] != "failed" and (tot is None or cur != self.protocol.get("approved_currency", "USD")
                                                                or tot > Decimal(ok)):
            why = "has no valid total" if tot is None or cur is None else f"total {tot} {cur} is over the approved {ok}"
            self._warn("over_total", f"the provider's order {why}; cancelling")
            self.cancel()
            return self
        delivered = (order.get("progress") or {}).get("delivered") or 0
        if order["state"] == "delivered" or (order["state"] == "in_flight" and delivered):
            d = order.get("delivery")
            if not isinstance(d, dict):
                from .protocol.client import ProtocolError
                try:
                    d = self._pclient(self.protocol["card_id"]).delivery(order["id"])
                except ProtocolError as e:
                    self._warn("delivery", f"could not fetch the delivery: {e.code}")
                    d = None
            if d:
                self._take_delivery(d)
        self._note(f"order {order['id']}: {order['state']}" + (f" ({self.reason_code})" if self.reason_code else ""))
        _save(self)
        return self

    def _take_delivery(self, d):
        """Results with the provider's per-stage provenance, the relays the data passed through, and each item's
        receipt check (signature with the card's keys; bound to our nonce, the request hash and the media hash)."""
        from . import card as get_card
        cid = self.protocol["card_id"]
        cl = self._pclient(cid)
        checks = cl.verify_delivery(d, nonce=self.protocol.get("nonce"), request_sha256=self.protocol.get("request_sha256"))
        items = [it for it in d.get("items", []) if isinstance(it, dict)] if isinstance(d.get("items"), list) else []
        ids = [it.get("id") for it in items]
        if len(set(map(str, ids))) != len(ids):
            self._warn("provider_error", "the delivery repeats an item id: malformed; no item is shown as verified")
        c = cl.card or get_card(cid)
        served = {k: (c.get("verification") or {}).get(k, {}).get("level") for k in ("human", "application", "camera", "time", "location", "content", "corroboration")}
        reg = (get_card(cid) or {}).get("verification") or {}
        rank = {"none": 0, "claimed": 1, "evidenced": 2}
        # the provider's served card can't raise its own levels at delivery: take the lower of it and the registry's copy
        levels = {k: min((v, (reg.get(k) or {}).get("level") or v), key=lambda x: rank.get(x, 0)) if v else v
                  for k, v in served.items()}
        prov = d.get("provenance") or {}
        if prov.get("card_id") not in (None, cid):
            self._warn("provenance", f"delivery provenance names card {prov.get('card_id')!r}, not {cid!r}")
        for st in prov.get("stages") or []:
            for g, v in (st.get("guarantees") or {}).items():
                if isinstance(v, dict) and v.get("basis") == "card" and levels.get(g) and v.get("level") != levels[g]:
                    self._warn("provenance", f"stage {st.get('stage')}: {g} stated {v.get('level')} on the card's basis; the card says {levels[g]}")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        out = []
        for it, check in zip(items, checks):   # positional: a verdict belongs to exactly one item
            o = dict(it)
            o["provenance"] = it.get("provenance") or prov
            o["relayed_by"] = d.get("relayed_by") or []
            o["receipt_check"] = check
            o["provider_proof"] = {k: levels[k] for k in ("time", "location", "content")}
            o.setdefault("sensor", {"kind": _sensor_kind(c)})
            o["retrieved_at"] = now
            out.append(o)
        self.results = out
        self.protocol["delivery"] = {k: v for k, v in d.items() if k != "items"} | {"items": len(out)}
        bad = [o["id"] for o in out if o["receipt_check"].get("verified") is False]
        if bad:
            self._warn("receipt", "receipt did not verify for " + ", ".join(map(str, bad)))

    def _refresh_protocol(self):
        from .protocol.client import ProtocolError
        try:
            order = self._pclient(self.protocol["card_id"]).status(self.protocol["order"]["id"])
        except ProtocolError as e:
            self._note(f"no status: {e.code}")
            return self
        return self._take_order(order)

    def wait(self, timeout=None, max_polls=None):
        """Protocol orders: poll the provider until delivered or failed, honouring its Retry-After."""
        if not (self.protocol and self.protocol.get("order")) or self.state not in ("submitted", "in_flight"):
            return self.refresh()
        from .protocol.client import ProtocolError
        try:
            order = self._pclient(self.protocol["card_id"]).wait(self.protocol["order"]["id"], timeout=timeout,
                                                                 max_polls=max_polls, route=self.choice.get("route"))
        except ProtocolError as e:
            self._note(f"no status: {e.code}")
            return self
        return self._take_order(order)

    def cancel(self, idempotency_key=None):
        """Ask a protocol provider to stop a placed order (cooperative: it may stay in_flight, then end failed with
        reason_code cancelled, or delivered if it was too late). A refusal is kept in `error`, not raised."""
        if not (self.protocol and self.protocol.get("order")):
            raise ValueError("only an order placed through the irlpls protocol can be cancelled here")
        from .protocol import client as pc
        try:
            order = self._pclient(self.protocol["card_id"]).cancel(
                self.protocol["order"]["id"],
                idempotency_key=pc.derive_key(_check_key(idempotency_key) if idempotency_key else f"cancel-{self.id}", self.protocol["card_id"]))
        except pc.ProtocolError as e:
            return self._problem(e, "cancel")
        self.protocol["approved_total"] = None  # do not re-trigger the over-total cancel
        self._note(f"cancel sent for {order['id']}")
        return self._take_order(order)

    def approve(self, approval_id=None, confirm=False, idempotency_key=None, total=None):
        """The second, paid step of a two-step order (Capella's cost estimate, GLOBHE's quote): accept one of
        `approvals` (see refresh()). Without confirm it returns the dry run of the call. With confirm=True it sends it:
        only on an explicit per-order call (a standing mandate never reaches here), with an idempotency_key, and with
        `total` repeating the option's amount as shown when the option has one (so a changed price is never accepted).
        A policy's max_order_usd still applies: an option over it, or not in USD while a ceiling is set, is refused."""
        if self.state not in ("submitted", "in_flight") or not self.approvals:
            raise JobError("no_approval", "this job has no price waiting for approval; refresh the placed order first to see one",
                           mcp="call irlpls_get_job with refresh=true, then irlpls_approve if approvals is not empty",
                           cli="irlpls refresh JOB, then irlpls approve JOB", python="job.refresh(), then job.approve(...)")
        opt = next((x for x in self.approvals if x.get("id") == approval_id), None) if approval_id is not None \
            else (self.approvals[0] if len(self.approvals) == 1 else None)
        if opt is None:
            raise JobError("invalid_approval_id", "approval_id must be one of: " + ", ".join(str(x.get("id")) for x in self.approvals),
                           mcp="pass approval_id from the job's approvals", cli="irlpls approve JOB --approval-id ID",
                           python="job.approve(approval_id=...)")
        a = adapters.get(self.choice["card_id"])
        if not confirm:
            return a.approve(self.provider_response, approval_id=opt.get("id"), confirm=False)
        self._operator_check("approving a price")
        _check_key(idempotency_key) if idempotency_key is not None else None
        if not idempotency_key:
            raise _key_needed("approving a price (it spends money)")
        if opt.get("amount") is not None and (_dec(total) is None or _dec(total) != _dec(opt["amount"])):
            raise JobError("total_required", f"repeat the price the person approved: total={opt['amount']} ({opt.get('currency')})",
                           mcp=f"pass total=\"{opt['amount']}\" exactly as listed in approvals", cli=f"add --total {opt['amount']}",
                           python=f"job.approve(..., total='{opt['amount']}')")
        pol = self._eff_policy()
        if pol and pol.max_order_usd is not None:
            if opt.get("currency") != "USD" or opt.get("amount") is None:
                raise PolicyViolation(self.choice["card_id"], [f"policy:max_order_usd: the price is in {opt.get('currency') or 'no stated currency'}, not USD"])
            if _dec(opt["amount"]) > as_decimal(pol.max_order_usd):
                raise PolicyViolation(self.choice["card_id"], [f"policy:max_order_usd: {opt['amount']} over {pol.max_order_usd}"])
        slot = "approve:" + idempotency_key
        fp = _fingerprint(self.choice["card_id"], {"approval_id": opt.get("id"), "amount": opt.get("amount")})
        with _OrderLock(self.id):
            self._load_orders()
            if slot in self.orders:
                if self.fps.get(slot, fp) != fp:
                    raise _reused(idempotency_key)
                self._note(f"replayed key {idempotency_key!r}: approval already sent")
                return self
            resp = a.approve(self.provider_response, approval_id=opt.get("id"), confirm=True)
            self.orders[slot] = resp
            self.fps[slot] = fp
            self._save_orders()
        self.approvals = []
        self.state = "in_flight"
        self._note(f"approved {opt.get('id') or 'the estimate'} at {opt.get('amount')} {opt.get('currency') or ''} with "
                   f"{self.choice['card_id']} (explicit approval)")
        _save(self)
        return self

    @classmethod
    def from_dict(cls, d):
        """A job back from to_dict() (its saved file). The plan, choice and order state are restored as saved; nothing is
        re-planned or re-sent."""
        from decimal import Decimal, InvalidOperation
        from .need import Request
        rd = dict(d.get("request") or {"need": d.get("need") or ""})
        if isinstance(rd.get("country_code"), str):
            rd["country_code"] = rd["country_code"].lower()
        pd = dict(d.get("policy") or {})
        for k in ("max_order_usd", "max_unit_price_usd"):
            if isinstance(pd.get(k), str):
                try:
                    pd[k] = Decimal(pd[k])
                except InvalidOperation:
                    pass
        pol = Policy.from_dict(pd) if d.get("policy") else None
        j = cls(d.get("need") or rd.get("need") or "", Request.from_dict(rd), pol, d.get("why"))
        j.id = d["id"]
        j.plan = [{k: v for k, v in o.items() if k != "default"} for o in d.get("plan") or []]
        ch = d.get("choice")
        j.choice = next((o for o in j.plan if ch and o["card_id"] == ch.get("card_id")), ch)
        for k, attr in (("state", "state"), ("questions", "questions"), ("quote", "quote"), ("provider_response", "provider_response"),
                        ("status", "status_response"), ("provider_status", "provider_status"), ("approvals", "approvals"),
                        ("results", "results"), ("ruled_out", "ruled_out"), ("warnings", "warnings"), ("log", "log"),
                        ("error", "error"), ("reason_code", "reason_code"), ("protocol", "protocol"), ("refusal", "refusal")):
            if k in d:
                setattr(j, attr, d[k] if d[k] is not None or attr not in ("questions", "approvals", "results", "ruled_out", "warnings", "log") else [])
        return j

    def default_option(self):
        """The option submit() would run now: the one chosen, else best(). None when nothing complies."""
        try:
            return self.choice or self.best()
        except Exception:
            return None

    def _with_pay(self, o):
        """A commission option with its order pack (irlpls.orderpack): how the person finishes it. Plans and drafts only."""
        if o.get("route") != "commission" or self.request is None:
            return o
        try:
            from . import card as get_card
            from .orderpack import how_to_pay
            c = get_card(o["card_id"])
            hp = how_to_pay(self, c) if c else None
        except Exception:
            hp = None
        return dict(o, how_to_pay=hp) if hp else o

    def to_dict(self):
        d_opt = self.default_option() if self.state not in ("failed",) or self.choice else None
        best_id = d_opt["card_id"] if d_opt else None
        dr = self.default_reason(d_opt) if d_opt else None
        plan = [dict(o, default=True, **({"default_reason": dr} if dr else {})) if best_id and o["card_id"] == best_id else o for o in self.plan]
        plan = [self._with_pay(o) for o in plan]
        req = self.request.to_dict() if self.request is not None else None
        if req and isinstance(req.get("country_code"), str):   # ISO 3166 upper case on the wire (schema/request.schema.json)
            req["country_code"] = req["country_code"].upper()
        return {"id": self.id, "need": self.need, "why": self.why, "state": self.state, "request": req,
                "best_card_id": best_id,
                "policy": self.policy.to_dict() if self.policy else None, "questions": self.questions, "plan": plan,
                "ruled_out": self.ruled_out, "choice": self.choice, "quote": self.quote, "provider_response": self.provider_response,
                "status": self.status_response, "provider_status": self.provider_status, "approvals": self.approvals,
                "reason_code": self.reason_code, "refusal": getattr(self, "refusal", None),
                "error": self.error, "protocol": self.protocol, "results": self.results, "warnings": self.warnings, "log": self.log}


def _expired(quote):
    """A quote past its expires_at (ISO 8601, UTC) is not reused."""
    from datetime import datetime, timezone
    exp = (quote or {}).get("expires_at")
    if not exp:
        return False
    try:
        t = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return True  # unreadable: re-quote rather than trust it
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) <= datetime.now(timezone.utc)


def _warning(w, card_id):
    """A warning in the one shape {code, card_id, msg} (adapters name the provider `provider`)."""
    w = dict(w) if isinstance(w, dict) else {"code": "warning", "msg": str(w)}
    w.setdefault("card_id", w.get("provider") or card_id)
    w.setdefault("code", "warning")
    w.setdefault("msg", "")
    return w


def _dedupe(rows):
    return adapters.base.dedupe(rows)


def _sensor_kind(card):
    """SOSA counts people as sensors too: device for cameras, gauges and satellites; human for people and crowds. A
    network of devices that volunteers own (Sensor.Community) is still devices: its rows were never made by a person."""
    by = (card.get("capture") or {}).get("by")
    mods = set((card.get("capabilities") or {}).get("modalities") or [])
    if mods and mods <= {"sensor"}:
        return "device"
    return "human" if by in ("people", "contributors", "subject") else "device"


def _bounded(d):
    """A job's dict as JSON text under STATE_MAX_BYTES: the log, then ruled_out, then plan are trimmed before a write."""
    txt = json.dumps(d, indent=1, default=str)
    for k, keep in (("log", 40), ("ruled_out", 20), ("plan", 10), ("log", 5), ("ruled_out", 0)):
        if len(txt) <= STATE_MAX_BYTES:
            break
        if isinstance(d.get(k), list):
            d = dict(d, **{k: d[k][-keep:] if k == "log" else d[k][:keep]})
            txt = json.dumps(d, indent=1, default=str)
    return txt


def _save(job):
    _JOBS[job.id] = job
    try:
        _private_dir(HOME)
        _private_dir(HOME / "jobs")
        _atomic_write(HOME / "jobs" / f"{job.id}.json", _bounded(job.to_dict()))
        _prune_jobs(keep=job.id)
    except OSError:
        pass


def get_job(job_id):
    return _JOBS.get(job_id)


_JOB_ID = __import__("re").compile(r"[0-9a-f]{12}")


def load_job(job_id):
    """A job from this session, else from its saved file (~/.irlpls/jobs/<id>.json, written by every step), so the CLI
    can answer, choose, submit, approve and refresh a job across commands. None when there is no such job."""
    j = _JOBS.get(job_id)
    if j is not None or not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        return j
    try:
        d = json.loads((HOME / "jobs" / f"{job_id}.json").read_text())
    except (OSError, ValueError):
        return None
    j = Job.from_dict(d)
    _JOBS[j.id] = j
    return j


def request(need, why=None, policy=None, geocode=True, **hints):
    """Start a job from a natural-language need. Hints the requester already knows (place, lat, lon, within_minutes,
    quantity, mode) override what the parser infers."""
    from . import make_request
    from .need import check_hints, check_request
    if isinstance(need, dict):  # a Request document (schema/request.schema.json): why and policy may ride inside it
        why = why or need.get("why")
        policy = policy if policy is not None else need.get("policy")
        if not isinstance(need.get("need"), str):
            raise ValueError("need is empty: say what to observe or capture, with the place and the time")
    if not isinstance(need, (str, dict)) or (isinstance(need, str) and not need.strip()):
        raise ValueError("need is empty: say what to observe or capture, with the place and the time "
                         "('river level at Comfort, TX now')")
    check_hints(hints)   # a misspelt hint ("quantiy") is an error, never dropped
    pol = policy if isinstance(policy, Policy) or policy is None else Policy.from_dict(policy)
    stated = budget_in(need.get("need") if isinstance(need, dict) else need)   # a ceiling stated in the ask itself
    if stated is not None and (pol is None or pol.max_order_usd is None):   # a policy's own ceiling always wins
        pol = __import__("dataclasses").replace(pol) if pol is not None else Policy()   # a copy: the caller's object is untouched
        pol.max_order_usd = stated
    if pol is not None:
        pol.check_values()   # a negative or NaN ceiling is the person's mistake: say so, don't plan around it
    req = make_request(need, geocode=False, **hints)
    if geocode and req.place and req.lat is None and not ambiguous_place(req):
        g, _slow = _geocode_timeboxed(req.place)   # time-boxed: a slow geocoder leaves the place un-located (asked, not hidden)
        if g:
            req.lat, req.lon, req.country_code, req.state = g["lat"], g["lon"], g["country_code"], g.get("state")
            req.subdivision = g.get("subdivision") or req.subdivision
            req.place_approximate = bool(g.get("approximate"))
    check_request(req)
    if isinstance(need, dict):  # a hostile intent can sit in any text field of the document (why, notes, ...)
        from .need import detect_targets_person, text_fields
        if detect_targets_person(req.need, why, *text_fields({k: v for k, v in need.items() if k not in ("policy",)})):
            req.targets_person = True
    job = Job(req.need, req, pol, why)
    job._plan()
    _save(job)
    return job
