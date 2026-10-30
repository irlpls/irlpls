"""Planning fixes from the full-pass testers (docs/research/fullpass/{agent-mcp,interfaces,live-answers,data-docs,site}.md).

Offline: no geocoding (geocode=False) and no network; live reads are faked on the adapter.
"""
import ast
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import irlpls
from irlpls import adapters, broker, geo, match
from irlpls.need import parse
from irlpls.policy import Policy

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if match.in_scope(c) and c.get("status") != "sample"]
BY = {c["id"]: c for c in CARDS}


def top(q):
    return [s["id"] for s in match.rank(CARDS, parse(q, CARDS))]


# ---- 1. place parsing ------------------------------------------------------------------------------------------------
PLACES = [("radar image of the Port of Rotterdam, Netherlands tonight", "Port of Rotterdam, Netherlands", "nl"),
          ("new optical satellite image of the Port of Los Angeles, CA within 3 days", "Port of Los Angeles, CA", "us"),
          ("current ship positions in the Gulf of Finland, Finland", "Gulf of Finland, Finland", "fi"),
          ("wave height off Cape Cod, Massachusetts now", "Cape Cod, Massachusetts", "us"),
          ("newest satellite image of Lake Geneva, Switzerland", "Lake Geneva, Switzerland", "ch"),
          ("radar image of Rotterdam, Netherlands tonight", "Rotterdam, Netherlands", "nl"),
          ("tide at Santa Monica", "Santa Monica", "us"),
          ("air, Delhi", "Delhi", "in")]


@pytest.mark.parametrize("q,place,cc", PLACES)
def test_ports_landmarks_and_of_off_places(q, place, cc):
    r = parse(q, CARDS)
    assert (r.place, r.country_code) == (place, cc)


@pytest.mark.parametrize("q", ["5000 voice recordings in Swahili, within 3 weeks",
                               "10 voice recordings of people saying the weather in Spanish within 7 days"])
def test_languages_are_not_places(q):
    r = parse(q, CARDS)
    assert r.place is None and r.country_code is None and r.mode == "commission"


@pytest.mark.parametrize("q", ["500 clips of doors opening, anywhere", "1000 dashcam clips from any country",
                               "worldwide photos of street signs, 300 of them"])
def test_anywhere_is_no_place(q):
    r = parse(q, CARDS)
    assert r.anywhere and r.place is None and "worldwide" not in r.terms


def test_anywhere_answer_is_never_geocoded_and_a_later_place_replaces_it(monkeypatch):
    calls = []
    monkeypatch.setattr(geo, "geocode", lambda p: calls.append(p) or None)
    j = irlpls.request("500 first-person clips of a hand opening a push-bar door, within 7 days", quantity=500, geocode=False)
    assert j.state == "proposed" and not any(q["blocking"] for q in j.questions)   # a dataset doesn't block on Where?
    j.answer(place="anywhere")
    assert not calls and j.request.anywhere and j.request.country_code is None and j.request.lat is None
    assert not any(q["field"] == "place" for q in j.questions)
    j.answer(place="Da Nang, Vietnam")
    assert j.request.country_code == "vn"
    monkeypatch.setattr(geo, "geocode", lambda p: {"lat": 39.8, "lon": -98.6, "country_code": "us", "state": None, "subdivision": None})
    j.answer(place="United States")
    assert (j.request.country_code, j.request.lat, j.request.anywhere) == ("us", 39.8, False)


def test_later_place_answer_replaces_stale_coordinates_offline(monkeypatch):
    monkeypatch.setattr(geo, "geocode", lambda p: None)
    j = irlpls.request("drone photos of a roof within 3 days", geocode=False, lat=16.0, lon=108.2, country_code="vn")
    j.answer(place="Austin, TX")
    assert (j.request.lat, j.request.country_code, j.request.state) == (None, "us", "texas")


def test_live_ask_without_a_place_still_blocks():
    j = irlpls.request("river level now", geocode=False)
    assert j.state == "clarifying" and j.questions[0]["field"] == "place" and j.questions[0]["blocking"]


