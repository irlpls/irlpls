"""Batch A2 (water + weather): ndbc-buoys, hubeau-hydrometrie, digitraffic-marine-ais, fmi-open-data.
Offline: irlpls.http is monkeypatched with responses recorded from the public endpoints on 2026-10-28
(tests/fixtures/adapters/<card-id>/SOURCE)."""
import json
import pathlib
import urllib.parse

import pytest

import irlpls
import irlpls.adapters as A
from irlpls import catalog, http
from irlpls.adapters import digitraffic_marine_ais as dt, fmi_open_data as fmi, hubeau_hydrometrie as hub, ndbc_buoys as ndbc
from irlpls.adapters.base import NotSupported

FIX = pathlib.Path(__file__).parent / "fixtures" / "adapters"
IDS = ("ndbc-buoys", "hubeau-hydrometrie", "digitraffic-marine-ais", "fmi-open-data")
POINT = {"ndbc-buoys": (36.785, -122.4), "hubeau-hydrometrie": (48.85, 2.35), "digitraffic-marine-ais": (60.15, 24.95),
         "fmi-open-data": (60.17, 24.94)}


def text(cid, name):
    return (FIX / cid / name).read_text()


def js(cid, name):
    return json.loads(text(cid, name))


def err(status, retry_after=None, body=""):
    return http.HTTPError(status, "https://example.invalid/x", body, headers={"Retry-After": str(retry_after)} if retry_after else None)


@pytest.fixture
def net(monkeypatch):
    """Route http.get by URL to a handler; any other HTTP call fails the test."""
    calls, routes = [], {}

    def get(url, **kw):
        calls.append((url, kw))
        for prefix, h in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                r = h(url, **kw) if callable(h) else h
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"unexpected GET {url}")

    def no(*a, **kw):
        raise AssertionError("no other HTTP in these tests")

    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(http, "post", no)
    monkeypatch.setattr(http, "head", no)
    monkeypatch.setattr(http, "request", no)
    return routes, calls


def ndbc_routes(routes, cams=None):
    routes[ndbc.LATEST] = text("ndbc-buoys", "latest_obs.txt")
    routes[ndbc.CAMS] = js("ndbc-buoys", "buoycams.json") if cams is None else cams


def hub_routes(routes):
    routes[f"{hub.BASE}/observations_tr"] = js("hubeau-hydrometrie", "observations_tr.json")
    routes[f"{hub.BASE}/referentiel/stations"] = js("hubeau-hydrometrie", "stations.json")
    routes[f"{hub.BASE}/referentiel/sites"] = js("hubeau-hydrometrie", "sites.json")


def dt_routes(routes, vessel=None):
    meta = js("digitraffic-marine-ais", "vessels.json")
    routes[f"{dt.BASE}/locations"] = js("digitraffic-marine-ais", "locations.json")

    def one(url, **kw):
        m = url.rsplit("/", 1)[1]
        return meta[m] if m in meta else err(404)
    routes[f"{dt.BASE}/vessels/"] = vessel or one


def fmi_routes(routes):
    routes[fmi.WFS] = text("fmi-open-data", "weather_simple.xml")


# -- registry and contract -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cid", IDS)
def test_registered_live_only_and_matches_card(cid):
    a = A.get(cid)
    assert a is not None and a.card_id == cid
    assert a.can_live and not a.can_commission and a.env is None and a.configured()
    assert "live" in irlpls.card(cid)["delivery"]["modes"]
    assert catalog.adapters()[cid]["live"] is True
    assert irlpls.card(cid)["adapter"] == cid


@pytest.mark.parametrize("cid", IDS)
def test_no_orders_so_no_status(cid):
    a = A.get(cid)
    with pytest.raises(NotSupported):
        a.status({"id": "x"})
    with pytest.raises(NotSupported):
        a.commission({}, confirm=False)
    assert a.map_status("delivered") is None and a.map_status(None) is None and not a.can_cancel


