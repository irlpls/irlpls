"""Evidenced only via an independent reviewer irlpls recognises (the owner, 2026-11-02: "Evidenced comes at a later time,
when we can partner with independent reviewing agencies"). irlpls/review.py and IRLMatch.reviewed (src/match.js) apply the
same rule: a self-declared 'evidenced' shows as Claimed with "evidence awaits independent review"; an attestation from a
reviewer on irlpls's list (irlpls/data/reviewers.json, empty today) whose scope names the link keeps it Evidenced.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from irlpls import review

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
REV = [{"id": "acme-review", "name": "Acme Review Lab", "url": "https://acme-review.example"}]


def card(att=None):
    g = {"level": "evidenced", "method": "signed by the device", "evidence_url": "https://p.example/proof", "unknown": "x"}
    return {"id": "t", "verification": {"location": dict(g), "time": dict(g), "human": {"level": "claimed", "unknown": "x"}},
            "provenance": {"receipt": dict(g)}, "attestations": att or []}


def test_self_declared_evidenced_shows_claimed():
    d = review.displayed(card(), REV)
    for k in ("location", "time"):
        g = d["verification"][k]
        assert (g["level"], g["stated_level"], g["review"]) == ("claimed", "evidenced", "awaiting")
    assert d["provenance"]["receipt"]["level"] == "claimed" and d["verification"]["human"]["level"] == "claimed"
    assert card()["verification"]["location"]["level"] == "evidenced"  # the card itself is untouched


def test_a_recognised_reviewer_keeps_its_link_evidenced():
    att = [{"verifier": "Acme Review Lab", "scope": "verification.location, provenance.receipt", "url": "https://acme-review.example/r/1"}]
    d = review.displayed(card(att), REV)
    assert d["verification"]["location"]["level"] == "evidenced" and "review" not in d["verification"]["location"]
    assert d["provenance"]["receipt"]["level"] == "evidenced"
    assert d["verification"]["time"]["level"] == "claimed"  # not in its scope
    # an attestation from someone irlpls doesn't recognise changes nothing; nor does any attestation while the list is empty
    other = [{"verifier": "Self Audit Inc", "scope": "location", "url": "https://x.example"}]
    assert review.displayed(card(other), REV)["verification"]["location"]["level"] == "claimed"
    assert review.displayed(card(att), [])["verification"]["location"]["level"] == "claimed"


def test_irlpls_recognises_no_reviewer_yet():
    assert review.reviewers() == []


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,I=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify({list:M.REVIEWERS,out:I.cases.map(([c,r])=>M.reviewed(c,r)),levels:M.stageLevels(I.cases[0][0])}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_applies_the_same_rule():
    att = [{"verifier": "acme-review", "scope": "location", "url": "https://acme-review.example/r/1"}]
    cases = [[card(), REV], [card(att), REV], [card(att), []]]
    out = json.loads(subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps({"cases": cases}), capture_output=True, text=True, check=True).stdout)
    assert out["list"] == review.reviewers()  # the browser's list is irlpls's list
    assert out["out"] == [review.displayed(c, r) for c, r in cases]
    assert "evidenced" not in out["levels"]  # the eight links of a self-declared card: never Evidenced


@pytest.mark.skipif(not (SITE / "cards.json").exists(), reason="site/ not built")
def test_the_site_shows_no_self_declared_evidenced():
    cards = json.loads((SITE / "cards.json").read_text())["cards"]
    lv = [g.get("level") for c in cards for grp in ("verification", "provenance") for g in (c.get(grp) or {}).values() if isinstance(g, dict)]
    assert "evidenced" not in lv
    cat = json.loads((SITE / "catalog.json").read_text())["providers"]
    assert all(not (p.get("proof") or {}).get("evidenced") for p in cat.values())
    raw = json.loads((ROOT / "cards" / "nws.json").read_text())
    if raw["verification"]["content"]["level"] == "evidenced":  # NWS states content evidenced: the site shows it Claimed, noted
        h = (SITE / "c" / "nws.html").read_text()
        assert "evidence awaits independent review" in h and json.loads((SITE / "cards" / "nws.json").read_text())["verification"]["content"]["level"] == "claimed"
    assert json.loads((SITE / "reviewers.json").read_text())["reviewers"] == []