def test_geocode_tries_without_a_landmark_prefix(monkeypatch):
    asked = []

    def get(url, params=None, **kw):
        asked.append(params["q"])
        if params["q"] == "Los Angeles, CA":
            return [{"lat": "34.05", "lon": "-118.24", "display_name": "LA",
                     "address": {"country_code": "us", "state": "California", "ISO3166-2-lvl4": "US-CA"}}]
        return []
    monkeypatch.setattr(geo.http, "get", get)
    monkeypatch.setattr(geo, "_cache", {})
    g = geo.geocode("Port of Los Angeles, CA")
    assert asked[:2] == ["Port of Los Angeles, CA", "Los Angeles, CA"] and g["subdivision"] == "US-CA"


# ---- 2. sub-national coverage ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("card_id,cc,state,sub,want", [
    ("uk-ea-flood", "gb", "england", None, True), ("uk-ea-flood", "gb", "scotland", None, False),
    ("uk-ea-flood", "gb", None, "GB-ENG", True), ("errand", "kr", "seoul", None, True), ("errand", "kr", None, "KR-11", True),
    ("errand", "kr", None, "KR-26", False), ("getterdone", "us", "georgia", None, True), ("getterdone", "us", "illinois", None, False),
    ("uk-ea-flood", "gb", None, None, None)])
def test_subdivision_coverage_for_every_country(card_id, cc, state, sub, want):
    assert geo.covers(BY[card_id], cc, state, sub) is want


def test_oxford_england_reaches_the_environment_agency():
    assert top("river level at Oxford, England now")[0] == "uk-ea-flood"
    j = irlpls.request("river level at Oxford, England now", geocode=False, lat=51.75, lon=-1.25, country_code="gb", state="england")
    assert j.best()["card_id"] == "uk-ea-flood"


# ---- 3. every AGENTS.md example plans without a blocking question ---------------------------------------------------
def _agents_examples():
    text = (ROOT / "docs" / "AGENTS.md").read_text()
    sec = text.split("## How to ask", 1)[1].split("### How to read the catalog", 1)[0]
    for line in re.findall(r"^irlpls_request\((.*)\)\s*$", sec, re.M):
        call = ast.parse(f"f({line})".replace("true", "True").replace("false", "False"), mode="eval").body
        yield {k.arg: ast.literal_eval(k.value) for k in call.keywords}


EXAMPLES = list(_agents_examples())


def test_agents_examples_found():
    assert len(EXAMPLES) == 15


@pytest.mark.parametrize("kw", EXAMPLES, ids=[e["need"][:50] for e in EXAMPLES])
def test_agents_example_needs_plan_without_blocking(kw):
    kw = dict(kw)
    need = kw.pop("need")
    j = irlpls.request(need, geocode=False, **kw)
    assert j.state == "proposed", (need, j.state, j.questions, j.refusal)
    assert not [q for q in j.questions if q["blocking"]], need
    assert j.plan, need


# ---- 4. input validation ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("need,kw,msg", [
    ("", {}, "need is empty"), ("   ", {}, "need is empty"),
    ("river level now", {"lat": 999, "lon": 0}, "lat must be"), ("river level now", {"lat": float("nan"), "lon": 0}, "lat must be"),
    ("river level now", {"lat": 30}, "lat and lon together"), ("photos of doors", {"quantity": 0}, "quantity"),
    ("photos of doors", {"quantity": -5}, "quantity"), ("photos of doors", {"quantity": 10 ** 9}, "split it"),
    ("photos of doors", {"within_minutes": -10}, "within_minutes"), ("photos of doors", {"mode": "fast"}, "mode must be"),
    ("photos of doors", {"quantiy": 5}, "did you mean quantity"),
    ("photos of doors", {"policy": {"max_order": 40}}, "did you mean max_order_usd"),
    ("photos of doors", {"policy": {"max_order_usd": -5}}, "0 or more"),
    ("photos of doors", {"policy": Policy(max_order_usd=float("nan"))}, "0 or more")])
