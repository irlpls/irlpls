"""Matcher fixes from launch simulation round 2 (docs/research/launch/SIMULATION-2-2026-11.md): other scripts and languages,
the fit floor, Antarctica, place parsing (Georgia, trailing clauses, Durbar Square) and mode. Python (irlpls/need.py, match.py)
and src/match.js must agree on every case. Parsing runs offline."""
import json
import pathlib
import shutil
import subprocess

import pytest

from irlpls import match, need
from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]
US_ONLY = {"nws", "usgs-water", "airnow", "noaa-tides", "ndbc-buoys"}


def ids(q):
    return [s["id"] for s in rank(CARDS, parse(q, CARDS))]


# ---- scripts and languages with no vocabulary: say so, never default to a country-locked card ----
UNREADABLE = ["ما هي جودة الهواء في القاهرة الآن؟", "दिल्ली में अभी हवा की गुणवत्ता कैसी है?",
              "지금 서울 강남역 사진 한 장 찍어줘", "请帮我拍一张上海外滩现在的照片"]


@pytest.mark.parametrize("q", UNREADABLE)
def test_unreadable_script_says_rephrase_and_ranks_nothing(q):
    r = parse(q, CARDS)
    assert need.ask_problem(r) == "rephrase"
    assert rank(CARDS, r) == []
    assert "rephrase in English" in match.unsupported(r, [])


def test_known_script_with_a_place_is_not_unreadable():
    assert need.ask_problem(parse("東京の今の空気質を教えて", CARDS)) is None
    assert need.ask_problem(parse("какая погода сейчас в Москве", CARDS)) is None


PT_IT = [("qualidade do ar em Maputo", "Maputo", "mz"), ("c'è neve a Cortina adesso?", "Cortina", "it"),
         ("qual é o nível do rio Tietê em São Paulo agora?", "São Paulo", "br")]


@pytest.mark.parametrize("q,place,cc", PT_IT)
def test_portuguese_and_italian_keep_place_and_go_live(q, place, cc):
    r = parse(q, CARDS)
    assert r.place == place and r.country_code == cc and r.mode == "live" and need.ask_problem(r) is None
    assert rank(CARDS, r), q


def test_portuguese_air_is_not_a_us_only_default():
    assert not US_ONLY & set(ids("qualidade do ar em Maputo")[:3])
    assert need.localise("qualidade do ar em Maputo") == "air quality in Maputo"


def test_no_place_question_never_defaults_to_a_country_locked_card():
    for q in ["is it raining", "what is the air quality", "how is the wind"]:
        for s in rank(CARDS, parse(q, CARDS)):
            c = next(c for c in CARDS if c["id"] == s["id"])
            assert not match.country_locked(c), (q, s["id"])


# ---- not an ask, not an observation ----
def test_help_is_no_ask():
    r = parse("help", CARDS)
    assert need.ask_problem(r) == "not_an_ask" and rank(CARDS, r) == []
    assert "not something to observe" in match.unsupported(r, [])


def test_delivery_is_not_routed_to_a_drone_provider():
    r = parse("deliver a package to Tbilisi, Georgia", CARDS)
    assert need.ask_problem(r) == "delivery" and rank(CARDS, r) == []


# ---- fit floor: a weak provider is not a plan option for something nobody can do ----
FLOORED = [("measure the soil moisture in my field in Punjab, Pakistan", {"nasa-firms"}),
           ("smell test: does the river in Kolkata smell bad", {"open-meteo", "hubeau-hydrometrie"}),
           ("geiger counter near Fukushima", {"weatherxm-agent"}),
           ("live wave height off Tofino", {"ndbc-buoys"}),
           ("traffic cam in Ulaanbaatar", {"hivemapper"})]


@pytest.mark.parametrize("q,banned", FLOORED, ids=[f[0][:40] for f in FLOORED])
def test_weak_fits_at_a_named_place_are_not_offered(q, banned):
    got = rank(CARDS, parse(q, CARDS))
    assert not banned & {s["id"] for s in got}, [(s["id"], s["fit"]) for s in got[:5]]
    assert all(s["fit"] > match.FLOOR for s in got)


