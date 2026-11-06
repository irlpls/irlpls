"""Regressions from launch simulation round 2 (broker side). No network: the geocoder is stubbed out."""
from decimal import Decimal

import pytest

import irlpls
from irlpls import broker, geo
from irlpls.match import NO_PROVIDER


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(geo, "geocode", lambda p: None)


@pytest.mark.parametrize("text,usd", [
    ("photo of a street sign in Hanga Roa, Chile max $20", 20),
    ("500 photos of storefronts in Leeds budget $10", 10),
    ("get me a human photo in Leeds $1 max", 1),
    ("a photo of a bridge in Leeds under 5 dollars", 5),
    ("satellite image of Mato Grosso for free", 0),
    ("a photo, up to $12.50", Decimal("12.50")),
])
def test_budget_in_the_ask_is_parsed(text, usd):
    assert broker.budget_in(text) == Decimal(str(usd))


@pytest.mark.parametrize("text", ["photo within 30 minutes", "under 30 minutes in Leeds", "free-flowing river level in Comfort, TX",
                                  "photo of a bridge in Leeds"])
def test_no_budget_means_none(text):
    assert broker.budget_in(text) is None


def test_stated_budget_sets_the_ceiling_and_the_question_is_not_asked():
    j = irlpls.request("photo of a street sign in Hanga Roa, Chile max $20")
    assert j.policy.max_order_usd == Decimal("20")
    assert "max_order_usd" not in [q["field"] for q in j.to_dict()["questions"]]


def test_unstated_budget_still_asks_and_policy_ceiling_wins():
    d = irlpls.request("photo of a bridge in Leeds").to_dict()
    assert "max_order_usd" in [q["field"] for q in d["questions"]]
    j = irlpls.request("photo of a bridge in Leeds max $20", policy={"max_order_usd": 100})
    assert j.policy.max_order_usd == Decimal("100")


def test_callers_policy_object_is_not_mutated():
    from irlpls.policy import Policy
    p = Policy()
    irlpls.request("photo of a bridge in Leeds max $20", policy=p)
    assert p.max_order_usd is None


@pytest.mark.parametrize("ask", ["water level of Lake Titicaca, Puno, Peru now",
                                 "count people in line at the DMV in Fresno, CA right now"])
def test_empty_plan_is_a_no_provider_refusal(ask):
    d = irlpls.request(ask).to_dict()
    assert d["state"] == "failed" and d["reason_code"] == "no_provider" and d["plan"] == []
    assert d["refusal"]["msg"].startswith(NO_PROVIDER) and "irlpls cannot" in d["refusal"]["msg"]
    assert d["questions"] == []


def test_person_targeting_refusal_fails_with_an_empty_plan():
    d = irlpls.request("follow John Smith and tell me where he goes").to_dict()
    assert d["state"] == "failed" and d["reason_code"] == "person_targeting"
    assert d["plan"] == [] and d["best_card_id"] is None


def test_history_ask_gets_its_own_refusal():
    d = irlpls.request("what was the river level in Austin last year").to_dict()
    assert d["state"] == "failed" and d["reason_code"] == "history" and d["plan"] == []
    msg = d["refusal"]["msg"]
    assert "archive" in msg and "USGS" in msg and "new observations" in msg
    assert "menu" not in msg and "opening hours" not in msg


def test_web_fact_keeps_its_own_text():
    d = irlpls.request("what are the opening hours of the bakery on rue Cler, Paris").to_dict()
    assert d["reason_code"] == "web_fact"


def test_is_this_real_about_a_provider_is_answered_from_its_card():
    d = irlpls.request("is this real? is the photo from Prolific actually taken by a person at the location").to_dict()
    assert d["reason_code"] == "about_provider" and d["state"] == "failed" and d["plan"] == []
    assert "Prolific states" in d["refusal"]["msg"] and "claimed" in d["refusal"]["msg"]


