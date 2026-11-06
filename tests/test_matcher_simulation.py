"""Matcher fixes from the launch simulation (docs/research/launch/SIMULATION-2026-11.md, F2, F3, F6, F7) plus the named-person
targeting pattern. Python (irlpls/need.py, match.py) and src/match.js must agree on every case."""
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


def ids(q):
    return [s["id"] for s in rank(CARDS, parse(q, CARDS))]


# ---- F3: asks nothing listed can serve must not default to a provider that cannot ----
UNSERVABLE = [
    ("photo of a polar bear in Svalbard, Norway right now", {"prolific", "toloka", "kled"}),
    ("is the bakery on Rue de Rivoli, Paris, France open right now? need a photo", {"satellogic", "skyfi", "planet"}),
    ("live reading of the ocean temperature at the South Pole", {"nws", "noaa-tides", "ndbc-buoys"}),
    ("send a human to Kathmandu, Nepal to count people in Durbar Square this afternoon", {"toloka", "prolific", "satellogic"}),
    ("is it raining", {"nws", "fmi-open-data", "ndbc-buoys", "open-meteo", "weatherxm-agent", "windy-webcams"}),
]


@pytest.mark.parametrize("q,banned", UNSERVABLE, ids=[u[0][:50] for u in UNSERVABLE])
def test_unservable_ask_has_no_wrong_provider(q, banned):
    assert not set(ids(q)) & banned, (q, ids(q)[:5])


def test_kathmandu_is_a_visit_not_a_live_read():
    r = parse("send a human to Kathmandu, Nepal to count people in Durbar Square this afternoon", CARDS)
    assert r.mode == "commission" and r.country_code == "np"


def test_no_place_live_ask_ranks_no_live_feed():
    r = parse("is it raining", CARDS)
    assert r.mode == "live" and not r.place
    for s in rank(CARDS, r):
        c = next(c for c in CARDS if c["id"] == s["id"])
        assert "live" not in c["delivery"]["modes"], s["id"]


def test_unsupported_wording_when_nothing_fits():
    assert "No provider in the catalog covers this" in match.unsupported(parse("is it raining", CARDS), rank(CARDS, parse("is it raining", CARDS)))
    r = parse("river level on Shoal Creek in Austin, TX right now", CARDS)
    assert match.unsupported(r, rank(CARDS, r)) is None


def test_south_pole_is_antarctica():
    assert parse("temperature at the South Pole", CARDS).country_code == "aq"


# ---- F7: the past is not an irlpls ask ----
HISTORY = ["what was the river level in Austin, TX last year", "air quality in Delhi in 2023", "tide at Galveston, TX 3 months ago",
           "historical wildfire hotspots in California"]
NOT_HISTORY = ["river level on Shoal Creek in Austin, TX right now", "earthquake near Los Angeles in the last hour",
               "what was the magnitude of the quake 10 minutes ago near Seattle", "earthquakes near Tokyo in the past 24 hours",
               "wildfire hotspots in California in the past 2 days", "air quality in Fresno, CA this morning"]


@pytest.mark.parametrize("q", HISTORY)
def test_history_is_refused_like_a_web_fact(q):
    assert need.history(q) and need.web_fact(q)


@pytest.mark.parametrize("q", NOT_HISTORY)
def test_recent_lookback_is_not_history(q):
    assert not need.history(q)


def test_history_job_is_redirected_not_planned():
    import irlpls
    j = irlpls.request("what was the river level in Austin, TX last year", geocode=False)
    assert j.plan == [] and j.refusal["code"] == "web_fact" and "new observations" in j.refusal["msg"]


# ---- F6: questions about a provider are answered from its card ----
ABOUT = [("Is Prolific verified?", "prolific", "verification"), ("is this provider verified? Prolific", "prolific", "verification"),
         ("is the photo from GetterDone verified to be taken at the location", "getterdone", "verification"),
         ("how much does GetterDone cost?", "getterdone", "price")]


@pytest.mark.parametrize("q,card,aspect", ABOUT)
def test_about_provider(q, card, aspect):
    got = match.about_provider(q, CARDS)
    assert got and got[0]["id"] == card and got[1] == aspect
    assert rank(CARDS, parse(q, CARDS)) == []   # never routed to another provider
    text = match.about_answer(got[0], aspect)
    assert got[0]["name"] in text and ("claimed" in text or "evidenced" in text or "none" in text or aspect == "price")


@pytest.mark.parametrize("q", ["air quality in Fresno, CA right now", "is the Guadalupe River at Comfort, TX flooding right now",
                               "how much water is in the Potomac at Washington, DC right now", "photo of the storefront at 123 Main St, Springfield, IL today"])
