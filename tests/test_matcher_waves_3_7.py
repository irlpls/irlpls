"""Regression asks from docs/research/launch/MATCHER-QA-WAVES-3-7.md (D2, D3, D5). Offline, no geocoding."""
import json
import pathlib

import pytest

from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]


def ids(q, n=8):
    return [r["id"] for r in rank(CARDS, parse(q, CARDS))[:n]]


@pytest.mark.parametrize("q,card", [
    ("current temperature in Cape Town", "za-weathersa-wis2-synop"),
    ("temperature in Santiago, Chile right now", "cl-meteochile-wis2-synop"),
    ("is it windy in Montevideo", "uy-inumet-wis2-synop"),
    ("is it cold in Punta Arenas", "cl-meteochile-wis2-synop"),
    ("is there space in a parking garage in Strasbourg", "strasbourg-parking-occupancy"),
    ("is the Parkhaus in Basel full", "basel-parkhaus-occupancy"),
])
def test_local_card_returned(q, card):
    assert card in ids(q, 4)


def test_earthquake_santiago_not_weather_card():
    assert "cl-meteochile-wis2-synop" not in ids("earthquake in Santiago Chile", 3)