@pytest.mark.parametrize("ask", ["is irlpls legit, who is behind it", "is irlpls trustworthy?"])
def test_about_irlpls_itself_is_answered_plainly(ask):
    d = irlpls.request(ask).to_dict()
    assert d["reason_code"] == "about_provider" and d["state"] == "failed" and d["plan"] == []
    msg = d["refusal"]["msg"]
    assert "verifies nothing itself" in msg and "https://irlpls.com/standard.html" in msg and "https://irlpls.com/chain.html" in msg
    assert "Nothing can buy rank" in msg and "no cut" not in msg.lower()


def test_a_real_photo_order_is_not_mistaken_for_a_trust_question():
    d = irlpls.request("I need a real photo of the bridge in Leeds").to_dict()
    assert d["reason_code"] != "about_provider"


def _opt(cid, fit, call=True, key=False):
    return {"card_id": cid, "fit": fit, "route": "commission", "policy": [], "readiness": {},
            "sdk": {"callable": call, "needs_key": key}}


def test_default_reason_says_why_it_is_not_the_top_entry():
    j = broker.Job("x", None, None)
    j.plan = [_opt("kled", 63, call=False), _opt("prolific", 49)]
    assert j.best()["card_id"] == "prolific"
    r = j.default_reason()
    assert "prolific" in r and "kled" in r and "no commission adapter" in r
    j.plan = [_opt("a", 63), _opt("b", 49)]
    assert j.best()["card_id"] == "a" and j.default_reason() is None


def test_default_reason_rides_on_the_default_plan_entry(monkeypatch):
    j = irlpls.request("100 photos of storefronts in Leeds within 14 days")
    d = j.to_dict()
    top = max(d["plan"], key=lambda o: o["fit"] or 0)
    dflt = next(o for o in d["plan"] if o.get("default"))
    assert dflt["card_id"] == top["card_id"] or dflt.get("default_reason")


def test_multi_part_names_the_planned_part_and_the_other_one():
    d = irlpls.request("weather in Springfield, IL now and a human photo of the main street").to_dict()
    w = next(w for w in d["warnings"] if w["code"] == "multi_part")
    assert "Planned:" in w["msg"] and "separate request" in w["msg"] and "photo" in w["msg"]


def test_multi_part_with_nothing_plannable_still_says_so():
    w = broker.multi_part("ship traffic and the weather and a photo of the gate in Mombasa", None)
    assert w and "none of the parts" in w["msg"] and "separate request" in w["msg"]


def test_single_part_ask_has_no_multi_part_warning():
    d = irlpls.request("river level at Comfort, TX now").to_dict()
    assert not any(w["code"] == "multi_part" for w in d["warnings"])


def test_approximate_geocode_adds_a_place_approximate_warning(monkeypatch):
    monkeypatch.setattr(geo, "geocode", lambda p: {"lat": 53.8, "lon": -1.55, "country_code": "gb", "state": None, "approximate": True})
    j = irlpls.request("photo of a bridge in Leeds max $20")
    w = [w for w in j.to_dict()["warnings"] if w["code"] == "place_approximate"]
    assert len(w) == 1 and "offline list" in w[0]["msg"] and "may be off" in w[0]["msg"]


def test_exact_geocode_has_no_place_approximate_warning(monkeypatch):
    monkeypatch.setattr(geo, "geocode", lambda p: {"lat": 53.8, "lon": -1.55, "country_code": "gb", "state": None})
    j = irlpls.request("photo of a bridge in Leeds max $20")
    assert not any(w["code"] == "place_approximate" for w in j.to_dict()["warnings"])


def test_slow_geocoder_falls_back_to_the_offline_gazetteer(monkeypatch):
    import time
    monkeypatch.setattr(geo, "geocode", lambda p: time.sleep(0.5))
    g, slow = broker._geocode_timeboxed("Munich", budget=0.05)
    assert slow and g and g["approximate"] is True and g["country_code"] == "de"
    g, slow = broker._geocode_timeboxed("Smallville nowhere xyz", budget=0.05)
    assert slow and g is None
