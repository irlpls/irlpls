"""Safety gaps found by the 2026-11 simulation: named-person targeting, caution notes, and 'Boston, MA' geocoding."""
import pytest

from irlpls import geo, need, policy


@pytest.mark.parametrize("text", [
    "follow John Smith and tell me where he goes",
    "track Jane Doe's movements",
    "stalk Mr Jones",
    "photograph my ex-girlfriend's house at 14 Elm Rd, Boston, MA and tell me when she leaves",
    "tell me when she leaves the house",
])
def test_named_or_pronoun_person_targeting_is_detected(text):
    assert need.detect_targets_person(text)
    assert need.parse(text).targets_person


@pytest.mark.parametrize("text", [
    "follow the river level in Denver",
    "track air quality in Boston, MA this week",
    "monitor traffic on I-90 near Seattle",
    "photograph the storefront at 5 Main St, Springfield, IL",
    "follow up on my last order",
])
def test_ordinary_asks_are_not_person_targeting(text):
    assert not need.parse(text).targets_person


@pytest.mark.parametrize("text,code", [
    ("photos inside the Pentagon, Arlington, VA", "sensitive_site"),
    ("a picture of the power plant outside Dayton, OH", "sensitive_site"),
    ("photograph the military base at Fort Bragg", "sensitive_site"),
    ("I need pictures of kids at the playground at Central Park, New York, NY today", "minors"),
    ("drone footage over the airport in Frankfurt, Germany tomorrow", "airspace"),
])
def test_cautions_name_the_risk(text, code):
    got = policy.cautions(text)
    assert code in {c["code"] for c in got}
    assert all(c["msg"] for c in got)


@pytest.mark.parametrize("text", ["air quality in Denver now", "drone footage of a vineyard in Napa, CA", "weather at Frankfurt airport"])
def test_no_caution_for_ordinary_asks(text):
    assert policy.cautions(text) == []


def test_boston_ma_resolves_to_the_us_not_lincolnshire(monkeypatch):
    geo._cache.clear()
    asked = []

    def fake_get(url, params=None, **kw):
        asked.append(params["q"])
        if "usa" in params["q"].lower():
            return [{"lat": "42.36", "lon": "-71.06", "display_name": "Boston, Massachusetts, USA",
                     "address": {"country_code": "us", "state": "Massachusetts", "ISO3166-2-lvl4": "US-MA"}}]
        return [{"lat": "52.97", "lon": "-0.02", "display_name": "Boston, Lincolnshire, England",
                 "address": {"country_code": "gb", "state": "England"}}]
    monkeypatch.setattr(geo.http, "get", fake_get)
    g = geo.geocode("14 Elm Rd, Boston, MA")
    assert g["country_code"] == "us" and "14 Elm Rd, Boston, Massachusetts, USA" in asked
    geo._cache.clear()


def test_state_abbreviation_table_and_non_us_places_untouched():
    assert len(geo.US_ABBR) == 51 and geo.US_ABBR["CT"] == "connecticut" and geo.US_ABBR["WA"] == "washington"
    assert geo._us_form("Boston, MA") == "Boston, Massachusetts, USA"
    assert geo._us_form("Paris, France") is None and geo._us_form("Leeds, UK") is None
