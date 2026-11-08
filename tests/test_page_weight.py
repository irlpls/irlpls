"""Page weight: the landing and Find must not pay for the whole registry twice (docs/research/launch/A11Y-PERF-2026-11.md, finding 1).

- index loads screen 1's example cards from the HTML and everything else from cards-lite.json (matcher fields only);
  find loads cards-lite.json; neither fetches the 1.2 MB /cards.json
- /cards.json (the public contract agents fetch) keeps its full shape and every card in it equals cards/<id>.json
- cards-lite carries every field src/match.js, labels.js and the stage code read: ranking over lite == ranking over full
Measured 2026-11-06 (unthrottled, idle): index 1.23 to 1.51 MB (was 2.39 to 2.67), find 0.89 MB (was 1.83).
"""
import functools
import http.server
import json
import pathlib
import shutil
import socketserver
import subprocess
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
pytestmark = pytest.mark.skipif(not (SITE / "cards-lite.json").exists(), reason="site/ not built")

INDEX_BUDGET = 1_740_000   # ~15% over the heaviest measured idle load (1.51 MB, with the hero clip)
FIND_BUDGET = 1_100_000    # ~15% over 0.95 MB at 132 cards (cards-lite 233 KB, was 334 KB; grows ~1.8 KB per card, asserted in the slim-view test)
INDEX_DOC_BUDGET = 125_000  # the HTML itself (108 KB measured): the registry is not in it
MAX_REQUEST = 400_000


def _asks():
    import importlib.util
    s = importlib.util.spec_from_file_location("bench", ROOT / "tests" / "test_match_bench.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    qs = [b[0] for b in m.BENCH] + list(m.BEST_ASKS)
    for f in json.loads((SITE / "bench_asks.json").read_text())["families"].values():
        qs += [a["ask"] for a in f["asks"]]
    return list(dict.fromkeys(qs))


def test_cards_json_contract_unchanged():
    d = json.loads((SITE / "cards.json").read_text())
    assert {"assurances", "cards"} <= set(d) <= {"assurances", "cards", "health"}
    assert len(d["cards"]) >= 90
    for c in d["cards"]:
        one = json.loads((SITE / "cards" / f'{c["id"]}.json').read_text())
        assert c == one, f'cards.json and cards/{c["id"]}.json differ'
    assert all("sources" in c and "api" in c for c in d["cards"]), "cards.json must stay the full card"
    assert (SITE / "cards.json").stat().st_size > 900_000


def test_lite_is_a_strict_slim_view():
    full = {c["id"]: c for c in json.loads((SITE / "cards.json").read_text())["cards"]}
    lite = json.loads((SITE / "cards-lite.json").read_text())["cards"]
    assert [c["id"] for c in lite] == list(full)
    assert (SITE / "cards-lite.json").stat().st_size < 0.3 * (SITE / "cards.json").stat().st_size
    assert (SITE / "cards-lite.json").stat().st_size / len(lite) < 2_000, "cards-lite grows over 2 KB per card (1.77 KB at 132): derive or drop a field"

    def checked_derived(a, b, path):  # summary/tasks/method are derived (build.py lite_summary, lite_tasks, _method_hits): only the full card's own words
        import re
        words = set(re.split(r"[^a-z0-9.]+", " ".join(b if isinstance(b, list) else [b]).lower()))
        for part in (a if isinstance(a, list) else [a]):
            for tok in re.split(r"[^a-z0-9.]+", part.lower()):
                assert not tok or tok == "allweather" or tok in words or tok in " ".join(b if isinstance(b, list) else [b]).lower(), f"{path}: {tok!r} is not in the full card"

    def sub(a, b, path=""):  # every lite value is the full card's value (verification may be reduced to its level/method)
        if path.endswith((".summary", ".capabilities.tasks")) or path.endswith(".method") and ".verification." in path:
            return checked_derived(a, b, path)
        if isinstance(a, dict):
            assert isinstance(b, dict), path
            for k, v in a.items():
                assert k in b, f"{path}.{k} not in the full card"
                sub(v, b[k], f"{path}.{k}")
        elif isinstance(a, list):
            assert isinstance(b, list) and len(a) == len(b), path
            for i, (x, y) in enumerate(zip(a, b)):
                sub(x, y, f"{path}[{i}]")
        else:
            assert a == b, f"{path}: {a!r} != {b!r}"
    for c in lite:
        sub(c, full[c["id"]], c["id"])


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};const R=process.argv[1];
eval(fs.readFileSync(path.join(R,'src/labels.js'),'utf8'));
eval(fs.readFileSync(path.join(R,'src/match.js'),'utf8'));const M=window.IRLMatch;
const ld=f=>JSON.parse(fs.readFileSync(path.join(R,'site',f),'utf8')).cards;
const F=ld('cards.json').filter(c=>c&&c.verification&&M.inScope(c)),L=ld('cards-lite.json').filter(c=>c&&c.verification&&M.inScope(c));
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
const run=(C,q)=>{const r=M.parse(q,C);return C.map(c=>({c:c,s:M.score(c,r)})).sort(M.compare).map(x=>[x.c.id,x.s.total,x.s.verdict,x.s.gaps||null,x.s.parts&&x.s.parts.matched||null])};
const stat=C=>C.map(c=>[c.id,M.stageLevels(c),M.stageTally(c,true),M.stageTally(c,false),window.IRLLabels.priceText(c),M.isFree(c),M.isKeyless(c)]);
console.log(JSON.stringify({ids:[F.map(c=>c.id),L.map(c=>c.id)],ranks:Q.map(q=>[run(F,q),run(L,q)]),stat:[stat(F),stat(L)]}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_lite_ranks_like_full():
    qs = _asks()
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    r = json.loads(out.stdout)
    assert r["ids"][0] == r["ids"][1] and len(r["ids"][0]) >= 85
    bad = [q for q, (f, l) in zip(qs, r["ranks"]) if f != l]
    assert not bad, f"{len(bad)} asks rank differently over cards-lite: {bad[:3]}"
    assert len(qs) > 100
    assert r["stat"][0] == r["stat"][1], "stage dots, tally or price text differ over cards-lite"


@pytest.fixture(scope="module")
def served():
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    h.log_message = lambda *a: None
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _idle_requests(base, page_name):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 844})
        got = []
        pg.on("requestfinished", lambda r: got.append((r.url.rsplit("/", 1)[-1] or "/", r.sizes()["responseBodySize"])))
        pg.goto(f"{base}/{page_name}")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1500)
        b.close()
    return got