# -- prepare(): the ready call, offline ---------------------------------------------------------------------------------
@pytest.mark.parametrize("cid", IDS)
def test_prepare_read_call_is_keyless_safe_and_offline(cid, net):
    lat, lon = POINT[cid]
    pc = A.get(cid).prepare({"need": "now", "lat": lat, "lon": lon}, "live")
    assert pc["card_id"] == cid and pc["operation"] == "read" and pc["method"] == "GET"
    assert pc["auth"] == {"kind": "none"} and pc["safe_to_run_without_approval"] is True
    assert pc["url"].startswith("https://") and pc["docs_url"].startswith("https://") and pc["notes"]
    assert not any(k.lower() in ("authorization", "x-api-key") for k in pc["headers"])
    assert A.get(cid).prepare({"need": "x", "lat": lat, "lon": lon}, "commission") is None
    assert net[1] == []   # nothing was sent


def test_prepare_urls_carry_the_place():
    q = lambda pc: urllib.parse.parse_qs(urllib.parse.urlsplit(pc["url"]).query)
    h = q(A.get("hubeau-hydrometrie").prepare({"lat": 48.85, "lon": 2.35}, "live"))
    assert h["latitude"] == ["48.85000"] and h["longitude"] == ["2.35000"] and h["distance"] == ["10"] and "date_debut_obs" in h
    d = A.get("digitraffic-marine-ais").prepare({"lat": 60.15, "lon": 24.95}, "live")
    assert q(d)["radius"] == ["10"] and int(q(d)["from"][0]) % 60000 == 0
    assert d["headers"]["Digitraffic-User"] and d["headers"]["Accept-Encoding"] == "gzip"
    f = q(A.get("fmi-open-data").prepare({"lat": 60.17, "lon": 24.94}, "live"))
    assert f["storedquery_id"] == ["fmi::observations::weather::simple"]
    w, s, e, n = (float(x) for x in f["bbox"][0].split(","))
    assert w < 24.94 < e and s < 60.17 < n
    n_ = A.get("ndbc-buoys").prepare({"lat": 36.785, "lon": -122.4}, "live")
    assert n_["url"] == ndbc.LATEST and "36.7850,-122.4000" in n_["notes"]


def test_prepare_without_coordinates():
    assert A.get("ndbc-buoys").prepare({"need": "buoy"}, "live")["url"] == ndbc.LATEST   # one file for every station
    assert A.get("hubeau-hydrometrie").prepare({"need": "x"}, "live") is None
    assert A.get("digitraffic-marine-ais").prepare({"need": "x"}, "live") is None
    f = A.get("fmi-open-data").prepare({"need": "x", "place": "Kumpula"}, "live")
    assert "place=Kumpula" in f["url"] and "bbox" not in f["url"]
    assert A.get("fmi-open-data").prepare({"need": "x"}, "live") is None


def test_broker_hook_returns_the_call():
    from irlpls import broker
    req = irlpls.Request(need="sea temperature at a buoy off Monterey, CA now", lat=36.785, lon=-122.4)
    assert broker._prepared(A.get("ndbc-buoys"), req, "live")["card_id"] == "ndbc-buoys"


# -- NDBC ---------------------------------------------------------------------------------------------------------------
def test_ndbc_text_parser_reads_header_and_drops_missing():
    rows = {r["station"]: r for r in ndbc.parse_latest_obs(text("ndbc-buoys", "latest_obs.txt"))}
    assert len(rows) == 9
    r = rows["46042"]
    assert (r["lat"], r["lon"], r["time"]) == (36.787, -122.408, "2026-10-28T22:30:00Z")
    assert r["values"] == {"WDIR": 320.0, "WSPD": 9.0, "GST": 11.0, "PRES": 1014.3, "ATMP": 17.5, "WTMP": 19.5, "DEWP": 12.6}
    assert rows["46236"]["values"]["WVHT"] == 1.3 and "WDIR" not in rows["46236"]["values"]   # MM = missing
    with pytest.raises(ValueError):
        ndbc.parse_latest_obs("no header here\n1 2 3")


