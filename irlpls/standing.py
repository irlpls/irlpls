"""Where a card stands, for its provider: its rank on the typical asks of its own families, the one change in evidence
that would move it up, and what an agent would tell its person about it. Everything is computed from the matcher and
the broker's real plan; nothing is written by hand (docs/research/strategy/PROVIDER-EXPERIENCE.md).

    from irlpls import standing
    s = standing.standing(card_id, cards)     # {"ranks": [...], "move_up": {...} or None, "says": {...} or None}

- Ranks: tools/bench_asks.json gives 1-4 asks per catalog family (irlpls/catalog.py FAMILIES). A card is ranked only
  on its own families' asks (no ranking on asks it never said it serves). Rank = position among the broker's plan
  options for the ask (irlpls.broker.Job._plan, offline, these cards as the registry; policy-compliant options only:
  the list best() picks from), so "#3 of 18" and the sentence's "option 3 of 18" are one number. A card not in the
  plan reads "Not covered" when its coverage excludes the place, else "Not a match".
- Move up: for each ask where the card is not first, each guarantee it does not evidence is set to `evidenced` (alone,
  then the required ones in pairs, then all of them) and the card is re-scored and re-ranked against the same cards,
  through the same plan filter (irlpls.broker.plannable, which evidence never changes).
  The biggest gain wins, with the fewest guarantees on a tie. None when no change of evidence changes a rank.
- Says: the sentence is filled from the same plan as the rank, for the card's best-ranked ask: whether it is the default pick, its route, speed and
  price, its readiness line, and the levels of time, place and content.
"""
import contextlib
import functools
import itertools
import json
import pathlib

from .catalog import FAMILIES
from .match import in_scope, rank, score

BENCH = pathlib.Path(__file__).resolve().parent.parent / "tools" / "bench_asks.json"
GUARANTEES = ("human", "application", "camera", "time", "location", "content", "corroboration")
WORD = {"human": "person", "application": "app", "camera": "camera", "time": "time", "location": "place",
        "content": "content", "corroboration": "cross-check"}
MAX_ROWS = 4
PROVIDERS_HOW = "providers.html#7-make-a-link-evidenced-optional"


@functools.lru_cache(maxsize=1)
def bench():
    return json.loads(BENCH.read_text())["families"]


def families_of(card_id):
    return [fid for fid, _t, _title, _ex, provs in FAMILIES if card_id in provs]


def asks_for(card_id):
    """[{family, ask, label}] for the card's own families, at most MAX_ROWS, no ask twice."""
    out, seen = [], set()
    for fid in families_of(card_id):
        for a in bench().get(fid, []):
            if a["ask"] not in seen:
                seen.add(a["ask"])
                out.append({"family": fid, "ask": a["ask"], "label": a["label"]})
    return out[:MAX_ROWS]


def listed(cards):
    return [c for c in cards if in_scope(c) and c.get("status") != "sample"]


def _position(ranked, cid):
    ids = [r["id"] if "id" in r else r["card_id"] for r in ranked]
    return (ids.index(cid) + 1) if cid in ids else None


class _Reg:
    def __init__(self, cards):
        self.cards, self.all, self._by_id = cards, cards, {c["id"]: c for c in cards}

    def get(self, cid):
        return self._by_id.get(cid)

    def __iter__(self):
        return iter(self.cards)

    def __len__(self):
        return len(self.cards)


@contextlib.contextmanager
def _as_registry(cards):
    import irlpls
    old = irlpls._registry
    irlpls._registry = _Reg(cards)
    try:
        yield
    finally:
        irlpls._registry = old


def plan(ask, cards):
    """The broker's plan for one ask, offline (no geocoding, no network), with these (listed) cards as the registry:
    (job, options). options = the plan's policy-compliant options in plan order, the list Job.best() picks from. Both
    the rank and the agent sentence read their numbers from it."""
    from . import make_request
    from .broker import Job
    with _as_registry(cards):
        req = make_request(ask, geocode=False)
        job = Job(req.need, req, None)
        job._plan(cards)
        return job, [o for o in job.plan if not o["policy"]]


def ranks(card_id, cards, _plans=None):
    """[{family, ask, label, pos, of, fit, covered}] for the card's own asks: its place among the plan's options
    (pos None when it is not in the plan). _plans: {ask: plan(ask, cards)} already made."""
    cards = listed(cards)
    c = next((x for x in cards if x["id"] == card_id), None)
    out = []
    for a in asks_for(card_id):
        job, opts = (_plans or {}).get(a["ask"]) or plan(a["ask"], cards)
        pos = _position(opts, card_id)
        sc = score(c, job.request) if c else None
        out.append({**a, "pos": pos, "of": len(opts), "fit": opts[pos - 1]["fit"] if pos else 0,
                    "covered": not (sc and sc["parts"].get("outside_coverage"))})
    return out


def _evidenced(card, keys):
    c = json.loads(json.dumps(card))
    for k in keys:
        c["verification"][k] = {**c["verification"].get(k, {}), "level": "evidenced"}
    return c


