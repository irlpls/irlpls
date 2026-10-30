"""irlpls: the open standard and broker for getting NEW real-world data from providers. Inference-free.

Capture Cards credential providers: what each can prove, what it only claims, and who has independently checked it.
You (or your agent's own model) state a need in natural language; the broker plans against the cards, enforces your
policy, and executes through provider adapters. Live feed or commissioned capture is the broker's routing decision.

    job = irlpls.request("is the Guadalupe at Comfort, TX good for tubing right now",
                         why="user deciding whether to go today")
    job.plan; job.submit(); job.results

Lower level: find() ranks cards; live() and commission() call one provider's adapter directly.
"""
from . import adapters, geo, ratelimit
from .match import rank, score
from .policy import Policy, PolicyViolation
from .broker import Job, get_job, request
from .registry import ASSURANCES, Registry
from .need import Request, parse

__version__ = "0.1.0"
_registry = None


def registry():
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry


def make_request(need, geocode=True, **overrides):
    """Turn a sentence (or a dict from your own LLM) into a Request, resolving the place if possible."""
    if isinstance(need, dict):  # a Request document: parse its need, then its other fields override as hints
        req = parse(need["need"], registry().cards)
        for k, v in need.items():
            if k in Request.__dataclass_fields__ and k != "need" and v is not None:
                setattr(req, k, v)
    else:
        req = parse(need, registry().cards)
    for k, v in overrides.items():
        setattr(req, k, v)
    if isinstance(req.country_code, str):  # a Request document uses ISO "US"; the parser and geo use "us"
        req.country_code = req.country_code.strip().lower() or None
    if geocode and req.place and req.lat is None:
        try:
            g = geo.geocode(req.place)
        except Exception:
            g = None
        if g:
            req.lat, req.lon, req.country_code, req.state = g["lat"], g["lon"], g["country_code"], g.get("state")
            req.subdivision = g.get("subdivision") or req.subdivision
    return req


def _policy(p):
    return p if isinstance(p, Policy) or p is None else Policy.from_dict(p)


def find(need, limit=10, geocode=True, policy=None, strict=False, **overrides):
    """Rank providers that can deliver NEW data for a need. Each result: id, name, fit (0-100, this request only),
    parts, gaps, what this SDK can call (live / commission), and policy violations if a policy is given.
    strict=True drops providers that violate the policy."""
    req = make_request(need, geocode=geocode, **overrides)
    pol = _policy(policy)
    out = []
    for m in rank(registry().cards, req):
        a = adapters.get(m["id"])
        m["adapter"] = None if not a else {"live": a.can_live, "commission": a.can_commission, "env": a.env, "ready": a.configured()}
        if pol:
            m["policy"] = pol.check(card(m["id"]))
            if strict and m["policy"]:
                continue
        out.append(m)
    return out[:limit]


def card(card_id):
    return registry().get(card_id)


def _where(place, lat, lon):
    if lat is not None and lon is not None:
        return lat, lon
    if not place:
        from .broker import JobError
        raise JobError("needs_place", "give a place, or lat and lon", mcp="pass place, or lat and lon",
                       cli="--at 'Austin, TX', or --lat and --lon", python="place='Austin, TX', or lat= and lon=")
    g = geo.geocode(place)
    if not g:
        raise ValueError(f"could not find {place!r}")
    return g["lat"], g["lon"]


def live(card_id, place=None, lat=None, lon=None, **kw):
    """Pull what a provider is reporting now near a place (sensor feeds)."""
    a = adapters.get(card_id)
    if not a:
        raise adapters.NotSupported(f"no adapter for {card_id!r}; see the card's api block to call it directly")
    lat, lon = _where(place, lat, lon)
    with ratelimit.using(card_id):  # paced, retried and cached under the provider's own limit (irlpls/ratelimit.py)
        res = a.live(lat, lon, **kw)
    # one format for everything handed out: UTC times with Z, one unit vocabulary (adapters.base.normalise)
    out = adapters.base.Results([adapters.base.normalise(dict(o)) if isinstance(o, dict) else o for o in res],
                                getattr(res, "warnings", []))
    for k in ("pending",):
        if hasattr(res, k):
            setattr(out, k, getattr(res, k))
    return out