def test_ndbc_live_nearest_stations_and_buoycam(net):
    ndbc_routes(net[0])
    res = A.get("ndbc-buoys").live(36.785, -122.4, radius_km=100, limit=2)
    sensors = [o for o in res if o["kind"] == "sensor"]
    assert {o["station"] for o in sensors} == {"46042", "46092"}
    wt = next(o for o in sensors if o["station"] == "46042" and o["variable"] == "WTMP")
    assert (wt["value"], wt["unit"], wt["label"], wt["phenomenon_time"]) == (19.5, "degC", "water temperature", "2026-10-28T22:30:00Z")
    assert wt["distance_km"] < 1 and wt["url"].endswith("station=46042")
    photo = [o for o in res if o["kind"] == "photo"]
    assert len(photo) == 1 and photo[0]["url"] == "https://www.ndbc.noaa.gov/images/buoycam/Z85A_2026_10_28_2210.jpg"
    assert photo[0]["phenomenon_time"] is None and photo[0]["station_obs_time"] == "2026-10-28T22:30:00Z"
    assert res.warnings == []
    assert A.get("ndbc-buoys").live(0, -150, radius_km=50) == []   # nothing near: empty, not an error


def test_ndbc_camera_errors_become_warnings(net):
    ndbc_routes(net[0], cams=err(429, retry_after=120))
    res = A.get("ndbc-buoys").live(36.785, -122.4)
    assert res and all(o["kind"] == "sensor" for o in res)
    assert res.warnings[0]["code"] == "rate_limited" and res.warnings[0]["retry_after_s"] == 120


# -- Hub'Eau ------------------------------------------------------------------------------------------------------------
def test_hubeau_latest_per_series_includes_site_level_flows():
    best = hub.latest_per_series(js("hubeau-hydrometrie", "observations_tr.json")["data"])
    assert ("F7000001", "Q") in best and best[("F7000001", "Q")]["code_station"] is None
    assert best[("F700000102", "H")]["date_obs"] == "2026-10-28T22:40:00Z"   # newest of several rows
    assert hub.latest_per_series([{"code_station": "X", "grandeur_hydro": "H", "resultat_obs": None}]) == {}


def test_hubeau_live_converts_units_and_names(net):
    hub_routes(net[0])
    res = A.get("hubeau-hydrometrie").live(48.85, 2.35, limit=3)
    by = {(o.get("station_code") or o.get("site_code"), o["label"]): o for o in res}
    lvl = by[("F700000102", "water level")]
    assert (lvl["unit"], lvl["raw_unit"]) == ("m", "mm") and lvl["value"] == lvl["raw_value"] / 1000
    assert lvl["station"].startswith("La Seine à Paris - Austerlitz") and lvl["river"] == "La Seine"
    assert lvl["phenomenon_time"] == "2026-10-28T22:40:00Z" and lvl["data_status"] == "Brute"
    flows = [o for o in res if o["label"] == "flow"]   # one gauge listed under two codes: the same reading once (full pass)
    assert len({(o["value"], o["phenomenon_time"]) for o in flows}) == len(flows)
    flow = flows[0]
    assert (flow["unit"], flow["raw_unit"]) == ("m3/s", "l/s") and flow["station"].startswith("La Seine à Paris")
    assert "code_entite=" in flow["url"]
    assert res.warnings == []
    url, kw = net[1][0]
    assert url.endswith("/observations_tr") and kw["params"]["sort"] == "desc" and kw["params"]["distance"] == 10


def test_hubeau_name_lookup_errors_become_warnings(net):
    hub_routes(net[0])
    net[0][f"{hub.BASE}/referentiel/stations"] = err(403)
    net[0][f"{hub.BASE}/referentiel/sites"] = err(404)
    res = A.get("hubeau-hydrometrie").live(48.85, 2.35, limit=3)
    assert res and {w["code"] for w in res.warnings} == {"refused", "not_found"}
    assert all(o["station"] in ("F700000102", "F700000103", "F7000001") for o in res)


# -- Digitraffic --------------------------------------------------------------------------------------------------------
def test_digitraffic_parser_drops_not_available_codes():
    fc = {"features": [{"mmsi": 1, "geometry": {"coordinates": [24.9, 60.1]},
                        "properties": {"mmsi": 1, "sog": 102.3, "cog": 360.0, "heading": 511, "navStat": 15,
                                       "timestampExternal": 1793228069210}},
                       {"geometry": {"coordinates": []}, "properties": {}}]}
    (v,) = dt.parse_locations(fc)
    assert v["sog"] is None and v["cog"] is None and v["heading"] is None and v["nav_status"] is None
    assert v["time"] == "2026-10-28T22:54:29Z" and (v["lat"], v["lon"]) == (60.1, 24.9)