def test_weak_fits_without_a_place_still_listed():
    q = "1000 dashcam clips of cyclists at dusk, within 14 days"
    assert rank(CARDS, parse(q, CARDS))


def test_south_pole_ocean_temperature_is_ruled_out():
    r = parse("live reading of the ocean temperature at the South Pole", CARDS)
    assert r.country_code == "aq" and rank(CARDS, r) == []


# ---- place parsing ----
def test_tbilisi_georgia_is_the_country():
    r = parse("weather in Tbilisi, Georgia now", CARDS)
    assert r.country_code == "ge" and r.state is None
    assert "nws" not in ids("weather in Tbilisi, Georgia now")
    r = parse("deliver a package to Tbilisi, Georgia", CARDS)
    assert r.country_code == "ge" and r.place == "Tbilisi"


def test_atlanta_georgia_is_still_the_us_state():
    r = parse("air quality in Atlanta, Georgia now", CARDS)
    assert r.country_code == "us" and r.state == "georgia"


def test_trailing_clause_is_not_part_of_the_place():
    r = parse("photo of my house in Chandigarh, India, I am abroad", CARDS)
    assert r.place == "Chandigarh, India"
    assert parse("river level in Austin, TX, I'm travelling", CARDS).place == "Austin, TX"


def test_landmark_keeps_its_country():
    r = parse("send a human to Kathmandu, Nepal to count people in Durbar Square this afternoon", CARDS)
    assert r.country_code == "np" and r.place.endswith("Nepal") and "Durbar Square" in r.place


# ---- mode ----
def test_any_fires_near_is_a_live_read():
    r = parse("any fires near Kelowna BC?", CARDS)
    assert r.mode == "live" and ids("any fires near Kelowna BC?")[0] == "nasa-firms"


def test_a_five_minute_human_is_a_commission():
    r = parse("get me a human in Tórshavn for 5 minutes", CARDS)
    assert r.mode == "commission" and r.within_minutes is None
    assert parse("500 clips, 7 days", CARDS).within_minutes == 7 * 1440
    assert parse("photo of the shop within 5 minutes", CARDS).within_minutes == 5


# ---- Python / JS parity ----
ALL = (UNREADABLE + [p[0] for p in PT_IT] + [f[0] for f in FLOORED] + [
    "help", "deliver a package to Tbilisi, Georgia", "live reading of the ocean temperature at the South Pole",
    "weather in Tbilisi, Georgia now", "air quality in Atlanta, Georgia now", "photo of my house in Chandigarh, India, I am abroad",
    "send a human to Kathmandu, Nepal to count people in Durbar Square this afternoon", "any fires near Kelowna BC?",
    "get me a human in Tórshavn for 5 minutes", "is it raining", "what is the air quality", "river level in Austin, TX, I'm travelling"])

NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,dir=path.join(process.argv[1],'cards');
const C=fs.readdirSync(dir).filter(f=>f.endsWith('.json')).sort().map(f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'))).filter(c=>M.inScope(c)&&c.status!=='sample');
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(Q.map(q=>{const r=M.parse(q,C);
  const l=C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>x.s.total==null||x.s.total>0).sort(M.compare);
  return {mode:r.mode,place:r.place||null,cc:r.country_code||null,state:r.state||null,within:r.within_minutes,problem:M.askProblem(r),
          top:M.askProblem(r)?[]:l.slice(0,3).map(x=>[x.c.id,x.s.total])}})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_parity():
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(ALL), capture_output=True, text=True, check=True)
    bad = []
    for q, j in zip(ALL, json.loads(out.stdout)):
        r = parse(q, CARDS)
        p = rank(CARDS, r)
        py = {"mode": r.mode, "place": r.place, "cc": r.country_code, "state": r.state, "within": r.within_minutes,
              "problem": need.ask_problem(r)}
        got = {k: j[k] for k in py}
        got["within"] = got["within"] if got["within"] is not None else None
        top_ok = (not p and not j["top"]) or (p and j["top"] and p[0]["id"] == j["top"][0][0] and abs(p[0]["fit"] - j["top"][0][1]) <= 2)
        if got != py or not top_ok:
            bad.append((q, py, got, [(s["id"], s["fit"]) for s in p[:2]], j["top"][:2]))
    assert not bad, "\n".join(map(str, bad))