def test_bad_input_is_a_clear_error(need, kw, msg):
    with pytest.raises(ValueError, match=msg):
        irlpls.request(need, geocode=False, **kw)


def test_bad_answer_is_a_clear_error():
    j = irlpls.request("river level at Comfort, TX now", geocode=False, lat=29.97, lon=-98.9)
    with pytest.raises(ValueError, match="quantity"):
        j.answer(quantity=-3)


# ---- 5. a ceiling against an unpublished price is a violation -------------------------------------------------------
def test_unpublished_price_violates_max_order_usd():
    unpublished = [c for c in CARDS if not (c.get("access") or {}).get("cost") in ("free", "free with key")
                   and not (c.get("pricing") or {}).get("from_usd") and not c.get("protocol")]
    assert unpublished
    for c in unpublished[:5]:
        assert any("price not published" in v for v in Policy(max_order_usd=40).check(c)), c["id"]
    j = irlpls.request("photo of the storefront at 123 Main St, Springfield, IL today", geocode=False,
                       lat=39.80, lon=-89.64, policy={"max_order_usd": 40})
    for o in j.plan:
        if o["price"] is None:
            assert o["policy"], o["card_id"]
    assert j.best() is None or j.best()["price"] is not None


# ---- 6. refusals and redirects with reasons -------------------------------------------------------------------------
def test_person_targeting_is_refused_with_a_reason():
    j = irlpls.request("follow my ex-girlfriend Jane Doe around Austin, TX tomorrow and photograph who she meets",
                       why="I want to know who she's seeing", geocode=False, lat=30.27, lon=-97.74)
    d = j.to_dict()
    assert d["refusal"]["code"] == "person_targeting" and d["reason_code"] == "person_targeting"
    assert j.state == "failed" and not j.questions
    assert j.ruled_out[0]["reason"] == "policy:allow_person_targeting"


@pytest.mark.parametrize("q", ["today's menu at Chez Panisse, Berkeley, CA", "what's on the menu at Chez Panisse, Berkeley, CA this week",
                               "opening hours of the Louvre in Paris, France", "ticket prices at the Space Needle in Seattle, WA"])
def test_web_facts_are_redirected_never_planned(q):
    j = irlpls.request(q, geocode=False, lat=37.88, lon=-122.27)
    assert j.state == "failed" and j.plan == [] and j.refusal["code"] == "web_fact" and "web search" in j.refusal["msg"]
    assert j.ruled_out and j.ruled_out[0]["reason"] == "unsupported"


@pytest.mark.parametrize("q", ["photo of the specials board at Joe's Diner, 50 Elm St, Burlington, VT today",
                               "photo of the posted hours at the library at 801 SW 10th Ave, Portland, Oregon within 2 days",
                               "check shelf prices at grocery stores in Toronto, Canada"])
def test_on_site_asks_are_not_web_facts(q):
    j = irlpls.request(q, geocode=False, lat=45.5, lon=-122.7)
    assert j.refusal is None and j.plan


# ---- 7. better picks ------------------------------------------------------------------------------------------------
def test_us_air_quality_prefers_airnow():
    for q in ("air quality in Phoenix, Arizona now", "PM2.5 near Portland, OR now"):
        assert top(q)[0] == "airnow", q
    assert top("air quality sensor readings in Berlin, Germany now")[0] != "airnow"


@pytest.mark.parametrize("q,want", [("latest earthquakes near Athens, Greece", "emsc-seismicportal"),
                                     ("latest earthquakes near Wellington, New Zealand", "geonet-nz"),
                                     ("latest earthquakes in Japan", "usgs-earthquakes")])
def test_regional_earthquake_catalogue_wins_the_tie(q, want):
    assert top(q)[0] == want


def test_choose_changes_best():
    j = irlpls.request("latest earthquakes near Athens, Greece", geocode=False, lat=37.98, lon=23.73)
    other = next(o["card_id"] for o in j.plan if o["card_id"] != j.best()["card_id"])
    j.choose(other)
    assert j.best()["card_id"] == other and j.to_dict()["choice"]["card_id"] == other