def test_digitraffic_live_positions_with_names(net):
    dt_routes(net[0])
    res = A.get("digitraffic-marine-ais").live(60.15, 24.95, limit=5)
    assert len(res) == 5 and res.warnings == []
    names = js("digitraffic-marine-ais", "vessels.json")
    for o in res:
        assert o["unit"] == "kn" and o["phenomenon_time"].endswith("Z") and o["result_time"]
        assert o["vessel"] == names[str(o["mmsi"])]["name"].strip()
    assert [o["distance_km"] for o in res] == sorted(o["distance_km"] for o in res)
    assert all(kw["headers"]["Digitraffic-User"] for _, kw in net[1])
    loc_kw = net[1][0][1]["params"]
    assert loc_kw["radius"] == 10 and loc_kw["from"] % 60000 == 0


def test_digitraffic_metadata_errors(net):
    dt_routes(net[0], vessel=err(429, retry_after=30))
    res = A.get("digitraffic-marine-ais").live(60.15, 24.95, limit=5)
    assert len(res) == 5 and res[0]["vessel"].startswith("MMSI ")
    assert len(res.warnings) == 1 and res.warnings[0]["retry_after_s"] == 30   # stops asking after a 429
    assert sum("/vessels/" in u for u, _ in net[1]) == 1


# -- FMI ----------------------------------------------------------------------------------------------------------------
def test_fmi_gml_parser_keeps_newest_and_skips_nan():
    st = fmi.parse_simple(text("fmi-open-data", "weather_simple.xml"))
    assert len(st) == 3
    hel = st[(60.17523, 24.94459)]
    assert hel["t2m"][0] == "2026-10-28T22:50:00Z"
    raw = text("fmi-open-data", "weather_simple.xml")
    assert "NaN" in raw and all(v == v for s in st.values() for _, v in s.values())


def test_fmi_refuses_dtd_and_reads_exception_report():
    with pytest.raises(ValueError):
        fmi.parse_simple('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
    msg = fmi.exception_text(text("fmi-open-data", "exception_report.xml"))
    assert msg == "Invalid bounding box format, expecting xmin,ymin,xmax,ymax[,crs]"
    with pytest.raises(ValueError, match="Invalid bounding box"):
        fmi.parse_simple(text("fmi-open-data", "exception_report.xml"))
    assert fmi.exception_text("not xml") is None


def test_fmi_live_nearest_station_values(net):
    fmi_routes(net[0])
    res = A.get("fmi-open-data").live(60.17, 24.94, limit=1)
    by = {o["parameter"]: o for o in res}
    assert by["t2m"]["unit"] == "degC" and by["t2m"]["label"] == "air temperature"
    assert by["t2m"]["phenomenon_time"] == "2026-10-28T22:50:00Z" and by["t2m"]["distance_km"] < 1
    assert {o["lat"] for o in res} == {60.17523}
    kw = net[1][0][1]
    assert kw["raw"] is True and kw["params"]["storedquery_id"] == fmi.QUERY and kw["params"]["timestep"] == 10


# -- errors from the main call propagate, so the shim maps them (429 keeps Retry-After) --------------------------------
@pytest.mark.parametrize("status", [401, 403, 404, 429])
@pytest.mark.parametrize("cid", IDS)
def test_main_call_errors_propagate_with_retry_after(cid, status, net):
    {"ndbc-buoys": ndbc_routes, "hubeau-hydrometrie": hub_routes, "digitraffic-marine-ais": dt_routes,
     "fmi-open-data": fmi_routes}[cid](net[0])
    main = {"ndbc-buoys": ndbc.LATEST, "hubeau-hydrometrie": f"{hub.BASE}/observations_tr",
            "digitraffic-marine-ais": f"{dt.BASE}/locations", "fmi-open-data": fmi.WFS}[cid]
    net[0][main] = err(status, retry_after=90 if status == 429 else None)
    with pytest.raises(http.HTTPError) as e:
        A.get(cid).live(*POINT[cid])
    assert e.value.status == status
    if status == 429:
        from irlpls import ratelimit
        assert ratelimit.retry_after(e.value.headers) == 90

