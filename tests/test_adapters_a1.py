"""Batch A1 (seismic + fire): usgs-earthquakes, emsc-seismicportal, geonet-nz, nasa-firms. Offline only: irlpls.http.get
is monkeypatched with responses recorded live on 2026-10-28 (tests/fixtures/adapters/<card>/SOURCE) or, for the keyed
FIRMS area API, a hand-made documented-shape fixture."""
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from irlpls import adapters, catalog, http
from irlpls.adapters import _fdsn
from irlpls.adapters.base import Results

FIX = Path(__file__).parent / "fixtures" / "adapters"
NOW = datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)
FAKE_KEY = "sekr3t-firms-key-123"
IDS = ("usgs-earthquakes", "emsc-seismicportal", "geonet-nz", "nasa-firms")


def fx(card, name):
    t = (FIX / card / name).read_text()
    return json.loads(t) if name.endswith((".json", ".geojson")) else t


class Recorder:
    """Stands in for http.get: answers by URL substring, records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, params=None, headers=None, raw=False, **kw):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}), "raw": raw})
        for frag, resp in self.routes.items():
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)


def test_all_four_are_registered_keyless_live_adapters():
    cat = catalog.adapters()
    for cid in IDS:
        a = adapters.get(cid)
        assert a is not None and a.can_live and not a.can_commission, cid
        assert a.env is None and a.configured(), cid             # FIRMS works keyless; its key is optional
        assert cat[cid]["live"] and cat[cid]["env"] is None, cid
        card = json.loads((Path(__file__).parents[1] / "cards" / f"{cid}.json").read_text())
        assert card["adapter"] == type(a).__module__.rsplit(".", 1)[1]
        assert card["delivery"]["modes"] == ["live"]


# ---- prepare(): the ready call, offline ------------------------------------------------------------------------------

def _q(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_usgs_prepare_bounds_time_radius_and_limit():
    pc = adapters.get("usgs-earthquakes").prepare({"lat": 35.77, "lon": -117.6}, "live", now=NOW)
    assert pc["method"] == "GET" and pc["url"].startswith("https://earthquake.usgs.gov/fdsnws/event/1/query?")
    q = _q(pc["url"])
    assert q == {"format": "geojson", "latitude": "35.7700", "longitude": "-117.6000", "starttime": "2026-10-21T23:00:00",
                 "orderby": "time", "limit": "10", "maxradiuskm": "300"}
    assert pc["auth"] == {"kind": "none"} and pc["safe_to_run_without_approval"] is True and pc["operation"] == "read"
    assert pc["docs_url"] == "https://earthquake.usgs.gov/fdsnws/event/1/"


def test_emsc_prepare_sends_the_radius_in_degrees():
    pc = adapters.get("emsc-seismicportal").prepare({"lat": 38.0, "lon": 23.7}, "live", radius_km=333.585, now=NOW)
    q = _q(pc["url"])
    assert pc["url"].startswith("https://www.seismicportal.eu/fdsnws/event/1/query?")
    assert q["format"] == "json" and q["maxradius"] == "3.0" and "maxradiuskm" not in q
    assert q["starttime"] == "2026-10-21T23:00:00" and pc["safe_to_run_without_approval"] is True


def test_geonet_prepare_names_the_version_header():
    pc = adapters.get("geonet-nz").prepare({"lat": -41.29, "lon": 174.78}, "live")
    assert pc["url"] == "https://api.geonet.org.nz/quake?MMI=3"
    assert pc["headers"] == {"Accept": "application/vnd.geo+json;version=2"}
    assert "-41.2900, 174.7800" in pc["notes"] and pc["auth"] == {"kind": "none"}


def test_firms_prepare_keyless_file_without_a_key(no_key):
    pc = adapters.get("nasa-firms").prepare({"lat": -8.5, "lon": 121.0}, "live")
    assert pc["url"] == "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv"
    assert pc["auth"] == {"kind": "none"} and pc["safe_to_run_without_approval"] is True
    assert "/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/" in pc["notes"]


def test_firms_prepare_area_call_names_the_key_never_fills_it(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", FAKE_KEY)
    pc = adapters.get("nasa-firms").prepare({"lat": -8.5, "lon": 121.0}, "live", radius_km=50)
    assert pc["url"].startswith("https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_MAP_KEY}/VIIRS_NOAA20_NRT/")
    assert pc["url"].endswith("/1")
    w, s, e, n = (float(v) for v in pc["url"].split("/")[-2].split(","))
    assert w < 121.0 < e and s < -8.5 < n
    assert pc["auth"]["kind"] == "other" and pc["auth"]["key_name"] == "FIRMS_MAP_KEY"
    assert pc["auth"]["placeholder"] == "{FIRMS_MAP_KEY}" and pc["auth"]["get_key_url"].endswith("/api/map_key/")
    assert FAKE_KEY not in json.dumps(pc)


@pytest.mark.parametrize("cid", IDS)
def test_prepare_needs_coordinates_and_the_live_route(cid):
    a = adapters.get(cid)
    assert a.prepare({"need": "earthquakes near Wellington"}, "live") is None
    assert a.prepare({"lat": 1.0, "lon": 2.0}, "commission") is None


# ---- live(): parsing recorded responses ------------------------------------------------------------------------------

def test_usgs_live_parses_the_recorded_query(monkeypatch):
    rec = Recorder({"earthquake.usgs.gov": fx("usgs-earthquakes", "query.geojson")})
    monkeypatch.setattr(http, "get", rec)
    out = adapters.get("usgs-earthquakes").live(35.77, -117.6, radius_km=100, limit=3)
    assert isinstance(out, Results) and len(out) == 3 and out.warnings == []
    o = out[0]
    assert o["phenomenon_time"] == "2026-10-28T15:12:26.370Z" and o["result_time"] == "2026-10-28T17:30:19.775Z"
    assert (o["lat"], o["lon"], o["value"], o["unit"]) == (35.7845, -117.643333333333, 0.74, "ml")
    assert o["label"] == "M 0.7 - 18 km N of Ridgecrest, CA" and o["review_status"] == "automatic"
    assert o["url"] == "https://earthquake.usgs.gov/earthquakes/eventpage/ci41568216" and o["depth_km"] == 10.33
    assert o["distance_km"] < 5
    p = rec.calls[0]["params"]
    assert p["maxradiuskm"] == 100 and p["limit"] == 3 and p["format"] == "geojson" and "starttime" in p


def test_usgs_live_empty_is_empty(monkeypatch):
    monkeypatch.setattr(http, "get", Recorder({"usgs": fx("usgs-earthquakes", "query_empty.geojson")}))
    assert list(adapters.get("usgs-earthquakes").live(0, -30, radius_km=5)) == []


def test_emsc_live_parses_the_recorded_query(monkeypatch):
    rec = Recorder({"seismicportal.eu": fx("emsc-seismicportal", "query.json")})
    monkeypatch.setattr(http, "get", rec)
    out = adapters.get("emsc-seismicportal").live(38.0, 23.7, radius_km=333.585, limit=3)
    assert len(out) == 3
    o = out[0]
    assert o["phenomenon_time"] == "2026-10-28T22:00:42.92Z" and o["result_time"] == "2026-10-28T22:55:28.398154Z"
    assert (o["lat"], o["lon"], o["value"], o["unit"], o["depth_km"]) == (37.8528, 26.751, 1.4, "ml", 4.3)
    assert o["label"] == "M 1.4 - DODECANESE ISLANDS, GREECE"
    assert o["url"] == "https://www.seismicportal.eu/eventdetails.html?unid=20261028_0000342"
    assert rec.calls[0]["params"]["maxradius"] == 3.0


def test_emsc_live_204_no_content_is_empty(monkeypatch):
    monkeypatch.setattr(http, "get", Recorder({"seismicportal": None}))   # http.request returns None for an empty 204
    assert list(adapters.get("emsc-seismicportal").live(0, -30, radius_km=10)) == []


def test_geonet_live_filters_by_distance_client_side(monkeypatch):
    rec = Recorder({"/quake": fx("geonet-nz", "quake_mmi3.json"), "/volcano/val": fx("geonet-nz", "volcano_val.json")})
    monkeypatch.setattr(http, "get", rec)
    a = adapters.get("geonet-nz")
    out = a.live(-41.29, 174.78, radius_km=150, limit=10)   # Wellington: Blenheim and Whanganui are in range
    assert [o["place"] for o in out] == ["20 km south-west of Blenheim", "30 km south of Whanganui"]
    assert all(o["distance_km"] <= 150 for o in out)
    o = out[0]
    assert o["phenomenon_time"] == "2026-10-26T06:24:29.162Z" and o["unit"] == "magnitude" and o["mmi"] == 5
    assert o["url"].startswith("https://www.geonet.org.nz/earthquake/2026p") and o["lat"] < 0 < o["lon"]
    assert rec.calls[0]["headers"]["Accept"] == "application/vnd.geo+json;version=2" and rec.calls[0]["params"] == {"MMI": 3}
    assert len(rec.calls) == 1                                   # no volcano call unless asked


def test_geonet_live_volcano_alert_levels_have_no_made_up_time(monkeypatch):
    monkeypatch.setattr(http, "get", Recorder({"/quake": fx("geonet-nz", "quake_mmi3.json"), "/volcano/val": fx("geonet-nz", "volcano_val.json")}))
    out = adapters.get("geonet-nz").live(-39.2, 175.6, radius_km=60, limit=5, volcanoes=True)
    vol = [o for o in out if o["unit"] == "volcanic alert level"]
    assert vol and all(o["phenomenon_time"] is None for o in vol)
    assert {o["volcano_id"] for o in vol} >= {"tongariro"} and vol[0]["label"] == "Ngauruhoe: No volcanic unrest."
    assert [o["distance_km"] for o in vol] == sorted(o["distance_km"] for o in vol)


def test_firms_live_keyless_filters_the_global_file(monkeypatch, no_key):
    rec = Recorder({"MODIS_C6_1_Global_24h.csv": fx("nasa-firms", "MODIS_C6_1_Global_24h.csv")})
    monkeypatch.setattr(http, "get", rec)
    out = adapters.get("nasa-firms").live(-8.5, 121.0, radius_km=50, limit=10)
    assert len(out) == 3 and out.warnings == []
    assert [o["phenomenon_time"] for o in out] == ["2026-10-27T07:39:00Z", "2026-10-27T07:39:00Z", "2026-10-27T00:05:00Z"]
    o = out[0]
    assert (o["value"], o["unit"], o["confidence"], o["satellite"]) == (8.16, "MW", 69, "A")
    assert o["label"] == "active fire, radiative power (MODIS Aqua)" and o["distance_km"] == 6.2
    assert out[2]["label"].endswith("(MODIS Terra)")
    assert rec.calls[0]["raw"] is True and "/api/" not in rec.calls[0]["url"]


def test_firms_live_with_key_uses_the_area_api_and_hides_the_key(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", FAKE_KEY)
    rec = Recorder({"/api/area/csv/": fx("nasa-firms", "area_VIIRS_NOAA20_NRT.csv")})
    monkeypatch.setattr(http, "get", rec)
    out = adapters.get("nasa-firms").live(-8.5, 121.0, radius_km=50)
    assert rec.calls[0]["url"].startswith(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FAKE_KEY}/VIIRS_NOAA20_NRT/")
    assert len(out) == 2 and out[0]["confidence"] == "nominal" and out[1]["confidence"] == "high"
    assert out[0]["label"] == "active fire, radiative power (VIIRS NOAA-20)" and out[0]["phenomenon_time"] == "2026-10-28T05:12:00Z"
    assert FAKE_KEY not in json.dumps(list(out))


def test_firms_rejects_an_unknown_source(no_key):
    with pytest.raises(ValueError):
        adapters.get("nasa-firms").live(0, 0, source="../../etc")


# ---- errors ---------------------------------------------------------------------------------------------------------

def _err(status, body="", headers=None, url="https://x.invalid/q"):
    return http.HTTPError(status, url, body, headers=headers)


@pytest.mark.parametrize("cid,frag", [("usgs-earthquakes", "usgs.gov"), ("emsc-seismicportal", "seismicportal"),
                                      ("geonet-nz", "geonet"), ("nasa-firms", "firms")])
@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"), (404, "not_found"), (429, "rate_limited")])
def test_documented_errors_become_warnings(monkeypatch, no_key, cid, frag, status, code):
    hdr = {"Retry-After": "120"} if status == 429 else None
    monkeypatch.setattr(http, "get", Recorder({frag: _err(status, "nope", hdr)}))
    out = adapters.get(cid).live(-41.29, 174.78, radius_km=50)
    assert list(out) == [] and len(out.warnings) == 1
    w = out.warnings[0]
    assert w["code"] == code and w["status"] == status and w["provider"] == cid
    if status == 429:
        assert w["retry_after_s"] == 120 and "retry after 120 s" in w["msg"]


def test_rate_limit_uses_the_pacers_retry_after_when_set(monkeypatch):
    e = _err(429)
    e.retry_after = 7.2
    monkeypatch.setattr(http, "get", Recorder({"usgs": e}))
    assert adapters.get("usgs-earthquakes").live(0, 0).warnings[0]["retry_after_s"] == 8


def test_usgs_too_many_events_400_is_a_warning(monkeypatch):
    monkeypatch.setattr(http, "get", Recorder({"usgs": _err(400, "Error 400: Bad Request\n\n20001 matching events exceeds search limit of 20000.")}))
    w = adapters.get("usgs-earthquakes").live(0, 0, radius_km=20000).warnings[0]
    assert w["code"] == "bad_request" and "exceeds search limit" in w["msg"]


def test_unmapped_errors_are_raised(monkeypatch):
    monkeypatch.setattr(http, "get", Recorder({"usgs": _err(500, "boom")}))
    with pytest.raises(http.HTTPError):
        adapters.get("usgs-earthquakes").live(0, 0)


def test_firms_invalid_key_is_unauthorized_and_the_key_never_leaks(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", FAKE_KEY)
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FAKE_KEY}/VIIRS_NOAA20_NRT/1,2,3,4/1"
    monkeypatch.setattr(http, "get", Recorder({"/api/area/": _err(400, f"Invalid MAP_KEY. {FAKE_KEY}", url=url)}))
    out = adapters.get("nasa-firms").live(-8.5, 121.0)
    assert out.warnings[0]["code"] == "unauthorized" and FAKE_KEY not in json.dumps(out.warnings)


def test_firms_unmapped_error_is_raised_with_the_key_scrubbed(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", FAKE_KEY)
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FAKE_KEY}/VIIRS_NOAA20_NRT/1,2,3,4/1"
    monkeypatch.setattr(http, "get", Recorder({"/api/area/": _err(500, f"oops {FAKE_KEY}", url=url)}))
    with pytest.raises(http.HTTPError) as ei:
        adapters.get("nasa-firms").live(-8.5, 121.0)
    assert FAKE_KEY not in str(ei.value) and FAKE_KEY not in ei.value.url and FAKE_KEY not in ei.value.body


def test_firms_non_csv_200_is_reported_not_parsed(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", FAKE_KEY)
    monkeypatch.setattr(http, "get", Recorder({"/api/area/": f"Invalid area coordinates for {FAKE_KEY}"}))
    out = adapters.get("nasa-firms").live(-8.5, 121.0)
    assert list(out) == [] and out.warnings[0]["code"] == "unexpected_response"
    assert FAKE_KEY not in json.dumps(out.warnings)


# ---- helper ---------------------------------------------------------------------------------------------------------

def test_fdsn_helpers():
    assert _fdsn.iso_ms(1793200346370) == "2026-10-28T15:12:26.370Z" and _fdsn.iso_ms(None) is None
    assert _fdsn.starttime(7, NOW) == "2026-10-21T23:00:00"
    p = _fdsn.query_params(1, 2, 50000, 99999, 1, "json", radius_in_km=False, now=NOW)
    assert p["maxradius"] == 180.0 and p["limit"] == 20000
    assert _fdsn.features({"features": [{"geometry": None}, {"geometry": {"coordinates": [1, 2]}}]}) == [{"geometry": {"coordinates": [1, 2]}}]
    assert _fdsn.features(None) == []
