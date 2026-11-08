"""Candidate selection for match.rank: score only cards that can have fit > 0, with identical results.

A card scores above 0 only if its description match reaches MIN_DESCRIPTION, which needs at least one of:
  - a requested term the card mentions (its own word, a stem, or a concept the word maps to): inverted index;
  - a custom-capture card is also credited for unmentioned specifics (counted in full here);
  - a requested modality the card lists (counted in full here).
Each card's description numerator is bounded from above by those pieces; cards whose bound is under
MIN_DESCRIPTION * denominator cannot score and are skipped.
An ask with neither terms nor modalities has no description gate, so every card stays a candidate (brute force).
The coverage index (area -> card ids; Global/Worldwide and unrestricted cards always included) prunes the one hard
coverage rule-out, a live ask in Antarctica. Everything is a SUPERSET filter: when in doubt a card is kept.
Candidates come back in the original list order, so the stable sort in rank() ties exactly as before.
"""
from . import match as _m


class CardIndex:
    def __init__(self, cards):
        self.cards = cards
        self.n = len(cards)
        self.by_id = {c["id"]: c for c in cards}
        self.tokens, self.stems, self.mods = {}, {}, {}
        self.custom, self.antarctic = set(), set()
        self.areas, self.always = {}, set()   # area code -> positions; cards that cover anywhere
        for i, c in enumerate(cards):
            try:
                toks, stems = _m.vocab(c)
                for t in toks:
                    self.tokens.setdefault(t, set()).add(i)
                for s in stems:
                    self.stems.setdefault(s, set()).add(i)
                for mo in c["capabilities"]["modalities"]:
                    self.mods.setdefault(mo, set()).add(i)
                if _m.custom_capture(c):
                    self.custom.add(i)
                if _m.antarctic(c):
                    self.antarctic.add(i)
                areas = [a.lower() for a in c["coverage"].get("areas") or []]
                if not areas or "*" in areas or _m._global(c):
                    self.always.add(i)
                for a in areas:
                    self.areas.setdefault(a, set()).add(i)
            except (KeyError, TypeError, AttributeError):
                self.always.add(i)   # malformed card: never prune it
                self.custom.add(i)
                self.bad = getattr(self, "bad", set()) | {i}

    def _word(self, w):
        con = _m.CONCEPTS.get(w, _m.CONCEPTS.get(_m._stem(w), "")).split()
        keys = list(con) if (w in _m.CONCEPT_ONLY or _m._stem(w) in _m.CONCEPT_ONLY) else [w] + con
        out = set()
        for x in keys:
            out |= self.tokens.get(x, set())
            out |= self.stems.get(_m._stem(x), set())
        return out

    def positions(self, req):
        """Positions (in card-list order) of every card that could score above 0 for this request."""
        if not (req.terms or req.modalities):
            return None   # no description gate: brute force
        bad = set(getattr(self, "bad", ()))
        spec = set(getattr(req, "specifics", None) or [])
        den = sum(_m.SPECIFIC if w in spec else 1.0 for w in req.terms) + len(req.modalities)
        # Upper bound on each card's description numerator; keep it only if that bound could reach MIN_DESCRIPTION.
        ub = {}
        for w in req.terms:
            wt = _m.SPECIFIC if w in spec else 1.0
            for i in self._word(w):
                ub[i] = ub.get(i, 0.0) + wt
        for mo in req.modalities:
            for i in self.mods.get(mo, ()):
                ub[i] = ub.get(i, 0.0) + 1.0
        if req.terms:
            extra = sum(_m.SPECIFIC for w in req.terms if w in spec and w not in _m.SENSORS)   # custom-task credit, taken in full
            for i in self.custom:
                ub[i] = ub.get(i, 0.0) + extra
        need = _m.MIN_DESCRIPTION * den - 1e-9
        keep = {i for i, v in ub.items() if v >= need} | bad
        if req.mode == "live" and (req.country_code or "").lower() == "aq":
            keep &= self.antarctic | set(getattr(self, "bad", ()))
        return sorted(keep)


def build(cards):
    return CardIndex(cards)


def candidates(req, index):
    """The cards rank() must score for this request (a superset of those with fit > 0), in list order."""
    pos = index.positions(req)
    return list(index.cards) if pos is None else [index.cards[i] for i in pos]


_CACHE = {}
AUTO_MIN = 500   # below this many cards brute force is as fast as building an index


def cached(cards):
    """An index for this very list (identity, length and members), or None for a short list."""
    if len(cards) < AUTO_MIN:
        return None
    key = (id(cards), len(cards), hash(tuple(map(id, cards))))
    hit = _CACHE.get("last")
    if hit and hit[0] == key and hit[1] is cards:
        return hit[2]
    idx = build(cards)
    _CACHE["last"] = (key, cards, idx)
    return idx
