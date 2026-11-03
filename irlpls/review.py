"""Which stated levels the site shows as Evidenced (the owner, 2026-11-02: "Evidenced comes at a later time, when we can
partner with independent reviewing agencies").

A link shows as Evidenced only when an independent reviewer irlpls recognises has attested it: the card's `attestations`
holds an entry from a reviewer on irlpls's list (irlpls/data/reviewers.json, empty today) whose scope names that guarantee
or provenance entry. A provider's own "evidenced" (even with a proof pack the reference checker verified) shows as Claimed,
with the quiet note "evidence awaits independent review". The checker (irlpls.evidence, the check page) stays a tool; its
verdicts never change a displayed level.

Mirrored by IRLMatch.reviewed (src/match.js). build.py applies `displayed` to every card the site publishes (cards.json,
cards/<id>.json, card pages, catalog.json), so every page and the browser's matcher read the same levels.
"""
import copy
import json
import re
from pathlib import Path

NOTE = "evidence awaits independent review"
REVIEWERS_FILE = Path(__file__).resolve().parent / "data" / "reviewers.json"


def reviewers(path=REVIEWERS_FILE):
    """The independent reviewers irlpls recognises: [{"id", "name", "url"}]. Empty today."""
    try:
        return list(json.loads(Path(path).read_text()).get("reviewers") or [])
    except (OSError, ValueError, AttributeError):
        return []


def _key(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _recognised(att, revs):
    who = _key(att.get("verifier"))
    return bool(who) and any(who in (_key(r.get("id")), _key(r.get("name"))) for r in revs)


def _scopes(att):
    """The links an attestation covers: 'location', 'verification.location', 'time, location', 'provenance.custody'."""
    return {_key(x.split(".")[-1]) for x in re.split(r"[,;/]|\band\b", str(att.get("scope") or "")) if x.strip()}


def attested(card, key, revs=None):
    """True if a recognised reviewer's attestation on this card covers `key` (a guarantee or provenance entry)."""
    revs = reviewers() if revs is None else revs
    if not revs:
        return False
    return any(isinstance(a, dict) and _recognised(a, revs) and _key(key) in _scopes(a) for a in card.get("attestations") or [])


def displayed(card, revs=None):
    """A copy of the card with each self-declared 'evidenced' shown as 'claimed' (stated_level and review kept beside it),
    unless a recognised reviewer attested it."""
    revs = reviewers() if revs is None else revs
    c = copy.deepcopy(card)
    for group in ("verification", "provenance"):
        for k, g in (c.get(group) or {}).items():
            if isinstance(g, dict) and g.get("level") == "evidenced" and not attested(c, k, revs):
                g["stated_level"], g["level"], g["review"] = "evidenced", "claimed", "awaiting"
    return c
