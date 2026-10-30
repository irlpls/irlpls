"""A3 adapters (cameras + air): tfl-jamcams, wsdot-cameras, airnow, empiric-earth. Offline: irlpls.http.get/post/head
are monkeypatched with recorded (TfL, WSDOT, AirNow; 2026-10-28) or documented-shape (Empiric) fixtures."""
import json
import pathlib
import urllib.parse

import pytest

import irlpls
import irlpls.adapters
from irlpls import catalog, http
from irlpls.adapters import _a3, airnow as airnow_mod
from irlpls.adapters.base import MissingCredentials

FIX = pathlib.Path(__file__).parent / "fixtures" / "adapters"
SECRET = "sekrit-value-123"
LONDON = (51.5074, -0.1278)
SEATTLE = (47.6062, -122.3321)
PORTLAND = (45.52, -122.68)


def fx(card, name):
    p = FIX / card / name
    return json.loads(p.read_text()) if name.endswith(".json") else p.read_text()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("network call not stubbed")
    for f in ("get", "post", "head", "request"):
        monkeypatch.setattr(http, f, boom)
    airnow_mod._cache.clear()
    yield
    airnow_mod._cache.clear()


def heads(lm="Wed, 28 Oct 2026 22:56:18 GMT", seen=None):
    def head(url, **kw):
        if seen is not None:
            seen.append(url)
        return {"last-modified": lm, "content-type": "image/jpeg"} if lm else {"content-type": "image/jpeg"}
    return head


def err(status, retry_after=None):
    def raise_(url, *a, **kw):
        h = {"Retry-After": str(retry_after)} if retry_after is not None else {}
        raise http.HTTPError(status, url + f"?app_key={SECRET}", f"bad key {SECRET}", headers=h)
    return raise_


# ---- registry and cards

@pytest.mark.parametrize("cid", ["tfl-jamcams", "wsdot-cameras", "airnow", "empiric-earth"])
def test_registered_live_only_and_matches_card(cid):
    a = irlpls.adapters.get(cid)
    assert a and a.can_live and not a.can_commission
    assert "live" in irlpls.card(cid)["delivery"]["modes"]
    assert cid in catalog.adapters()
    assert a.prepare({"lat": 1.0, "lon": 2.0}, "commission") is None
    assert a.prepare({"need": "x"}, "live") is None          # no coordinates, no call
    assert a.map_status("delivered") is None                 # reads only: no orders, so no statuses to map


def test_keyless_adapters_are_configured_and_empiric_needs_a_token(monkeypatch):
    monkeypatch.delenv("NEXAR_ACCESS_TOKEN", raising=False)
    for cid in ("tfl-jamcams", "wsdot-cameras", "airnow"):
        assert irlpls.adapters.get(cid).configured()
    assert not irlpls.adapters.get("empiric-earth").configured()


# ---- TfL JamCams

def test_tfl_prepare_is_keyless_read():
    pc = irlpls.adapters.get("tfl-jamcams").prepare({"lat": LONDON[0], "lon": LONDON[1]}, "live")
    assert pc["operation"] == "read" and pc["method"] == "GET" and pc["auth"] == {"kind": "none"}
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(pc["url"]).query)
    assert pc["url"].startswith("https://api.tfl.gov.uk/Place?") and q["type"] == ["JamCam"] and q["radius"] == ["1000"]
    assert pc["safe_to_run_without_approval"] is True and "{TFL_APP_KEY}" in pc["notes"]


def test_tfl_live_parses_places_and_uses_last_modified(monkeypatch):
    calls, seen = [], []
    monkeypatch.setattr(http, "get", lambda url, params=None, **kw: calls.append((url, params)) or fx("tfl-jamcams", "place_near.json"))
    monkeypatch.setattr(http, "head", heads(seen=seen))
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    res = irlpls.adapters.get("tfl-jamcams").live(*LONDON, radius_km=0.5, limit=2)
    assert calls[0][0] == "https://api.tfl.gov.uk/Place" and calls[0][1]["radius"] == 500 and calls[0][1]["app_key"] is None
    assert len(res) == 2 and res.warnings == []
    o = res[0]
    assert o["provider"] == "tfl-jamcams" and o["kind"] == "photo" and o["camera"] == "Trafalgar Square"
    assert o["phenomenon_time"] == "2026-10-28T22:56:18Z"
    assert o["url"] == "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00001.06502.jpg" and o["video"].endswith(".mp4")
    assert o["lat"] == 51.5074 and o["distance_km"] == 0.06
    assert seen == [r["url"] for r in res]


