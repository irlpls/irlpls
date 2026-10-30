"""Freshness audit 2026-10 (docs/research/freshness-2026-10.md): pinned host moves and card facts."""
import json
import pathlib

import irlpls
from irlpls.adapters import get

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures" / "adapters"
NEW_STAC = "https://stac.opensearch.dataspace.copernicus.eu/v1"


def card(cid):
    return json.loads((ROOT / "cards" / f"{cid}.json").read_text())


def test_copernicus_uses_new_stac_host_everywhere():
    # Old host replaced 2027-01-12: https://dataspace.copernicus.eu/news/2026-10-16-new-sfeos-stac-catalogue-endpoint
    assert card("copernicus-sentinel")["api"]["base_url"] == NEW_STAC
    pc = get("copernicus-sentinel").prepare({"lat": 50.94, "lon": 6.96}, "live")
    assert pc["url"].startswith(NEW_STAC + "/search?") and "sortby=-datetime" in pc["url"]
    src = (ROOT / "irlpls" / "adapters" / "copernicus.py").read_text()
    assert 'STAC = "https://stac.dataspace.copernicus.eu' not in src


def test_copernicus_reads_recorded_new_host_response(monkeypatch):
    from irlpls import http
    rec = json.loads((FX / "copernicus-sentinel" / "stac_s2.json").read_text())
    seen = []

    def fake(url, params=None, **kw):
        seen.append(url)
        return rec if params["collections"] == "sentinel-2-l2a" else {"features": []}
    monkeypatch.setattr(http, "get", fake)
    out = irlpls.live("copernicus-sentinel", lat=50.94, lon=6.96)
    assert all(u.startswith(NEW_STAC) for u in seen)
    assert len(out) == 1 and out[0]["label"] == "Sentinel-2 optical scene"
    assert out[0]["phenomenon_time"] == "2026-10-28T10:41:39.024000Z" and out[0]["cloud_cover_pct"] == 17.56
    assert out[0]["result_time"] and out[0]["url"].startswith(NEW_STAC)


def test_iceye_card_names_odi_not_live_dc_atr():
    tasks = " ".join(card("iceye")["capabilities"]["tasks"])
    assert "ODI" in tasks and "removed 2026-11-15" in tasks


def test_defunct_mturk_names_no_adapter():
    assert "adapter" not in card("mturk")
