"""Regression asks for the wave-8 cards (transit, bike share, road traffic, avalanche). Offline, no geocoding."""
import json
import pathlib

import pytest

from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]


def ids(q, n=3):
    return [r["id"] for r in rank(CARDS, parse(q, CARDS))[:n]]


@pytest.mark.parametrize("q,card", [
    ("where are the trains in Finland now", "digitraffic-train-locations"),
    ("bikes available at Oslo bysykkel station", "oslo-bysykkel-gbfs"),
    ("traffic count on E18 Finland", "digitraffic-tms-traffic-counts"),
    ("road incidents and traffic messages in Finland", "digitraffic-road-traffic-messages"),
    ("avalanche danger Norway today", "nve-avalanche-warnings-norway"),
    ("next bus Brisbane live", "translink-seq-gtfs-rt"),
    ("live bus positions in Kuala Lumpur", "my-data-gov-gtfs-realtime"),
])
def test_wave8_card_top3(q, card):
    assert card in ids(q), ids(q, 6)
