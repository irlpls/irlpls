"""H1 (SECURITY-REVIEW-2026-11): no input shape may cost super-linear time; huge numbers never raise OverflowError."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from irlpls import match, need, policy

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 0.5


def _shapes(n):
    for ch in " \t\n 　,;.-($9a1é":
        yield ch * n
    for u in ("1 ", "a, ", "123 Main St ", "near ", "and ", " and", ", and", "; ", "plus ", "as well as ", "within 5 ",
              "500 photos ", "St", "Aa", "A,", "A "):
        yield u * (n // len(u))
    yield "river level at Comfort, TX" + " " * n + "now"
    yield "x and " + " " * n + "and y"
    yield "Aa" * (n // 2) + ", TX"
    yield "under $" + "9" * n
    yield "9" * n + " photos"
    yield "within " + "9" * n + " hours"
    yield "photos of " + "the " * (n // 4) + "place"


def _timed(fn, *a):
    t = time.perf_counter()
    try:
        fn(*a)
    except ValueError:
        pass   # a clean rejection is fine; a traceback of any other kind is not
    return time.perf_counter() - t


@pytest.mark.parametrize("n", [200, 2000, 20000])
def test_parse_and_helpers_are_fast(n):
    fns = [need.parse, need.split_parts, need.localise, need.normalise, need.detect_targets_person, policy.cautions,
           need.anywhere, need.history, need.web_fact]
    for g in _shapes(n):
        for f in fns:
            assert _timed(f, g) < LIMIT, (f.__name__, g[:30], n)


def test_split_parts_whitespace_run_is_linear():
    for n in (3000, 20000):
        assert _timed(need.split_parts, "river level at Comfort, TX" + " " * n + "now") < 0.1


def test_cap_applies_inside_parse_before_regex():
    with pytest.raises(ValueError) as e:
        need.parse("a" * (need.MAX_NEED_CHARS + 1))
    assert "4,000" in str(e.value)
    assert _timed(need.parse, " " * 10**6) < 0.1
    assert need.parse("river level at Comfort, TX now").state == "texas"


def test_broker_request_long_whitespace_rejected_fast():
    import irlpls
    t = time.perf_counter()
    with pytest.raises(ValueError):
        irlpls.request("river level at Comfort, TX" + " " * 5000 + "now", geocode=False)
    assert time.perf_counter() - t < LIMIT


def test_huge_numbers_never_raise_overflow():
    big = 10 ** 400
    assert need._number(big) is False
    assert policy.valid_money(big) is False
    from decimal import Decimal
    assert policy.valid_money(Decimal("1e999999")) is False
    assert policy.valid_money(Decimal("12.5")) is True
    for text in ("under $1e999", "under $" + "9" * 5000, "9" * 5000 + " photos", "within " + "9" * 3000 + " hours",
                 "500000000000000000000 photos"):
        try:
            r = need.parse(text[:need.MAX_NEED_CHARS])
        except ValueError:
            continue
        assert r.quantity <= need.MAX_QUANTITY
        assert r.max_price_usd is None or r.max_price_usd < 1e15
    for k in ("lat", "within_minutes", "max_price_usd", "quantity"):
        with pytest.raises(ValueError):
            need.check_request(need.Request(need="river level at Comfort, TX now", **{k: big}))


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_matcher_is_linear_too():
    js = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,out=[];
for(const n of [200,2000,20000]){const S=[];for(const ch of [' ','\n',',',';','.','$','9','a',' ','　'])S.push(ch.repeat(n));
 for(const u of ['1 ','a, ','near ','and ',' and','; ','plus ','St','Aa','A,','A '])S.push(u.repeat(n/u.length|0));
 S.push('river level at Comfort, TX'+' '.repeat(n)+'now','Aa'.repeat(n/2)+', TX','under $'+'9'.repeat(n),'under $1e999');
 for(const g of S){const t=Date.now();M.parse(g,[]);out.push(Date.now()-t)}}
console.log(JSON.stringify(Math.max.apply(null,out)));
"""
    r = subprocess.run(["node", "-e", js, str(ROOT)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) < 500
