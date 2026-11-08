"""Cards declare their own family: membership derives from the card's `family` field."""
import json
from pathlib import Path

import pytest

from irlpls import catalog, validate

ROOT = Path(__file__).resolve().parent.parent
CARDS = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
IDS = {d[0] for d in catalog.FAMILY_DEFS}


def test_every_listed_card_has_a_known_family():
    for c in catalog.usable(CARDS):
        assert c.get("family") in IDS, f"{c['id']}: family {c.get('family')!r}"


def test_schema_declares_the_field():
    s = json.loads((ROOT / "schema" / "capture-card.schema.json").read_text())
    assert s["properties"]["family"]["type"] == "string"
    assert "family" not in s["required"]


def test_membership_derives_from_the_field():
    cards = catalog.usable(CARDS)
    mem = catalog.family_members(cards)
    for c in cards:
        assert c["id"] in mem[c["family"]]
    assert mem["river-water-level"] == sorted(mem["river-water-level"])


def test_new_card_joins_by_its_field_without_editing_catalog():
    c = {**next(x for x in CARDS if x["id"] == "airnow"), "id": "example-new-air", "family": "air-quality"}
    cat, _ = catalog.build(catalog.usable(CARDS) + [c])
    assert "example-new-air" in cat["families"][[f["id"] for f in cat["families"]].index("air-quality")]["providers"]


def test_unknown_family_fails_the_build():
    c = {**next(x for x in CARDS if x["id"] == "airnow"), "id": "example-bad", "family": "no-such-family"}
    with pytest.raises(ValueError, match="unknown family"):
        catalog.build(catalog.usable(CARDS) + [c])


def test_card_in_no_family_fails_the_build_with_the_new_hint():
    c = {k: v for k, v in next(x for x in CARDS if x["id"] == "airnow").items() if k != "family"}
    c["id"] = "example-orphan"
    with pytest.raises(ValueError, match=r'add "family": "<id>"'):
        catalog.build(catalog.usable(CARDS) + [c])


def test_repo_gate_names_unknown_family(tmp_path):
    d = tmp_path / "cards"
    d.mkdir()
    c = {**next(x for x in CARDS if x["id"] == "airnow"), "family": "nope"}
    (d / "airnow.json").write_text(json.dumps(c))
    assert any("unknown family" in m for m in validate.repo_problems(d))
