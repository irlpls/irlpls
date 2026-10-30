"""Where a card stands (irlpls/standing.py): ranks on its own families' bench asks, the computed what-if, and the agent
sentence, all from one list: the broker's plan options for the ask. Every number is checked against an independent
plan; the sentence and the rank must agree for every card and ask; check.html's browser ranks (src/match.js) must
agree with Python's.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

from irlpls import standing

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
LISTED = standing.listed(CARDS)


def test_every_family_has_asks_and_every_ask_is_from_the_bench_or_the_catalog():
    from irlpls.catalog import FAMILIES
    from tests.test_match_bench import BENCH
    bench_qs = {b[0] for b in BENCH}
    examples = {q for _f, _t, _ti, ex, _p in FAMILIES for q in ex}
    fams = standing.bench()
    assert set(fams) == {f[0] for f in FAMILIES}
    for fid, asks in fams.items():
        assert 1 <= len(asks) <= 4, fid
        for a in asks:
            assert a["ask"] in (examples if a.get("source") == "catalog" else bench_qs), a["ask"]
            assert 0 < len(a["label"]) <= 34


def _job(ask, cards):
    """An independent broker plan for the ask, these cards as the registry (what an agent calling plan/best gets)."""
    import irlpls
    from irlpls import make_request
    from irlpls.broker import Job
    old = irlpls._registry
    irlpls._registry = standing._Reg(cards)
    try:
        req = make_request(ask, geocode=False)
        job = Job(req.need, req, None)
        job._plan(cards)
        return job
    finally:
        irlpls._registry = old


@pytest.mark.parametrize("cid", ["proxypics", "caltrans-cctv", "getterdone", "usgs-water", "human-for-ai"])
def test_ranks_are_the_plans_own_positions(cid):
    rows = standing.ranks(cid, CARDS)
    assert rows and all(r["family"] in standing.families_of(cid) for r in rows)
    for r in rows:
        ids = [o["card_id"] for o in _job(r["ask"], LISTED).plan if not o["policy"]]
        assert r["of"] == len(ids)
        assert r["pos"] == (ids.index(cid) + 1 if cid in ids else None)


def test_the_sentence_and_the_rank_agree_for_every_card_and_bench_ask():
    """One list: the 'option N of M' an agent says is the '#N of M' the card page shows, for every card on every ask."""
    asks = {a["ask"] for c in LISTED for a in standing.asks_for(c["id"])}
    plans = {q: standing.plan(q, LISTED) for q in asks}
    checked = 0
    for c in LISTED:
        rows = standing.ranks(c["id"], LISTED, plans)
        for r in rows:
            job, opts = plans[r["ask"]]
            if not r["pos"]:
                assert c["id"] not in [o["card_id"] for o in opts], (c["id"], r["ask"])
                continue
            only = [dict(r2, pos=None) if r2["ask"] != r["ask"] else r2 for r2 in rows]   # the sentence for this ask
            s = standing.says(c["id"], LISTED, only, plans)
            assert s and s["ask"] == r["ask"] and (s["pos"], s["of"]) == (r["pos"], r["of"]), (c["id"], r["ask"], s, r)
            best = job.best()
            if best and best["card_id"] == c["id"]:
                assert "default pick" in s["line"]
            else:
                assert f"option {r['pos']} of {r['of']}" in s["line"], (s["line"], r)
            checked += 1
        s = standing.says(c["id"], LISTED, rows, plans)
        if s:
            row = next(r for r in rows if r["ask"] == s["ask"])
            assert (s["pos"], s["of"]) == (row["pos"], row["of"])
    assert checked > 50


def test_proxypics_ranks_first_for_a_storefront_in_springfield_and_is_not_covered_in_nairobi():
    rows = {r["label"]: r for r in standing.ranks("proxypics", CARDS)}
    assert rows["Storefront photo, Springfield IL"]["pos"] == 1
    assert rows["Storefront photo, Nairobi"]["pos"] is None and rows["Storefront photo, Nairobi"]["covered"] is False


def test_move_up_is_computed_by_re_planning_never_guessed():
    up = standing.move_up("caltrans-cctv", CARDS)
    assert up and up["before"] > up["after"]
    alt = [standing._evidenced(c, up["keys"]) if c["id"] == "caltrans-cctv" else c for c in LISTED]
    ids = [o["card_id"] for o in _job(up["ask"], alt).plan if not o["policy"]]
    assert ids.index("caltrans-cctv") + 1 == up["after"]
    assert up["how"].startswith("providers.html#")


def test_no_move_up_when_already_first_everywhere_it_ranks():
    rows = standing.ranks("proxypics", CARDS)
    assert all(r["pos"] in (1, None) for r in rows)
    assert standing.move_up("proxypics", CARDS, rows) is None


def test_the_agent_sentence_comes_from_the_plan():
    s = standing.says("proxypics", CARDS)
    assert s and s["line"].startswith("For “") and "ProxyPics" in s["line"]
    job = _job(s["ask"], LISTED)
    opt = next(o for o in job.plan if o["card_id"] == "proxypics")
    assert s["ready"] == opt["readiness"]["line"]
    assert ("default pick" in s["line"]) == (job.best()["card_id"] == "proxypics")
    assert ("within " + opt["speed"]) in s["line"]
    levels = set(opt["proof"].values())
    assert ("claimed, not evidenced" in s["proof"]) == ("claimed" in levels)


def test_a_published_card_is_ranked_as_published():
    """The site ranks the provider's own copy; a provider-maintained card wins a tie only, never on fit."""
    pp = json.loads(json.dumps(next(c for c in CARDS if c["id"] == "proxypics")))
    pp["status"] = "provider-maintained"
    swapped = [pp if c["id"] == "proxypics" else c for c in CARDS]
    a = {r["ask"]: r for r in standing.ranks("proxypics", CARDS)}
    b = {r["ask"]: r for r in standing.ranks("proxypics", swapped)}
    for q in a:
        assert a[q]["fit"] == b[q]["fit"]
        assert b[q]["pos"] is None or a[q]["pos"] is None or b[q]["pos"] <= a[q]["pos"]


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,dir=path.join(process.argv[1],'cards');
const C=fs.readdirSync(dir).filter(f=>f.endsWith('.json')).sort().map(f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'))).filter(c=>M.inScope(c)&&c.status!=='sample');
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(Q.map(([id,q])=>{const r=M.parse(q,C);
  const l=C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>(x.s.total==null||x.s.total>0)&&M.plannable(x.c,r)).sort(M.compare);
  const i=l.findIndex(x=>x.c.id===id);return [i<0?null:i+1,l.length]})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_check_page_ranks_match_python():
    qs, want = [], []
    for cid in sorted({c["id"] for c in LISTED}):   # every card, every one of its asks
        for r in standing.ranks(cid, CARDS):
            qs.append([cid, r["ask"]])
            want.append([r["pos"], r["of"]])
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    bad = [(q, w, g) for q, w, g in zip(qs, want, got) if w != g]
    assert not bad, bad
