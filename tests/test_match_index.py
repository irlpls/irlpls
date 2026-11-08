"""The candidate index must change nothing but speed: same fit>0 set and same ordered top 20 as brute force."""
import ast
import json
import pathlib
import random
import statistics
import sys
import time

import pytest

from irlpls import index as ix
from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import synth_cards  # noqa: E402

from tests.test_match_bench import BENCH  # noqa: E402

CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]
BIG = CARDS + synth_cards.generate(synth_cards.load_templates(), 2000, 7)

WORDS = ["river level", "air quality", "storefront photo", "traffic camera", "earthquakes", "tide", "wildfire smoke",
         "roof inspection", "satellite radar imagery", "thermal imaging", "egocentric video clips", "shelf prices survey",
         "someone to stand in line", "parking garage", "weather temperature wind", "snow on the road", "dashcam driving",
         "pollution ozone", "construction site aerial", "kayaking", "bakery open", "drone photos", "sensor readings",
         "500 clips of a hand opening a door", "sea surface temperature", "cyclist at intersection", "flooded street",
         "photo", "video", "stuff", "blorptastic widget"]
PLACES = ["Wellington, New Zealand", "Dunedin", "Valparaiso, Chile", "Bergen, Norway", "Riga, Latvia", "Austin, TX",
          "London, UK", "Delhi, India", "Antarctica", "Nairobi, Kenya", "Seattle", "Windhoek, Namibia", "Tokyo, Japan", ""]
TAILS = ["right now", "today", "within 3 days", "tomorrow", "under $100", "with AI-training rights", ""]


def waves_asks():
    out = []
    for p in sorted((ROOT / "tests").glob("test_matcher_waves_3_7.py")):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and " " in n.value and 8 < len(n.value) < 300:
                out.append(n.value)
    return out


def generated(n=200):
    rng = random.Random(11)
    out = []
    for _ in range(n):
        w, pl, t = rng.choice(WORDS), rng.choice(PLACES), rng.choice(TAILS)
        out.append(" ".join(x for x in (w, f"in {pl}" if pl else "", t) if x))
    return out


ASKS = list(dict.fromkeys([b[0] for b in BENCH] + waves_asks() + generated()))


def parsed(cards, q):
    try:
        return parse(q, cards)
    except ValueError:
        return None


def check(cards, label):
    idx = ix.build(cards)
    bad, n = [], 0
    for q in ASKS:
        req = parsed(cards, q)
        if req is None:
            continue
        n += 1
        a, b = rank(cards, req, False), rank(cards, req, idx)
        if {s["id"] for s in a} != {s["id"] for s in b} or [s["id"] for s in a[:20]] != [s["id"] for s in b[:20]] \
                or [s["fit"] for s in a[:20]] != [s["fit"] for s in b[:20]]:
            bad.append(q)
    assert n > 200
    assert not bad, f"{label}: index differs from brute force on {bad[:5]}"


def test_index_equals_brute_force_real_cards():
    check(CARDS, "real")


def test_index_equals_brute_force_with_2000_clones():
    check(BIG, "real+2000")


def test_default_rank_uses_cached_index_for_large_lists():
    assert ix.cached(CARDS) is None            # short lists stay brute force
    assert ix.cached(BIG) is ix.cached(BIG)    # cached by list identity
    req = parse("river level in Austin, TX right now", BIG)
    assert [s["id"] for s in rank(BIG, req)] == [s["id"] for s in rank(BIG, req, False)]


def test_latency_before_after(capsys):
    idx = ix.build(BIG)
    qs = [parsed(BIG, q) for q in ASKS[:60]]
    qs = [r for r in qs if r]
    t = {"brute": [], "indexed": []}
    for r in qs:
        for name, arg in (("brute", False), ("indexed", idx)):
            s = time.perf_counter()
            rank(BIG, r, arg)
            t[name].append((time.perf_counter() - s) * 1000)
    q = lambda v, p: sorted(v)[min(len(v) - 1, int(p * len(v)))]  # noqa: E731
    line = f"rank latency at {len(BIG)} cards, {len(qs)} asks (ms): " + "; ".join(
        f"{k} p50={statistics.median(v):.1f} p95={q(v, .95):.1f}" for k, v in t.items())
    with capsys.disabled():
        print("\n" + line)
    s95, s50 = q(t["brute"], .95) / q(t["indexed"], .95), statistics.median(t["brute"]) / statistics.median(t["indexed"])
    with capsys.disabled():
        print(f"speedup p50 {s50:.1f}x, p95 {s95:.1f}x")
    # The p95 tail is broad asks ("weather", "snow on the road"): a third or more of the cards, and of their clones,
    # genuinely mention those words, so they are plausible candidates and are scored. Typical asks gain far more.
    assert s50 >= 3 and s95 >= 1.5, f"{line}; speedup p50 {s50:.1f}x p95 {s95:.1f}x"
