"""Catalog shards (irlpls/shards.py; SCALE-SEARCH-ARCH.md step 7, first slice)."""
import hashlib
import importlib.util
import json
import pathlib
import random

import pytest

import build
from irlpls import shards

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _synth():
    s = importlib.util.spec_from_file_location("synth_cards", ROOT / "tools" / "synth_cards.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def _real():
    return [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))
            if json.loads(p.read_text()).get("status") != "defunct"]


@pytest.fixture(scope="module")
def cards():
    return _real()


@pytest.fixture(scope="module")
def built(cards, tmp_path_factory):
    d = tmp_path_factory.mktemp("sh") / "shards"
    return d, shards.write_shards(cards, build.lite, d)


def _tree(d):
    return {str(f.relative_to(d)): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()}


def test_deterministic(cards, built, tmp_path):
    d, _ = built
    shuffled = list(cards)
    random.Random(3).shuffle(shuffled)
    shards.write_shards(shuffled, build.lite, tmp_path / "again")
    assert _tree(d) == _tree(tmp_path / "again")


def test_coverage(cards, built):
    d, man = built
    by_key = {}
    for s in man["shards"]:
        by_key.setdefault(s["key"], set()).update(c["id"] for c in json.loads((d / s["path"]).read_text()))
    for c in cards:
        areas = (c.get("coverage") or {}).get("areas") or []
        for a in areas:
            if a != "*":
                assert c["id"] in by_key[a.split("-")[0]], (c["id"], a)
        if not areas or "*" in areas:
            assert c["id"] in by_key["global"], c["id"]
    assert set(by_key) == set(man["keys"])


def test_shard_records_are_the_lite_records(cards, built):
    d, man = built
    want = {c["id"]: json.loads(build._dump(build.lite(c))) for c in cards}
    for s in man["shards"]:
        for r in json.loads((d / s["path"]).read_text()):
            assert r == want[r["id"]]


def test_every_card_in_exactly_one_pack(cards, built):
    d, man = built
    seen = []
    for i, p in enumerate(man["packs"]):
        recs = json.loads((d / p["path"]).read_text())
        assert len(recs) == p["cards"] <= shards.PACK_SIZE
        for r in recs:
            assert man["pack_of"][r["id"]] == i
        seen += [r["id"] for r in recs]
    assert sorted(seen) == sorted(c["id"] for c in cards) and len(seen) == len(set(seen))
    full = {c["id"]: c for c in cards}
    assert all(r == full[r["id"]] for r in json.loads((d / man["packs"][0]["path"]).read_text()))


def test_manifest_hashes_and_budgets(built):
    d, man = built
    assert man["version"] == shards.VERSION and man["cards"] == len(man["pack_of"])
    for e in man["shards"] + man["packs"]:
        b = (d / e["path"]).read_bytes()
        assert len(b) == e["bytes"] and hashlib.sha256(b).hexdigest() == e["sha256"]
    assert max(s["bytes"] for s in man["shards"]) <= shards.SHARD_MAX
    assert shards.check_budgets(d) == len(_tree(d))


def test_budget_check_fails(built, tmp_path):
    d, _ = built
    t = tmp_path / "s"
    t.mkdir()
    for k, v in _tree(d).items():
        (t / k).parent.mkdir(parents=True, exist_ok=True)
        (t / k).write_bytes(v)
    man = json.loads((t / "manifest.json").read_text())
    man["shards"][0]["bytes"] = shards.SHARD_MAX + 1
    (t / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(ValueError, match="shard"):
        shards.check_budgets(t)


def test_split_by_modality_when_over_limit(cards, tmp_path):
    man = shards.write_shards(cards, build.lite, tmp_path / "x", limit=4_000)
    assert max(s["bytes"] for s in man["shards"]) <= 4_000
    assert any(s["path"].startswith("global.") for s in man["shards"])
    ids = {r["id"] for s in man["shards"] if s["key"] == "global" for r in json.loads((tmp_path / "x" / s["path"]).read_text())}
    assert ids == {c["id"] for c in cards if "global" in shards.keys_of(c)}


def test_scale_5000_synthetic(tmp_path):
    sc = _synth()
    synth = sc.generate(sc.load_templates(), 5000, 1)
    man = shards.write_shards(synth, build.lite, tmp_path / "shards")
    files = [f for f in (tmp_path / "shards").rglob("*") if f.is_file()]
    assert man["cards"] == 5000 and len(man["packs"]) == -(-5000 // shards.PACK_SIZE)
    assert len(files) == len(man["shards"]) + len(man["packs"]) + 1 <= 400
    assert max(s["bytes"] for s in man["shards"]) <= shards.SHARD_MAX
    assert shards.check_budgets(tmp_path / "shards") == len(files)
