"""USGS off WaterServices (waterservices.usgs.gov is slowed from 2026-11-16 and shut on 2027-02-22,
https://waterdata.usgs.gov/blog/wdfn-waterservices-degradation/). Every USGS call now goes to api.waterdata.usgs.gov:
latest-continuous (readings), monitoring-locations (names, browser), statistics/v0/observationNormals (usual for the date),
NIMS (cameras). Replies are the live recordings in fixtures/adapters/usgs-water (see its SOURCE); no network."""
import json
import pathlib
import re

import pytest

import irlpls
from irlpls import http
from irlpls.adapters import usgs_water

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "adapters" / "usgs-water"


def fix(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def recorded(monkeypatch):
    """The recorded replies, keyed by what was asked; returns the (url, params) list."""
    calls = []

    def get(url, params=None, headers=None, raw=False, **kw):
        calls.append((url, params))
        assert "waterservices" not in url, url
        if "latest-continuous" in url:
            return fix(f"latest_continuous_{params['parameter_code']}.json")
        if url == usgs_water.STATS:
            return fix("observation_normals.json")
        if url.endswith("/cameras"):
            return fix("cameras_comfort.json")
        if "listFiles" in url:
            return fix("listFiles.json")
        raise AssertionError("unexpected URL " + url)
    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(usgs_water, "_cameras", None)
    monkeypatch.delenv("USGS_WATER_API_KEY", raising=False)
    return calls


def test_daily_stats_reads_the_recorded_normals():
    calls = []
    orig = http.get
    try:
        http.get = lambda url, params=None, **kw: calls.append((url, params)) or fix("observation_normals.json")
        st = usgs_water.daily_stats(["08166700", "08167000"], [(10, 28)])
    finally:
        http.get = orig
    (url, params), = calls
    assert url == "https://api.waterdata.usgs.gov/statistics/v0/observationNormals"
    # several sites/parameters are repeated parameters (a comma list matches nothing), only the reading's day is asked for
    assert params["monitoring_location_id"] == ["USGS-08166700", "USGS-08167000"] and params["parameter_code"] == ["00065", "00060"]
    assert params["computation_type"] == "percentile" and params["normal_type"] == "DOY"
    assert params["start_date"] == params["end_date"] == "10-28"
    # percentiles of the daily mean (parent statistic 00003), not of the daily max or min
    assert st[("08167000", "00060")][(10, 28)] == {"p25": 57.7, "p50": 104.0, "p75": 185.0, "count": 87}
    assert st[("08167000", "00065")][(10, 28)] == {"p25": 2.192, "p50": 2.79, "p75": 3.38, "count": 36}
    assert ("08166700", "00065") not in st


def test_live_reading_usual_range_and_photo_from_the_new_api_only(recorded):
    res = irlpls.live("usgs-water", lat=29.9677, lon=-98.905)
    by = {(o.get("site"), o["label"]): o for o in res}
    q = by[("USGS-08167000", "discharge")]
    assert q["value"] == 78.4 and q["unit"] == "ft³/s" and q["daily_median"] == 104.0
    assert q["daily_stats"] == {"p25": 57.7, "p50": 104.0, "p75": 185.0, "count": 87, "date": "10-28", "unit": "ft³/s",
                                "source": "https://api.waterdata.usgs.gov/statistics/v0/observationNormals"}
    assert by[("USGS-08167000", "gage height")]["daily_median"] == 2.79
    photo = by[("USGS-08167000", "river camera")]
    assert photo["url"].endswith("TX_Guadalupe_Rv_at_Comfort___2026-10-29T02-11-21Z.jpg")
    assert [w["code"] for w in res.warnings] == ["daily_stats_missing"]       # 08166700 has no published statistics
    assert all(u.startswith("https://api.waterdata.usgs.gov/") for u, _ in recorded)
    assert sum(1 for u, _ in recorded if u == usgs_water.STATS) == 1         # one statistics call for every site


def test_a_429_is_raised_not_sent_to_the_legacy_service(monkeypatch):
    asked = []

    def get(url, **kw):
        asked.append(url)
        raise http.HTTPError(429, url, "OVER_RATE_LIMIT")
    monkeypatch.setattr(http, "get", get)
    monkeypatch.delenv("USGS_WATER_API_KEY", raising=False)
    with pytest.raises(http.HTTPError):
        usgs_water.USGSWater().live(29.9677, -98.905, images=False)
    assert asked and not any("waterservices" in u for u in asked)


def test_the_key_goes_to_every_call_as_x_api_key(recorded, monkeypatch):
    seen = []
    inner = http.get

    def get(url, params=None, headers=None, **kw):
        seen.append((url, headers))
        return inner(url, params=params, headers=headers, **kw)
    monkeypatch.setattr(http, "get", get)
    monkeypatch.setenv("USGS_WATER_API_KEY", "k-123")
    usgs_water.USGSWater().live(29.9677, -98.905)
    assert seen and all((h or {}).get("X-Api-Key") == "k-123" for _, h in seen)


def test_no_code_or_card_calls_waterservices():
    """Code, cards and the pages call api.waterdata.usgs.gov only (docs may still describe the shutdown)."""
    files = [*ROOT.glob("irlpls/**/*.py"), *ROOT.glob("tools/*.py"), *ROOT.glob("examples/*.py"), *ROOT.glob("cards/*.json"),
             ROOT / "src" / "live.js", ROOT / "src" / "index.html", ROOT / "src" / "pitch" / "flow.html", ROOT / "irlpls" / "data" / "cards.json"]
    hits = [str(f.relative_to(ROOT)) for f in files if f.exists() and "//waterservices.usgs.gov" in f.read_text()]  # a URL, not a mention
    assert not hits, hits


def test_card_states_the_new_endpoints_limits_and_the_shutdown():
    c = json.loads((ROOT / "cards" / "usgs-water.json").read_text())
    paths = [e["path"] for e in c["api"]["endpoints"]]
    assert "/collections/latest-continuous/items" in paths and "/collections/monitoring-locations/items" in paths
    assert "https://api.waterdata.usgs.gov/statistics/v0/observationNormals" in paths
    assert "https://api.waterdata.usgs.gov/nims/v0/cameras" in paths
    assert re.match(r"\d{4}-\d{2}-\d{2}: ", c["api"]["notes"]) and "2027-02-22" in c["api"]["notes"]
    rl = c["access"]["rate_limit"]
    assert rl["source_url"] == "https://api.waterdata.usgs.gov/docs/ogcapi/keys/" and "requests" not in rl  # USGS states no number
    assert any(s["url"] == "https://waterdata.usgs.gov/blog/wdfn-waterservices-degradation/" for s in c["sources"])
    assert {a["type"] for a in c["api"]["auth"]} >= {"none", "api_key_header"} and c["access"]["key_required"] is False


def test_revisit_list_has_the_waterservices_check():
    if not (ROOT / "docs" / "REVISIT.md").exists():
        pytest.skip("docs/REVISIT.md is internal (left out of the public export)")
    rows = (ROOT / "docs" / "REVISIT.md").read_text()
    assert re.search(r"^\| 2027-01-01 \| open \| USGS WaterServices: confirm WaterServices fully unused.*throttling begins", rows, re.M)


def test_browser_route_uses_the_ogc_api():
    js = (ROOT / "src" / "live.js").read_text()
    route = js[js.index("'usgs-water':{"):js.index(" 'nws':{")]
    assert "O='https://api.waterdata.usgs.gov/ogcapi/v1/collections/'" in route and "O+'latest-continuous/items?f=json&parameter_code=00060,00065" in route
    assert "O+'monitoring-locations/items/USGS-'" in route and "usgsname." in route  # the name, asked once per browser
