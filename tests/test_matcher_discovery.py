"""Matcher weaknesses found by the agent-discovery tests: geography ties, "in" as a country,
"tonight", a no-match verdict, late delivery, multi-part asks. Python, and src/match.js under node when available."""
import json
import pathlib
import shutil
import subprocess

import pytest

from irlpls.match import in_scope, rank, score
from irlpls.need import parse, split_parts

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]
BY = {c["id"]: c for c in CARDS}
HAS_NODE = bool(shutil.which("node"))

NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,dir=path.join(process.argv[1],'cards');
const C=fs.readdirSync(dir).filter(f=>f.endsWith('.json')).sort().map(f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'))).filter(c=>M.inScope(c)&&c.status!=='sample');
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(Q.map(q=>{const r=M.parse(q,C);
  const l=C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>x.s.total==null||x.s.total>0).sort(M.compare);
  return {cc:r.country_code,state:r.state,mode:r.mode,within:r.within_minutes,parts:r.parts,
    top:l.slice(0,5).map(x=>[x.c.id,x.s.total,x.s.verdict])}})));
"""


def js(qs):
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def py(q):
    return parse(q, CARDS), [(s["id"], s["fit"], s["verdict"]) for s in rank(CARDS, parse(q, CARDS))]


# (1) geography -----------------------------------------------------------------------------------------------------
GAZ = [("traffic camera in Helsinki", "fi"), ("river level of the Thames", "gb"), ("Rhine at Cologne water level", "de"),
       ("weather in Oxford", "gb"), ("air quality in Lagos", "ng"), ("port of Singapore", "sg"), ("tide in Cornwall", "gb"),
       ("webcam in Leeds", "gb"), ("air in Seoul right now", "kr"), ("bakery in Lisbon", "pt"), ("smoke in Seattle", "us"),
       ("traffic in LA", "us"), ("Venice webcam", "it")]


@pytest.mark.parametrize("q,cc", GAZ)
def test_gazetteer_resolves_country(q, cc):
    assert parse(q).country_code == cc


def test_helsinki_traffic_camera_prefers_finland():
    r, res = py("traffic camera in Helsinki")
    assert r.country_code == "fi" and "fintraffic-weathercams" in [x[0] for x in res[:3]]


def test_unknown_place_penalises_explicit_elsewhere_and_prefers_global():
    q = "traffic camera in Zzyzx"          # no country known for the place
    r = parse(q, CARDS)
    assert r.country_code is None and r.place
    s = score(BY["fintraffic-weathercams"], r)
    assert any("may not be where" in g for g in s["gaps"])
    glob = [c for c in CARDS if "*" in (c["coverage"].get("areas") or [])]
    assert glob and score(glob[0], r)["parts"].get("coverage", 1) >= s["parts"]["coverage"]
    # no place at all: no geography penalty
    assert not any("may not be where" in g for g in score(BY["fintraffic-weathercams"], parse("traffic camera", CARDS))["gaps"])


# (2) "in" is a word ------------------------------------------------------------------------------------------------
def test_in_is_never_a_country_code():
    for q in ["river level in Delhi", "photos of a storefront in Delhi, in", "weather in the field in lower case, in"]:
        r = parse(q)
        assert r.state != "indiana", q
    assert parse("river level in Delhi").country_code == "in"          # India from the gazetteer, not from the word "in"
    assert parse("storefront in Delhi, in").state is None
    assert parse("storefront in Delhi, IN").state is None              # a non-US city before the code keeps its country
    assert parse("storefront in Indianapolis, IN").state == "indiana"  # a real capitalised code still works
    assert parse("storefront near Austin, TX").state == "texas"


# (3) tonight -------------------------------------------------------------------------------------------------------
def test_tonight_is_commission_with_a_deadline_of_hours():
    r = parse("SAR image of the Port of Rotterdam tonight", CARDS)
    assert r.mode == "commission" and r.within_minutes == 720
    assert parse("river level right now").mode == "live"


def test_tonight_needs_live_or_hours_latency():
    r = parse("SAR image of the Port of Rotterdam tonight", CARDS)
    assert score(BY["umbra"], r)["parts"]["coverage"] >= 0.9              # card says 1-4 hours
    slow = json.loads(json.dumps(BY["umbra"]))
    slow["delivery"]["speed"] = "days"
    s = score(slow, r)
    assert s["parts"]["coverage"] < score(BY["umbra"], r)["parts"]["coverage"] - 0.2 and any("tonight" in g for g in s["gaps"])
    assert s["fit"] < score(BY["umbra"], r)["fit"]
    res = [x["id"] for x in rank(CARDS, r)]
    assert set(res[:2]) <= {"capella", "umbra", "iceye"}   # providers whose cards say hours lead the free archive pass


# (4) verdict -------------------------------------------------------------------------------------------------------
def test_verdict_bands_and_strong_fits_unchanged():
    r = parse("is the Guadalupe River at Comfort, TX flooding right now", CARDS)
    top = rank(CARDS, r)[0]
    assert top["id"] == "usgs-water" and top["verdict"] == ("strong" if top["fit"] >= 60 else "weak")
    for s in rank(CARDS, r):
        want = "strong" if s["fit"] >= 60 else "weak" if s["fit"] >= 30 else "none"
        assert s["verdict"] == want
    r = parse("what is my neighbour doing right now", CARDS)
    assert all(s["verdict"] != "strong" for s in rank(CARDS, r))


# (5) late delivery -------------------------------------------------------------------------------------------------
def test_weeks_against_three_days_lowers_fit_materially():
    r = parse("SAR image of the Port of Rotterdam, 3 days", CARDS)
    assert r.within_minutes == 4320
    days, weeks = json.loads(json.dumps(BY["umbra"])), json.loads(json.dumps(BY["umbra"]))
    days["delivery"]["speed"], weeks["delivery"]["speed"] = "days", "weeks"
    d, w = score(days, r)["fit"], score(weeks, r)["fit"]
    assert d and w <= d * 0.75, (d, w)
    # a longer deadline is not penalised
    r = parse("SAR image of the Port of Rotterdam, 8 weeks", CARDS)
    assert score(weeks, r)["fit"] == score(days, r)["fit"]


# (6) parts ---------------------------------------------------------------------------------------------------------
def test_multi_part_asks():
    r = parse("river level near Austin TX and photos of the storefront at the bakery on Main", CARDS)
    assert len(r.parts) == 2 and r.parts[0].startswith("river level")
    assert split_parts("photos of the bakery and cafe") == []
    assert split_parts("traffic cameras in Austin and Dallas") == []
    assert parse("river level near Comfort TX").parts == []
    assert "parts" not in parse("river level near Comfort TX").to_dict()


# JS parity ---------------------------------------------------------------------------------------------------------
QS = ["traffic camera in Helsinki", "Rhine at Cologne water level", "river level in Delhi", "storefront in Delhi, IN",
      "SAR image of the Port of Rotterdam tonight", "river level right now", "what is my neighbour doing right now",
      "photos of a storefront in Austin, TX, 3 days", "traffic camera in Zzyzx",
      "river level near Austin TX and photos of the storefront at the bakery on Main", "photos of the bakery and cafe"]


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_js_parity_discovery():
    out = js(QS)
    for q, j in zip(QS, out):
        r, res = py(q)
        assert (j["cc"], j["state"], j["mode"], j["within"]) == (r.country_code, r.state, r.mode, r.within_minutes), q
        assert j["parts"] == r.parts, q
        assert bool(j["top"]) == bool(res), q
        if not res:
            continue
        assert j["top"][0][0] == res[0][0] and abs(j["top"][0][1] - res[0][1]) <= 2, (q, j["top"][:3], res[:3])
        assert j["top"][0][2] == res[0][2] or abs(j["top"][0][1] - res[0][1]) > 0, q


# countries by name and short form, and places the landing used to carry itself ------------------------------------
COUNTRY_QS = [("drone photos of a field in Ukraine", "ua"), ("weather in the United Kingdom", "gb"), ("river level in Britain", "gb"),
              ("storefront photo in the UK", "gb"), ("traffic camera in the US", "us"), ("air quality in the USA", "us"),
              ("flooding in vietnam right now", "vn"), ("photos of a port in Morocco", "ma"), ("street photos in Czech Republic", "cz"),
              ("satellite image of Papua New Guinea", "pg"), ("tide gauge in New South Wales", "au"), ("crop field in Kazakhstan", "kz"),
              ("building in Côte d'Ivoire", "ci"), ("drone photos of kharkiv", "ua"), ("street in Kharkiv today", "ua")]


@pytest.mark.parametrize("q,cc", COUNTRY_QS)
def test_countries_by_name_and_short_form(q, cc):
    assert parse(q).country_code == cc


def test_everyday_words_are_not_countries():
    for q in ["photos of a turkey farm", "china plates on a shelf", "canada geese at the lake", "river level in new mexico",
              "the west bank of the river", "storefront in Georgia"]:
        assert parse(q).country_code in (None, "us"), q


def test_kharkiv_drops_us_only_providers():
    r = parse("photo of a building in Kharkiv", CARDS)
    assert r.country_code == "ua"
    assert "US" in BY["proxypics"]["coverage"]["areas"]
    s = score(BY["proxypics"], r)
    assert s["fit"] == 0 and s["parts"].get("outside_coverage")
    assert "proxypics" not in [x["id"] for x in rank(CARDS, r)]
    assert all(score(c, r)["fit"] == 0 for c in CARDS if c["coverage"].get("areas")
               and "*" not in c["coverage"]["areas"] and "ua" not in [a.lower() for a in c["coverage"]["areas"]]
               and not any(a.lower().startswith("ua-") for a in c["coverage"]["areas"]))


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_js_gazetteer_is_the_python_gazetteer():
    from irlpls.need import CASELESS, CITIES, COUNTRIES
    node = ("const fs=require('fs'),path=require('path');global.window={};"
            "eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));"
            "const g=window.IRLMatch.gazetteer;console.log(JSON.stringify([Object.entries(g.countries),Object.entries(g.cities),g.caseless]))")
    out = subprocess.run(["node", "-e", node, str(ROOT)], capture_output=True, text=True, check=True)
    countries, cities, caseless = json.loads(out.stdout)
    assert countries == [list(x) for x in COUNTRIES.items()]                   # same names, same order (first match wins)
    assert cities == [[k, list(v)] for k, v in CITIES.items()]
    assert caseless == [list(x) for x in CASELESS]


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_js_parity_countries_and_kharkiv():
    qs = [q for q, _ in COUNTRY_QS] + ["photo of a building in Kharkiv"]
    for q, j in zip(qs, js(qs)):
        r, res = py(q)
        assert (j["cc"], j["state"]) == (r.country_code, r.state), q
        assert bool(j["top"]) == bool(res), q
        assert not res or j["top"][0][0] == res[0][0], (q, j["top"][:3], res[:3])
        assert "proxypics" not in [x[0] for x in j["top"]] or r.country_code in ("us", "gb", None), q


# person targeting: questions about a relation's whereabouts, and strangers pinned to a place -------------------------
TARGETED = ["what is my neighbour doing right now", "where is my ex tonight", "follow my coworker",
            "photograph the woman at 12 Elm St", "is my husband at the bar", "where's my girlfriend right now",
            "who is my wife meeting tonight", "is my boss really at home",
            # round 4: these stay refused while a family member's house is a place
            "where is my mom right now", "follow my ex", "photograph my neighbour", "photograph my mom",
            "watch my ex's apartment every night", "follow my dad to his house"]
UNTARGETED = ["track my package", "photo of the storefront", "count people in line at the DMV", "crowd size at the stadium",
              "is the bakery open", "send someone to photograph the storefront at 12 Elm St", "where is the nearest river gauge",
              "what is the river level right now", "is the road at the bridge flooded", "photos of the man-made lake",
              # round 4 (a visitor was refused): a family member's house is a place, not the person
              "someone to check on my mom's house in Tampa after the storm", "photo of my dad's farm roof after the hail",
              "check on my sister's apartment in Austin, TX"]


@pytest.mark.parametrize("q", TARGETED)
def test_person_targeting_flags_relation_questions_and_strangers(q):
    assert parse(q).targets_person, q


@pytest.mark.parametrize("q", UNTARGETED)
def test_person_targeting_no_false_positives(q):
    assert not parse(q).targets_person, q


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_js_targets_person_parity():
    node = ("const fs=require('fs'),path=require('path');global.window={};"
            "eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));"
            "console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(q=>window.IRLMatch.targetsPerson(q))))")
    qs = TARGETED + UNTARGETED
    out = subprocess.run(["node", "-e", node, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [parse(q).targets_person for q in qs]


# round 4: landmarks resolve to their state; one photo of one spot ranks on-site capture before dataset vendors ---------
LANDMARKS = [("live traffic webcam of the Golden Gate Bridge", "us", "california"),
             ("live webcam view of traffic on the Golden Gate Bridge right now", "us", "california"),
             ("someone to check on my mom's house in Tampa after the storm", "us", "florida"),
             ("crowd at Times Square now", "us", "new york"), ("photo of the Eiffel Tower today", "fr", None)]


@pytest.mark.parametrize("q,cc,state", LANDMARKS)
def test_landmarks_and_cities_resolve(q, cc, state):
    r = parse(q)
    assert (r.country_code, r.state) == (cc, state), q


def test_golden_gate_ranks_california_cameras_not_finland_or_london():
    for q in ("live traffic webcam of the Golden Gate Bridge", "live webcam view of traffic on the Golden Gate Bridge right now"):
        _, res = py(q)
        ids = [x[0] for x in res]
        assert ids[0] == "caltrans-cctv", (q, ids[:5])
        assert not {"fintraffic-weathercams", "tfl-jamcams", "wsdot-cameras"} & set(ids), (q, ids[:5])


SINGLE = ["photo of a burned building in Kharkiv, Ukraine taken today", "verify a photo of a flooded street in Lagos, Nigeria"]


@pytest.mark.parametrize("q", SINGLE)
def test_single_capture_ranks_on_site_providers_before_dataset_vendors(q):
    from irlpls.match import bulk_vendor
    r, res = py(q)
    assert not bulk_vendor(BY[res[0][0]]), (q, res[:5])
    first_bulk = next(i for i, x in enumerate(res) if bulk_vendor(BY[x[0]]))
    assert all(not bulk_vendor(BY[x[0]]) for x in res[:first_bulk]) and first_bulk >= 1


def test_datasets_are_not_down_ranked():
    from irlpls.match import bulk_vendor
    _, res = py("500 egocentric clips")
    assert bulk_vendor(BY[res[0][0]]) or res[0][0] == "kled", res[:3]
    assert not any(score(BY[x[0]], parse("500 egocentric clips", CARDS))["parts"].get("bulk") for x in res)


def test_unknown_coverage_is_flagged():
    r = parse("photo of a burned building in Kharkiv, Ukraine taken today", CARDS)
    assert score(BY["toloka"], r)["parts"].get("coverage_unknown")
    assert not score(BY["rentahuman"], r)["parts"].get("coverage_unknown")


@pytest.mark.skipif(not HAS_NODE, reason="node not installed")
def test_js_parity_round_4():
    qs = [q for q, _, _ in LANDMARKS] + SINGLE + ["500 egocentric clips"]
    for q, j in zip(qs, js(qs)):
        r, res = py(q)
        assert (j["cc"], j["state"]) == (r.country_code, r.state), q
        assert [x[0] for x in j["top"]] == [x[0] for x in res[:5]], (q, j["top"], res[:5])
        assert [x[1] for x in j["top"]] == [x[1] for x in res[:5]], q