def test_tfl_skips_unavailable_and_missing_time_is_none_not_made_up(monkeypatch):
    data = fx("tfl-jamcams", "place_near.json")
    for a in data["places"][0]["additionalProperties"]:
        if a["key"] == "available":
            a["value"] = "false"
    monkeypatch.setattr(http, "get", lambda *a, **kw: data)
    monkeypatch.setattr(http, "head", heads(lm=None))
    res = irlpls.adapters.get("tfl-jamcams").live(*LONDON, limit=5)
    assert [o["camera"] for o in res] == ["Northumberland Ave/Victoria Emb", "Charing Cross Rd/Cranbourne St"]
    assert all(o["phenomenon_time"] is None for o in res)
    assert {w["code"] for w in res.warnings} == {"no_capture_time"}


def test_tfl_head_error_keeps_the_photo_without_a_time(monkeypatch):
    monkeypatch.setattr(http, "get", lambda *a, **kw: fx("tfl-jamcams", "place_near.json"))
    monkeypatch.setattr(http, "head", err(404))
    res = irlpls.adapters.get("tfl-jamcams").live(*LONDON, limit=1)
    assert res[0]["phenomenon_time"] is None and "HTTP 404" in res.warnings[0]["msg"]


def test_tfl_key_is_sent_but_never_shown(monkeypatch):
    sent = {}
    monkeypatch.setenv("TFL_APP_KEY", SECRET)
    monkeypatch.setattr(http, "get", lambda url, params=None, **kw: sent.update(params) or fx("tfl-jamcams", "place_near.json"))
    monkeypatch.setattr(http, "head", heads())
    a = irlpls.adapters.get("tfl-jamcams")
    res = a.live(*LONDON)
    assert sent["app_key"] == SECRET
    assert SECRET not in repr(list(res)) + repr(res.warnings)
    assert SECRET not in repr(a.prepare({"lat": LONDON[0], "lon": LONDON[1]}, "live"))
    # a refused key becomes MissingCredentials, without echoing the key or the URL
    monkeypatch.setattr(http, "get", err(403))
    with pytest.raises(MissingCredentials) as e:
        a.live(*LONDON)
    assert SECRET not in str(e.value) and "TFL_APP_KEY" in str(e.value)


@pytest.mark.parametrize("status,code", [(401, "refused"), (403, "refused"), (404, "not_found"), (500, "provider_error")])
def test_tfl_keyless_errors_become_warnings(monkeypatch, status, code):
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    monkeypatch.setattr(http, "get", err(status))
    res = irlpls.adapters.get("tfl-jamcams").live(*LONDON)
    assert list(res) == [] and res.warnings[0]["code"] == code and res.warnings[0]["status"] == status
    assert SECRET not in repr(res.warnings)


def test_tfl_429_reports_retry_after(monkeypatch):
    monkeypatch.delenv("TFL_APP_KEY", raising=False)
    monkeypatch.setattr(http, "get", err(429, retry_after=30))
    res = irlpls.adapters.get("tfl-jamcams").live(*LONDON)
    w = res.warnings[0]
    assert w["code"] == "rate_limited" and w["retry_after_s"] == 30.0 and "retry after 30s" in w["msg"]
    assert SECRET not in repr(w)


# ---- WSDOT cameras

def test_wsdot_prepare_is_keyless_arcgis_query():
    pc = irlpls.adapters.get("wsdot-cameras").prepare({"lat": SEATTLE[0], "lon": SEATTLE[1]}, "live")
    u = urllib.parse.urlsplit(pc["url"])
    q = urllib.parse.parse_qs(u.query)
    assert u.netloc == "data.wsdot.wa.gov" and u.path.endswith("/FeatureServer/0/query")
    assert q["geometry"] == ["-122.33210,47.60620"] and q["inSR"] == ["4326"] and q["outSR"] == ["4326"] and q["f"] == ["json"]
    assert q["distance"] == ["5000"] and q["units"] == ["esriSRUnit_Meter"]
    assert pc["auth"] == {"kind": "none"} and pc["safe_to_run_without_approval"] and "{WSDOT_ACCESS_CODE}" in pc["notes"]


def test_wsdot_live_sorts_by_distance_and_times_from_head(monkeypatch):
    got = {}
    monkeypatch.setattr(http, "get", lambda url, params=None, **kw: got.update(params) or fx("wsdot-cameras", "arcgis_near.json"))
    monkeypatch.setattr(http, "head", heads("Wed, 28 Oct 2026 22:56:19 GMT"))
    res = irlpls.adapters.get("wsdot-cameras").live(*SEATTLE, radius_km=2, limit=2)
    assert got["distance"] == 2000 and got["outSR"] == 4326
    assert [o["camera"] for o in res] == ["I-5 at MP 165: Yesler Way", "I-5 at MP 166: Pine St"]
    o = res[0]
    assert o["kind"] == "photo" and o["phenomenon_time"] == "2026-10-28T22:56:19Z" and o["direction"] == "S"
    assert o["url"] == "https://images.wsdot.wa.gov/nw/005vc16511.jpg" and o["camera_id"] == 1028
    assert abs(o["lat"] - 47.6017) < 1e-4 and o["distance_km"] < 1