def live_many(card_id, asks, budget_s=60, **kw):
    """Many asks to one provider ({lat, lon} or (lat, lon) each), batched where the provider allows it and queued under
    its rate limit. One entry per ask (None if still queued) plus .warnings; what didn't fit in budget_s is listed in a
    'rate_limited' warning (and .pending), never dropped. See irlpls/ratelimit.py."""
    return ratelimit.live_many(card_id, asks, budget_s=budget_s, **kw)


def commission(card_id, spec, confirm=False, policy=None, idempotency_key=None):
    """Ask a provider to capture what you specify. Dry run (with a real quote where offered) unless confirm=True.
    With a policy, a violating order is never sent: the dry run reports the violations, and confirm=True raises.
    confirm=True needs an idempotency_key: a retry with the same key returns the first response and never orders twice
    (kept in ~/.irlpls/orders/), and the key is passed to the provider where it takes one."""
    a = adapters.get(card_id)
    if not a:
        raise adapters.NotSupported(f"no adapter for {card_id!r}")
    if confirm:
        from .broker import _check_key, _key_needed
        pol = _policy(policy)
        bad = pol.check(card(card_id)) if pol else []
        if bad:   # a violating order is refused before anything else is asked for
            raise PolicyViolation(card_id, bad)
        explicit = idempotency_key is not None   # the broker keeps its own replay record and puts its key in the spec
        idempotency_key = idempotency_key or (spec or {}).get("idempotency_key")
        if not idempotency_key:
            raise _key_needed()
        _check_key(idempotency_key)
        prior = _order_replay(card_id, idempotency_key) if explicit else None
        if prior is not None:
            return prior
        spec = dict(spec or {}, idempotency_key=idempotency_key)
        spec.setdefault("external_id", idempotency_key)
        out = _commission(a, card_id, spec, True, policy)
        if explicit and isinstance(out, dict) and not out.get("dry_run"):
            _order_replay(card_id, idempotency_key, out)
        return out
    return _commission(a, card_id, spec, False, policy)


def _order_replay(card_id, key, out=None):
    """Read (out None) or record the response an idempotency key got from a provider."""
    import hashlib
    import json
    from .broker import HOME
    f = HOME / "orders" / (hashlib.sha256(f"{card_id}|{key}".encode()).hexdigest()[:32] + ".json")
    if out is None:
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return None
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out, default=str))
    except OSError:
        pass
    return out


def _commission(a, card_id, spec, confirm, policy):
    pol = _policy(policy)
    violations = pol.check(card(card_id)) if pol else []
    if violations:
        if confirm:
            raise PolicyViolation(card_id, violations)
        return {"dry_run": True, "blocked_by_policy": violations, "note": "Not sent. This order violates your policy."}
    if confirm:   # never place an order missing details the person must give (an address, a price, what to capture)
        with ratelimit.using(card_id):
            dry = a.commission(spec, confirm=False)
        if isinstance(dry, dict) and dry.get("missing"):
            raise adapters.base.MissingDetails(card_id, dry["missing"])
    with ratelimit.using(card_id):
        out = a.commission(spec, confirm=False if pol and pol.max_order_usd is not None else confirm)
    if pol and pol.max_order_usd is not None:
        total = _quoted_total(out)
        if total is None:
            violations = ["no quoted total in USD to check against max_order_usd"]
        elif total > pol.max_order_usd:
            violations = [f"quote ${total:g} over max_order_usd ${pol.max_order_usd:g}"]
        if violations:
            if confirm:
                raise PolicyViolation(card_id, violations)
            out["blocked_by_policy"] = violations
            return out
        if confirm:
            with ratelimit.using(card_id):
                out = a.commission(spec, confirm=True)
    return out


def _quoted_total(dry):
    """The dry run's firm USD total (adapters.base.usd_total), or None. A price in another currency is never a USD
    total, so it fails a max_order_usd check."""
    t = adapters.base.usd_total(dry)
    return None if t is None else float(t)


def configured():
    """Card ids whose adapters you can call now (free, or your key is set)."""
    return adapters.configured()
