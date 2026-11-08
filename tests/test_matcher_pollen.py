"""Regression asks for the pollen-allergy family. Offline, no geocoding."""
import json
import pathlib

import pytest

from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]
POLLEN = "dwd-pollen-gefahrenindex"


def ids(q, n=3):
    return [r["id"] for r in rank(CARDS, parse(q, CARDS))[:n]]


@pytest.mark.parametrize("q", [
    "pollen forecast Germany today",
    "hay fever risk Hamburg",
    "birch pollen count this week",
    "is it a high pollen day in Munich",
])
def test_pollen_top3(q):
    assert POLLEN in ids(q), ids(q, 6)


def test_air_quality_not_pollen_first():
    top = ids("air quality in Berlin", 10)
    assert top[0] != POLLEN
    if POLLEN in top:
        fam = {c["id"]: c.get("family") for c in CARDS}
        first_aq = min(i for i, x in enumerate(top) if fam.get(x) == "air-quality")
        assert first_aq < top.index(POLLEN), top
