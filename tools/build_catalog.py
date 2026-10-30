#!/usr/bin/env python3
"""Build catalog.json: the agent-facing map of what irlpls can be asked.

cards.json is 1 MB (about a quarter-million tokens): an agent cannot read it whole. catalog.json is the index an agent
reads first: five question types, ask families, example needs, the provider ids that answer each, and one compact
record per provider (route, coverage, price, proof, how to call it, whether the reference SDK can call it today).
It is generated from cards/*.json plus the adapter sources; nothing here is hand-written per provider except the
family membership table below, and the script fails if an in-scope card belongs to no family.

    python3 tools/build_catalog.py --out /path/to/dir      # writes <dir>/catalog.json and catalog.index.json  (default: site/)
    python3 tools/build_catalog.py --out DIR --no-matcher  # skip the node run that records matcher top-3 per example

No network. Never runs build.py. Optional: node (to run site/match.js over each example need).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from irlpls.catalog import FAMILIES, build, usable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def load_cards():
    return usable([json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))])


# ---------------------------------------------------------------- matcher check (optional)
NODE = r"""
global.window={};const fs=require('fs');eval(fs.readFileSync(process.argv[1],'utf8'));
const M=window.IRLMatch;const cards=JSON.parse(fs.readFileSync(process.argv[2])).cards.filter(M.inScope);
const needs=JSON.parse(process.argv[3]);const out={};
for(const n of needs){const r=M.parse(n,cards);
 out[n]=cards.map(c=>({c,s:M.score(c,r)})).filter(x=>x.s.total>0).sort(M.compare).slice(0,3).map(x=>[x.c.id,x.s.total]);}
console.log(JSON.stringify(out));
"""


def matcher_top(needs, cards_json):
    if not shutil.which("node"):
        return None
    try:
        r = subprocess.run(["node", "-e", NODE, str(ROOT / "src" / "match.js"), str(cards_json), json.dumps(needs)],
                           capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except Exception:
        return None



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"), help="directory to write catalog.json into (default site/)")
    ap.add_argument("--no-matcher", action="store_true")
    ap.add_argument("--cards-json", default=None, help="a cards.json for the matcher check (default: <out>/cards.json, else site/cards.json)")
    a = ap.parse_args()
    cards = load_cards()
    cj = Path(a.cards_json) if a.cards_json else next((p for p in (Path(a.out) / "cards.json", ROOT / "site" / "cards.json") if p.exists()), None)
    needs = [n for f in FAMILIES for n in f[3]]
    top = None if a.no_matcher or not cj else matcher_top(needs, cj)
    try:
        cat, idx = build(cards, top)
    except ValueError as e:
        sys.exit(str(e))
    provs, fams = cat["providers"], cat["families"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalog.json").write_text(json.dumps(cat, separators=(",", ":"), ensure_ascii=False))
    (out / "catalog.index.json").write_text(json.dumps(idx, separators=(",", ":"), ensure_ascii=False))
    print(f"also {out / 'catalog.index.json'} {(out / 'catalog.index.json').stat().st_size // 1024} KB")
    print(f"wrote {out / 'catalog.json'}  {len(provs)} providers, {len(fams)} families, "
          f"{(out / 'catalog.json').stat().st_size // 1024} KB, matcher_checked={bool(top)}")


if __name__ == "__main__":
    main()