class _Fake:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, lat, lon, radius_km=3, limit=3, **kw):
        return adapters.base.Results([dict(r) for r in self.rows], [])


def _live(monkeypatch, card_id, rows):
    a = adapters.get(card_id)
    monkeypatch.setattr(a, "live", _Fake(rows))
    monkeypatch.setattr(irlpls, "live", lambda cid, **kw: adapters.get(cid).live(kw.get("lat"), kw.get("lon")))


def test_new_satellite_image_rejects_archive_scenes(monkeypatch):
    j = irlpls.request("new optical satellite image of the Port of Los Angeles, CA within 3 days", geocode=False,
                       lat=33.73, lon=-118.26)
    j.choose("copernicus-sentinel")
    _live(monkeypatch, "copernicus-sentinel", [{"provider": "copernicus-sentinel", "kind": "image", "label": "S2",
                                               "phenomenon_time": "2026-01-01T10:00:00Z"}])
    j.submit()
    assert j.state != "delivered" and not j.results
    w = next(w for w in j.warnings if w["code"] == "not_new")
    assert "2026-01-01" in w["msg"] and "before you asked" in w["msg"]


def test_empty_live_read_ends_with_a_plain_warning(monkeypatch):
    j = irlpls.request("PM2.5 near Berlin, Germany now", geocode=False, lat=52.52, lon=13.4, policy={"max_order_usd": 0})
    j.choose("sensor-community")
    _live(monkeypatch, "sensor-community", [])
    j.submit()
    w = next(w for w in j.warnings if w["code"] == "no_results")
    assert "within 3 km" in w["msg"] and "No readings" in w["msg"]


# ---- site: live asks read as live, and the browser matcher agrees ---------------------------------------------------
@pytest.mark.parametrize("q", ["tide at Santa Monica", "earthquake Alaska", "river Vienna", "air quality London", "air, Delhi",
                               "ships in the Gulf of Finland"])
def test_condition_asks_are_live(q):
    assert parse(q, CARDS).mode == "live"


def test_tide_at_santa_monica_is_not_german_or_english():
    ids = top("tide at Santa Monica")
    assert ids[0] == "noaa-tides" and not {"pegelonline", "uk-ea-flood"} & set(ids)


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch;
console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(q=>{const r=M.parse(q,[]);
  return [r.place||null,r.country_code,r.state,r.subdivision||null,!!r.anywhere,r.mode]})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_parse_parity_on_places():
    qs = [p[0] for p in PLACES] + ["5000 voice recordings in Swahili, within 3 weeks", "500 clips of doors opening, anywhere",
                                   "river level at Oxford, England now", "photo in Seoul", "earthquake Alaska",
                                   "storefront near Austin, TX"]
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    py = [[r.place, r.country_code, r.state, r.subdivision, r.anywhere, r.mode] for r in (parse(q) for q in qs)]
    assert json.loads(out.stdout) == py


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_covers_parity_on_subdivisions():
    node = (r"const fs=require('fs'),path=require('path');global.window={};"
            r"eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));"
            r"const C=JSON.parse(fs.readFileSync(0,'utf8'));"
            r"console.log(JSON.stringify(C.cases.map(x=>window.IRLMatch.covers(C.cards[x[0]],x[1],x[2],x[3]))))")
    cases = [["uk-ea-flood", "gb", "england", None], ["uk-ea-flood", "gb", "scotland", None], ["errand", "kr", None, "KR-11"],
             ["getterdone", "us", "georgia", None], ["errand", "kr", "seoul", None], ["uk-ea-flood", "gb", None, None]]
    inp = json.dumps({"cards": {k: BY[k] for k in ("uk-ea-flood", "errand", "getterdone")}, "cases": cases})
    out = subprocess.run(["node", "-e", node, str(ROOT)], input=inp, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [match.covers(BY[a], b, c, d) for a, b, c, d in cases]
