"""Launch premortem regressions: what a stranger's agent hits after `pip install irlpls irlpls-mcp`."""
import json
import re
from pathlib import Path

import irlpls
from irlpls import catalog, need

ROOT = Path(__file__).resolve().parent.parent


def test_catalog_runtime_build_tolerates_a_package_with_fewer_cards():
    cards = [c for c in irlpls.registry().cards if c["id"] != "hubeau-hydrometrie"]
    cat, _ = catalog.build(cards, strict=False)
    assert "hubeau-hydrometrie" not in cat["providers"] and cat["families"]


def test_packaged_cards_cover_every_catalog_family():
    ids = {c["id"] for c in json.loads((ROOT / "irlpls" / "data" / "cards.json").read_text())["cards"]}
    want = {p for f in catalog.FAMILIES for p in f[4]}
    assert not want - ids, f"irlpls/data/cards.json is stale (run build.py): {sorted(want - ids)}"


def test_an_address_keeps_its_city_and_state_without_commas():
    assert need.parse("photo of the storefront at 5 Main St Springfield IL today").place == "5 Main St Springfield IL"


def test_a_huge_need_is_refused_not_echoed_back():
    import pytest
    with pytest.raises(ValueError, match="characters"):
        irlpls.request("x" * 100000)


def test_choose_survives_into_the_next_cli_process(tmp_path, monkeypatch):
    from irlpls import broker
    monkeypatch.setattr(broker, "HOME", tmp_path)
    broker._JOBS.clear()
    j = irlpls.request("air quality in Denver now")
    j.choose("purpleair")
    broker._JOBS.clear()   # a new process
    assert broker.load_job(j.id).choice["card_id"] == "purpleair"


def test_unlocated_live_place_is_said_not_silently_planned(monkeypatch):
    from irlpls import geo
    monkeypatch.setattr(geo, "geocode", lambda p: None)
    d = irlpls.request("river level at Comfort, TX now").to_dict()
    assert any(q["field"] == "lat" and not q["blocking"] for q in d["questions"])


def test_camera_asks_are_live_and_pick_free_tfl():
    for q in ("Traffic camera in London?", "London cameras", "webcam in Lisbon"):
        assert need.parse(q).mode == "live", q
    assert irlpls.request("Traffic camera in London?").best()["card_id"] == "tfl-jamcams"
    assert need.parse("100 camera trap images of foxes in Kent").mode == "commission"


def test_other_planets_are_not_places():
    j = irlpls.request("weather on Mars").to_dict()
    assert j["state"] == "clarifying" and "Earth" in j["questions"][0]["ask"]