@pytest.mark.parametrize("name,budget", [("index.html", INDEX_BUDGET), ("find.html", FIND_BUDGET)])
def test_first_load_weight(served, name, budget):
    got = _idle_requests(served, name)
    names = [n for n, _ in got]
    total = sum(s for _, s in got)
    assert "cards.json" not in names, "the full 1.2 MB registry must not load on this page"
    assert "cards-lite.json" in names
    assert total <= budget, f"{name}: {total} bytes at idle (budget {budget})"
    big = [(n, s) for n, s in got if s > MAX_REQUEST]
    assert not big, f"{name}: requests over {MAX_REQUEST} bytes: {big}"
    if name == "index.html":
        assert dict(got)["index.html"] <= INDEX_DOC_BUDGET


def test_index_embeds_only_the_first_screen():
    import re
    h = (SITE / "index.html").read_text()
    blob = json.loads(re.search(r'<script id="cards" type="application/json">(.*?)</script>', h, re.S).group(1))
    assert 1 <= len(blob["cards"]) <= 8 and blob["n"] >= 85
    assert len(json.dumps(blob)) < 20_000


def test_shards_stay_small():
    m = SITE / "shards" / "manifest.json"
    if not m.exists():
        pytest.skip("shards not built")
    man = json.loads(m.read_text())
    assert max(s["bytes"] for s in man["shards"]) <= 1 << 20, "a catalog shard is over 1 MiB"
    assert max(p["bytes"] for p in man["packs"]) <= 25 << 20
    assert sum(1 for f in (SITE / "shards").rglob("*") if f.is_file()) <= 600
    assert sum(1 for f in SITE.rglob("*") if f.is_file()) <= 15_000