def test_wsdot_arcgis_error_body_and_http_errors(monkeypatch):
    monkeypatch.setattr(http, "get", lambda *a, **kw: {"error": {"code": 400, "message": "Invalid query"}})
    res = irlpls.adapters.get("wsdot-cameras").live(*SEATTLE)
    assert list(res) == [] and res.warnings[0]["code"] == "provider_error"
    monkeypatch.setattr(http, "get", err(429, retry_after=12))
    res = irlpls.adapters.get("wsdot-cameras").live(*SEATTLE)
    assert res.warnings[0]["code"] == "rate_limited" and res.warnings[0]["retry_after_s"] == 12.0
    monkeypatch.setattr(http, "get", err(404))
    assert irlpls.adapters.get("wsdot-cameras").live(*SEATTLE).warnings[0]["code"] == "not_found"


# ---- AirNow

def test_airnow_prepare_names_the_keyless_file_and_the_keyed_api():
    pc = irlpls.adapters.get("airnow").prepare({"lat": PORTLAND[0], "lon": PORTLAND[1]}, "live")
    assert pc["url"] == "https://files.airnowtech.org/airnow/today/reportingarea.dat" and pc["auth"] == {"kind": "none"}
    assert pc["safe_to_run_without_approval"] and "{AIRNOW_API_KEY}" in pc["notes"] and "45.5200" in pc["notes"]


def test_airnow_parse_keeps_only_current_observations():
    rows = airnow_mod.parse(fx("airnow", "reportingarea.dat"))
    assert {r["area"] for r in rows} == {"Aberdeen", "Anchorage", "Baku", "Portland", "Seattle-Bellevue-Kent Valley"}
    assert len(rows) == 10 and all(isinstance(r["aqi"], int) for r in rows)


def test_airnow_time_zones_convert_only_when_known():
    rows = {(r["area"], r["parameter"]): r for r in airnow_mod.parse(fx("airnow", "reportingarea.dat"))}
    assert airnow_mod.utc_time(rows[("Portland", "OZONE")]) == "2026-10-28T22:00:00Z"        # 15:00 PDT
    assert airnow_mod.utc_time(rows[("Aberdeen", "PM10")]) == "2026-10-28T22:00:00Z"         # 17:00 CDT
    assert airnow_mod.utc_time(rows[("Anchorage", "PM2.5")]) == "2026-10-28T22:00:00Z"       # 14:00 ADT in Alaska
    assert airnow_mod.utc_time(rows[("Baku", "PM2.5")]) is None                               # AZT: not guessed
    assert airnow_mod.utc_time(dict(rows[("Anchorage", "PM2.5")], state="NS")) is None        # ADT outside Alaska


def test_airnow_live_nearest_area_and_cache(monkeypatch):
    n = []
    monkeypatch.setattr(http, "get", lambda url, raw=False, **kw: n.append(url) or fx("airnow", "reportingarea.dat"))
    a = irlpls.adapters.get("airnow")
    res = a.live(*PORTLAND)
    assert n == ["https://files.airnowtech.org/airnow/today/reportingarea.dat"]
    assert {o["label"] for o in res} == {"OZONE AQI", "PM2.5 AQI"}
    oz = next(o for o in res if o["parameter"] == "OZONE")
    assert oz["value"] == 28 and oz["unit"] == "AQI" and oz["category"] == "Good" and oz["primary"] is True
    assert oz["reporting_area"] == "Portland" and oz["phenomenon_time"] == "2026-10-28T22:00:00Z" and oz["kind"] == "sensor"
    assert len(a.live(*SEATTLE, radius_km=300, limit=2)) == 5           # Seattle (3) + Portland (2), from the cache
    assert len(n) == 1
    assert list(a.live(0.0, 0.0)) == []                  # nothing within 50 km


def test_airnow_errors(monkeypatch):
    monkeypatch.setattr(http, "get", err(429, retry_after=60))
    res = irlpls.adapters.get("airnow").live(*PORTLAND)
    assert list(res) == [] and res.warnings[0]["code"] == "rate_limited" and res.warnings[0]["retry_after_s"] == 60.0
    monkeypatch.setattr(http, "get", err(404))
    assert irlpls.adapters.get("airnow").live(*PORTLAND).warnings[0]["code"] == "not_found"


# ---- Empiric Earth (fixture: documented-shape)