def test_data_asks_are_not_about_a_provider(q):
    assert match.about_provider(q, CARDS) is None


# ---- F2: other languages keep the place and route like their English twins ----
MULTI = [("東京の今の空気質を教えて", "Tokyo", "jp", "air quality in Tokyo now"),
         ("какая погода сейчас в Москве", "Moscow", "ru", "weather now in Moscow"),
         ("nivel del río en Sevilla, España ahora", "Seville, Spain", "es", "river level in Seville, Spain now"),
         ("Wie hoch ist der Rhein bei Köln gerade?", "Cologne", "de", None),
         ("niveau de la Seine à Paris maintenant", "Paris", "fr", None)]


@pytest.mark.parametrize("q,place,cc,twin", MULTI, ids=[m[0][:20] for m in MULTI])
def test_non_english_keeps_place_and_goes_live(q, place, cc, twin):
    r = parse(q, CARDS)
    assert r.place == place and r.country_code == cc and r.mode == "live" and r.need == q
    if twin:
        e = parse(twin, CARDS)
        assert (e.place, e.country_code, e.mode) == (r.place, r.country_code, r.mode)


@pytest.mark.parametrize("q", [m[0] for m in MULTI])
def test_non_english_never_defaults_to_a_us_only_provider(q):
    assert not {"nws", "usgs-water", "airnow", "noaa-tides", "ndbc-buoys"} & set(ids(q))


def test_english_asks_are_untouched_by_localising():
    for q in ["river level in Rio de Janeiro", "level of the Seine in Paris now", "air quality in Fresno, CA right now"]:
        assert need.localise(q) == q


def test_pairs_resolve_to_a_national_gauge():
    assert ids("niveau de la Seine à Paris maintenant")[0] == "hubeau-hydrometrie"
    assert ids("Wie hoch ist der Rhein bei Köln gerade?")[0] == "pegelonline"


# ---- named-person targeting (moved from policy.py) ----
NAMED = ["follow John Smith and tell me where he goes", "track Jane Doe", "stalk Mr Jones", "tell me when she leaves"]


@pytest.mark.parametrize("q", NAMED)
def test_named_person_targets(q):
    assert need.detect_targets_person(q) and parse(q, CARDS).targets_person


@pytest.mark.parametrize("q", ["track the Potomac river level", "follow the flood at Comfort, TX", "monitor air quality in Fresno, CA"])
def test_places_are_not_named_people(q):
    assert not need.detect_targets_person(q)


def test_policy_no_longer_patches_need():
    src = (ROOT / "irlpls" / "policy.py").read_text()
    assert "_need.TARGETS_PERSON" not in src


# ---- Python / JS parity ----
ALL = [u[0] for u in UNSERVABLE] + [a[0] for a in ABOUT] + [m[0] for m in MULTI] + HISTORY + NAMED

NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,dir=path.join(process.argv[1],'cards');
const C=fs.readdirSync(dir).filter(f=>f.endsWith('.json')).sort().map(f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'))).filter(c=>M.inScope(c)&&c.status!=='sample');
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(Q.map(q=>{const r=M.parse(q,C);
  const l=C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>x.s.total==null||x.s.total>0).sort(M.compare);
  const a=M.aboutProvider(q,C);
  return {mode:r.mode,place:r.place||null,cc:r.country_code||null,targets:!!r.targets_person,history:M.history(q),about:a&&[a.card.id,a.aspect],
          top:l.slice(0,3).map(x=>[x.c.id,x.s.total]),loc:M.localise(q)}})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_parity():
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(ALL), capture_output=True, text=True, check=True)
    bad = []
    for q, j in zip(ALL, json.loads(out.stdout)):
        r = parse(q, CARDS)
        p = rank(CARDS, r)
        a = match.about_provider(q, CARDS)
        py = {"mode": r.mode, "place": r.place, "cc": r.country_code, "targets": bool(r.targets_person), "history": need.history(q),
              "about": a and [a[0]["id"], a[1]], "loc": need.localise(q)}
        got = {k: j[k] for k in py}
        top_ok = (not p and (not j["top"] or a)) or (p and j["top"] and p[0]["id"] == j["top"][0][0] and abs(p[0]["fit"] - j["top"][0][1]) <= 2)
        if got != py or not top_ok:
            bad.append((q, py, got, [(s["id"], s["fit"]) for s in p[:2]], j["top"][:2]))
    assert not bad, "\n".join(map(str, bad))
