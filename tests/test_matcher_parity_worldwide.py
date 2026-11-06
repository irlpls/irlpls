"""A card whose regions say Worldwide/Global covers everywhere, even with a partial coverage.areas list: Python
(irlpls/match.py) and the browser matcher (src/match.js) must both keep it for asks outside the listed countries."""
import json
import pathlib
import shutil
import subprocess

import pytest

from irlpls.match import rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
ASKS = ["какая погода сейчас в Москве", "earthquake near Los Angeles in the last hour", "weather in Moscow right now"]
PARTIAL = ["ZA", "KE", "NG", "IN", "BR"]


def _patched(regions, ask):
    """In-memory copy of the cards: the card ranked first for the ask gets the regions and a partial areas list."""
    cards = json.loads(json.dumps(CARDS))
    top = rank(cards, parse(ask, cards))[0]["id"]
    card = next(c for c in cards if c["id"] == top)
    card["coverage"]["regions"] = regions
    card["coverage"]["areas"] = list(PARTIAL)
    return cards, top


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch;
const {cards,asks}=JSON.parse(fs.readFileSync(0,'utf8'));
const C=cards.filter(c=>M.inScope(c)&&c.status!=='sample');
console.log(JSON.stringify(asks.map(q=>{const r=M.parse(q,C);
  return C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>x.s.total==null||x.s.total>0).map(x=>[x.c.id,x.s.total])})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("regions", [["Worldwide"], ["Global"]])
def test_worldwide_card_with_partial_areas_agrees(regions):
    for q in ASKS:
        cards, cid = _patched(regions, q)
        out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps({"cards": cards, "asks": [q]}),
                             capture_output=True, text=True, check=True)
        js = dict(json.loads(out.stdout)[0])
        py = {r["id"]: r["fit"] for r in rank(cards, parse(q, cards))}
        assert cid in py, f"{q!r}: python dropped {cid}"
        assert cid in js, f"{q!r}: js dropped {cid} (python fit {py[cid]})"
        assert abs(py[cid] - js[cid]) <= 2, (q, py[cid], js[cid])