def test_empiric_prepare_is_a_bearer_post_without_the_token(monkeypatch):
    monkeypatch.setenv("NEXAR_ACCESS_TOKEN", SECRET)
    pc = irlpls.adapters.get("empiric-earth").prepare({"lat": 40.7336, "lon": -73.9858}, "live", now_ms=1793192400000)
    assert pc["method"] == "POST" and pc["url"] == "https://external.getnexar.com/api/virtualcam/v5/frames"
    assert pc["headers"]["Authorization"] == "Bearer {NEXAR_ACCESS_TOKEN}"
    assert pc["auth"]["kind"] == "bearer" and pc["auth"]["key_name"] == "NEXAR_ACCESS_TOKEN"
    b = pc["body"]
    assert b["sort_by"] == "TIMESTAMP" and b["filters"]["end_time"] == "1793192400000"
    assert b["filters"]["start_time"] == str(1793192400000 - 72 * 3600 * 1000)
    bb = b["bounding_box"]
    assert bb["south_west"]["latitude"] < 40.7336 < bb["north_east"]["latitude"]
    assert bb["south_west"]["longitude"] < -73.9858 < bb["north_east"]["longitude"]
    assert "refresh-token" in pc["notes"] and "/api/virtualcam/v4/coverage" in pc["notes"]
    assert SECRET not in repr(pc)


def test_empiric_live_needs_the_token(monkeypatch):
    monkeypatch.delenv("NEXAR_ACCESS_TOKEN", raising=False)
    with pytest.raises(MissingCredentials, match="NEXAR_ACCESS_TOKEN"):
        irlpls.adapters.get("empiric-earth").live(40.7336, -73.9858)


def test_empiric_live_parses_frames_newest_first(monkeypatch):
    monkeypatch.setenv("NEXAR_ACCESS_TOKEN", SECRET)
    sent = {}

    def post(url, body=None, headers=None, **kw):
        sent.update(url=url, body=body, headers=headers)
        return fx("empiric-earth", "frames.json")
    monkeypatch.setattr(http, "post", post)
    res = irlpls.adapters.get("empiric-earth").live(40.7336, -73.9858, limit=5)
    assert sent["headers"]["Authorization"] == "Bearer " + SECRET and sent["body"]["sort_by"] == "TIMESTAMP"
    assert [o["frame_id"] for o in res] == ["a1b2c3d4e5f60718293a4b5c6d7e8f90", "56f8d643be2ff1e674598eccfae2b3e2"]
    o = res[1]
    assert o["kind"] == "photo" and o["phenomenon_time"] == "2026-10-28T12:00:00Z"
    assert o["lat"] == 40.733587 and o["lon"] == -73.985751 and o["road_type"] == "RESIDENTIAL" and o["light"] == "NIGHTTIME"
    assert SECRET not in repr(list(res))


def test_empiric_error_mapping(monkeypatch):
    monkeypatch.setenv("NEXAR_ACCESS_TOKEN", SECRET)
    a = irlpls.adapters.get("empiric-earth")
    monkeypatch.setattr(http, "post", err(401))
    with pytest.raises(MissingCredentials) as e:
        a.live(40.7, -73.9)
    assert SECRET not in str(e.value)
    monkeypatch.setattr(http, "post", err(403))   # area not authorized / not in the product group
    res = a.live(40.7, -73.9)
    assert res.warnings[0]["code"] == "refused" and SECRET not in repr(res.warnings)
    monkeypatch.setattr(http, "post", err(404))
    assert a.live(40.7, -73.9).warnings[0]["code"] == "not_found"
    monkeypatch.setattr(http, "post", err(429, retry_after=5))
    w = a.live(40.7, -73.9).warnings[0]
    assert w["code"] == "rate_limited" and w["retry_after_s"] == 5.0


# ---- helper

def test_last_modified_reads_http_dates_and_never_invents(monkeypatch):
    monkeypatch.setattr(http, "head", heads("Wed, 28 Oct 2026 23:56:18 +0100"))
    assert _a3.last_modified("https://x/cam.jpg") == ("2026-10-28T22:56:18Z", None)
    monkeypatch.setattr(http, "head", heads("not a date"))
    assert _a3.last_modified("https://x/cam.jpg")[0] is None
    monkeypatch.setattr(http, "head", heads(None))
    assert _a3.last_modified("https://x/cam.jpg") == (None, "no Last-Modified header")


def test_client_side_rate_limit_is_reported_as_rate_limited():
    from irlpls.ratelimit import RateLimited
    w = _a3.http_warning("tfl-jamcams", RateLimited("tfl-jamcams", "https://x", 42.0, "50 per 60 s"), "cameras")
    assert w["code"] == "rate_limited" and w["client_side"] is True
