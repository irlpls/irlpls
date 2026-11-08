"""Scale fixture: the synthetic generator is deterministic and valid, and clones in unrelated places do not
change the ranking of real cards (prefilter safety)."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

import pytest

from irlpls.match import rank
from irlpls.need import parse
from irlpls.validate import problems
from test_match_bench import BENCH, CARDS

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("synth_cards", ROOT / "tools" / "synth_cards.py")
synth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synth)

TEMPLATES = synth.load_templates()


def test_deterministic_and_valid():
    a, b = synth.generate(TEMPLATES, 200, 7), synth.generate(TEMPLATES, 200, 7)
    assert a == b
    assert a != synth.generate(TEMPLATES, 200, 8)
    assert len({c["id"] for c in a}) == 200
    for c in a:
        assert c["status"] == "sample" and c["name"].startswith("Synthetic Provider ")
        errs = [p for p in problems(c) if p[0] == "error"]
        assert not errs, (c["id"], errs)


def test_refuses_protected_dirs(tmp_path):
    for bad in (ROOT / "cards" / "x", ROOT / "site" / "y"):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "synth_cards.py"), "--out", str(bad), "--n", "1"],
                           capture_output=True, text=True)
        assert r.returncode != 0 and not bad.exists()
    out = tmp_path / "o"
    subprocess.run([sys.executable, str(ROOT / "tools" / "synth_cards.py"), "--out", str(out), "--n", "3"], check=True,
                   capture_output=True)
    assert len(list(out.glob("*.json"))) == 3


@pytest.mark.slow
def test_clones_in_unrelated_areas_keep_top5_stable():
    clones = synth.generate(TEMPLATES, 2000, 1)
    real_ids = {c["id"] for c in CARDS}
    changed, times = [], []
    everything = CARDS + clones   # one list for every ask, so the auto-index is built once (as the site does), not per ask
    rank(everything, parse(BENCH[0][0], CARDS))   # warm-up: the one-off index build is not a per-ask latency
    for q, _top, _not3 in BENCH:
        req = parse(q, CARDS)
        base = [s["id"] for s in rank(CARDS, req)][:5]
        t = time.perf_counter()
        full = rank(everything, req)
        times.append(time.perf_counter() - t)
        got = [s["id"] for s in full if s["id"] in real_ids][:5]
        if got != base:
            changed.append((q, base, got))
    times.sort()
    p50, p95 = times[len(times) // 2], times[int(len(times) * 0.95) - 1]
    print(f"\nrank with +2000 clones over {len(times)} asks: p50={p50*1000:.0f} ms p95={p95*1000:.0f} ms")
    assert not changed, json.dumps(changed, indent=1)
    assert p95 < 5