def move_up(card_id, cards, rows=None, _plans=None):
    """The one change in evidence that moves the card up the most on one of its asks, computed by re-ranking through
    the plan's own filter (what the plan would be with that evidence): {keys, words, label, ask, before, after, of, how}
    or None."""
    from .broker import plannable
    cards = listed(cards)
    me = next((c for c in cards if c["id"] == card_id), None)
    if not me:
        return None
    rows = rows if rows is not None else ranks(card_id, cards, _plans)
    open_ = [k for k in GUARANTEES if (me["verification"].get(k) or {}).get("level") != "evidenced"]
    best = None
    for r in rows:
        if not r["pos"] or r["pos"] == 1:
            continue
        job, _opts = (_plans or {}).get(r["ask"]) or plan(r["ask"], cards)
        if not job.plan:
            continue
        req = job.request
        ok = {c["id"] for c in cards if plannable(c, req, job.policy)}
        req_keys = [k for k in (req.require or []) if k in open_]
        sets = [(k,) for k in open_] + list(itertools.combinations(req_keys, 2)) + ([tuple(req_keys)] if len(req_keys) > 2 else [])
        for ks in sets:
            alt = [(_evidenced(c, ks) if c["id"] == card_id else c) for c in cards]
            pos = _position([m for m in rank(alt, req) if m["id"] in ok], card_id)
            if not pos or pos >= r["pos"]:
                continue
            cand = (r["pos"] - pos, -len(ks))
            if best is None or cand > best[0]:
                best = (cand, ks, r, pos)
    if not best:
        return None
    _, ks, r, pos = best
    ws = [WORD[k] for k in ks]
    words = ws[0] if len(ws) == 1 else ", ".join(ws[:-1]) + " and " + ws[-1]
    return {"keys": list(ks), "words": words, "label": r["label"], "ask": r["ask"], "before": r["pos"], "after": pos,
            "of": r["of"], "how": PROVIDERS_HOW}


def _price_words(p, unit):
    if p == 0:
        return "free"
    if p is None:
        return "price not published"
    u = str(unit or "").split("(")[0].split(";")[0].strip()
    u = u[4:] if u.startswith("per ") else u
    return f"from ${p:g}" + (f" per {u}" if u else "")


def _proof_words(proof):
    """'Time, place and content are claimed, not evidenced.' from the plan's proof levels."""
    by = {}
    for k in ("time", "location", "content"):
        by.setdefault(proof.get(k) or "none", []).append(WORD[k])
    join = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]  # noqa: E731
    bits = []
    for lv, say in (("evidenced", "evidenced"), ("claimed", "claimed, not evidenced"), ("none", "not stated")):
        if by.get(lv):
            xs = by[lv]
            bits.append(f"{join(xs)} {'is' if len(xs) == 1 else 'are'} {say}")
    s = "; ".join(bits)
    return s[:1].upper() + s[1:] + "."


def says(card_id, cards, rows=None, _plans=None):
    """What an agent tells its person about this card for its best-ranked ask, filled from the same plan as its rank.
    {ask, label, pick, pos, of, line, ready, proof} or None when the card is not in the plan for any of its asks."""
    cards = listed(cards)
    rows = rows if rows is not None else ranks(card_id, cards, _plans)
    ranked = sorted((r for r in rows if r["pos"]), key=lambda r: (r["pos"], -r["fit"]))
    for r in ranked:
        job, opts = (_plans or {}).get(r["ask"]) or plan(r["ask"], cards)
        pos = _position(opts, card_id)
        if not pos:
            continue
        opt = opts[pos - 1]
        best = job.best()
        pick = best is not None and best["card_id"] == card_id
        route = "made to order" if opt["route"] == "commission" else "live"
        speed = opt.get("speed")
        speed = f"usually within {speed}" if speed in ("minutes", "hours", "days", "weeks") else "speed not stated"
        who = opt["name"].split(" (")[0]
        lead = f"{who} is the default pick" if pick else f"{who} is option {pos} of {len(opts)}"
        line = f"For “{r['label']}”, {lead}: {route}, {speed}, {_price_words(opt.get('price'), opt.get('price_unit'))}."
        return {"ask": r["ask"], "label": r["label"], "pick": pick, "pos": pos, "of": len(opts), "line": line,
                "ready": (opt.get("readiness") or {}).get("line"), "proof": _proof_words(opt.get("proof") or {})}
    return None


def standing(card_id, cards):
    """{ranks, move_up, says} for one card against these cards (the site's cards, with published copies swapped in).
    One plan per ask feeds all three."""
    cards = listed(cards)
    plans = {a["ask"]: plan(a["ask"], cards) for a in asks_for(card_id)}
    rows = ranks(card_id, cards, plans)
    return {"ranks": rows, "move_up": move_up(card_id, cards, rows, plans), "says": says(card_id, cards, rows, plans)}
