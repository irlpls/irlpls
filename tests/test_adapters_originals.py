"""Ready calls for the 20 original adapters (those written before the wrapper waves), and Job.best()'s margin rule.

Offline: prepare() builds calls without the network, so nothing here is sent; a stubbed http layer fails any attempt.
"""
import json
import pathlib
import re
import secrets

import pytest

import irlpls
from irlpls import adapters, broker, http
from irlpls.adapters.base import Adapter, OPERATIONS
from test_integration_shared import validate  # noqa: F401  (the job schema validator fixture)

# card id -> (route, operations it builds, default operation)
ORIGINALS = {
    "nws": ("live", {"read"}, "read"), "usgs-water": ("live", {"read"}, "read"), "noaa-tides": ("live", {"read"}, "read"),
    "caltrans-cctv": ("live", {"read"}, "read"), "windy-webcams": ("live", {"read"}, "read"),
    "purpleair": ("live", {"read"}, "read"), "uk-ea-flood": ("live", {"read"}, "read"),
    "pegelonline": ("live", {"read"}, "read"), "sensor-community": ("live", {"read"}, "read"),
    "copernicus-sentinel": ("live", {"read"}, "read"), "fintraffic-weathercams": ("live", {"read"}, "read"),
    "alertcalifornia": ("live", {"read"}, "read"),
    "hivemapper": ("commission", {"order", "status"}, "order"),
    "proxypics": ("commission", {"read", "order", "status"}, "order"),
    "rentahuman": ("commission", {"quote", "order", "status"}, "quote"),
    "spexi": ("commission", {"order", "status"}, "order"),
    "skyfi": ("commission", {"quote", "order", "status"}, "quote"),
    "planet": ("commission", {"quote", "order", "status"}, "quote"),
    "umbra": ("commission", {"quote", "order", "status"}, "quote"),
    "prolific": ("commission", {"quote", "order", "status"}, "quote"),
}
REQ = {"need": "photo of the storefront on Market Street", "place": "San Francisco", "lat": 37.7793, "lon": -122.4193,
       "quantity": 2, "within_minutes": 2880, "country_code": "US", "state": "CA"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("prepare() must not touch the network")
    monkeypatch.setattr(http, "_open", no_net)


def ad(cid):
    return adapters.ADAPTERS[cid]


def test_every_adapter_now_builds_ready_calls():
    without = sorted(cid for cid, a in adapters.ADAPTERS.items() if type(a).prepare is Adapter.prepare)
    assert without == []
    assert set(ORIGINALS) <= set(adapters.ADAPTERS)


@pytest.mark.parametrize("cid", sorted(ORIGINALS))
def test_each_operation_and_only_those(cid):
    route, ops, default = ORIGINALS[cid]
    a = ad(cid)
    other = "commission" if route == "live" else "live"
    for op in OPERATIONS:
        pc = a.prepare(REQ, route, operation=op)
        if op not in ops:
            assert pc is None, f"{cid} built an unsupported {op}"
            continue
        assert pc["operation"] == op and pc["card_id"] == cid
        assert pc["url"].startswith("https://") and pc["docs_url"].startswith("https://")
        assert pc["safe_to_run_without_approval"] is (op in ("read", "status")), f"{cid} {op}"
        assert a.prepare(REQ, other, operation=op) is None
    assert a.prepare(REQ, route)["operation"] == default
    assert set(broker._prepared_all(a, irlpls.need.Request.from_dict(REQ), route)) == ops


@pytest.mark.parametrize("cid", sorted(ORIGINALS))
def test_key_is_only_named_and_every_placeholder_is_explained(cid):
    route, ops, _ = ORIGINALS[cid]
    a = ad(cid)
    for op in ops:
        pc = a.prepare(REQ, route, operation=op)
        auth = pc["auth"]
        if a.env:
            assert auth["key_name"] == a.env and auth["placeholder"] == "{" + a.env + "}"
            assert auth["get_key_url"].startswith("https://") and auth["in"]
            assert any("{" + a.env + "}" in v for v in pc["headers"].values()), f"{cid} {op}: key header missing"
        else:
            assert auth == {"kind": "none"}
        named = {p["name"] for p in pc.get("placeholders") or []} | ({a.env} if a.env else set())
        used = set(PLACEHOLDER.findall(json.dumps([pc["url"], pc["headers"], pc.get("body")])))
        assert used <= named, f"{cid} {op}: unexplained placeholders {used - named}"
        for p in pc.get("placeholders") or []:
            assert p["what"] and p["where"]


@pytest.mark.parametrize("cid", sorted(ORIGINALS))
def test_module_docstring_cites_public_docs(cid):
    import sys
    mod = sys.modules[type(ad(cid)).__module__]
    assert "Docs:" in mod.__doc__ and "https://" in mod.__doc__.split("Docs:", 1)[1]


def test_needs_a_place_where_the_call_has_one():
    nowhere = {k: v for k, v in REQ.items() if k not in ("lat", "lon")}
    for cid, (route, ops, default) in ORIGINALS.items():
        if cid in ("rentahuman", "prolific", "proxypics"):   # a bounty, a remote study, an address: no coordinates needed
            continue
        assert ad(cid).prepare(nowhere, route) is None, cid
        if "status" in ops:   # a status call needs only the order id
            assert "{" in ad(cid).prepare(nowhere, route, operation="status")["url"], cid


def test_live_calls_are_built_from_the_ask():
    assert ad("nws").prepare(REQ, "live")["url"] == "https://api.weather.gov/points/37.7793,-122.4193"
    assert ad("nws").prepare(REQ, "live")["headers"]["User-Agent"] == "{NWS_USER_AGENT}"
    assert ad("caltrans-cctv").prepare(REQ, "live")["url"].endswith("/d4/cctv/cctvStatusD04.json")   # San Francisco: district 4
    u = ad("purpleair").prepare(REQ, "live")["url"]
    assert "nwlat=37.8" in u and "selng=-122.3" in u and "location_type=0" in u
    assert "nearby=37.7793,-122.4193,10" in ad("windy-webcams").prepare(REQ, "live")["url"]
    assert ad("sensor-community").prepare(REQ, "live")["url"].endswith("/filter/area=37.7793,-122.4193,3")
    assert "parameter_code=00065" in ad("usgs-water").prepare(REQ, "live")["url"]
    assert "collections=sentinel-2-l2a" in ad("copernicus-sentinel").prepare(REQ, "live")["url"]
    assert ad("fintraffic-weathercams").prepare(REQ, "live")["headers"]["Accept-Encoding"] == "gzip"
    assert "geometry=-122.4193,37.7793" in ad("alertcalifornia").prepare(REQ, "live")["url"]


def test_commission_calls_follow_the_documented_shape():
    bee = ad("hivemapper").prepare(REQ, "commission")
    assert bee["url"].endswith("/requestVideo?idempotencyKey={IDEMPOTENCY_KEY}") and bee["headers"]["Authorization"] == "Basic {BEEMAPS_API_KEY}"
    assert bee["body"]["geojson"]["type"] == "Polygon" and bee["auth"]["kind"] == "basic"

    rq, ro = ad("rentahuman").prepare(REQ, "commission", operation="quote"), ad("rentahuman").prepare(REQ, "commission", operation="order")
    assert rq["body"]["dryRun"] is True and "dryRun" not in ro["body"] and ro["body"]["idempotencyKey"] == "{IDEMPOTENCY_KEY}"
    assert rq["body"]["price"] == "{PRICE_USD}" and ad("rentahuman").prepare(dict(REQ, max_price_usd=40), "commission")["body"]["price"] == 40

    sq, so = ad("skyfi").prepare(REQ, "commission", operation="quote"), ad("skyfi").prepare(REQ, "commission", operation="order")
    assert sq["url"].endswith("/order-tasking/validate") and so["url"].endswith("/order-tasking")
    assert sq["body"]["aoi"] == so["body"]["aoi"] and sq["body"]["aoi"].startswith("POLYGON((")

    pq = ad("planet").prepare(REQ, "commission")
    assert pq["headers"]["Authorization"] == "api-key {PLANET_API_KEY}" and pq["body"] == {
        "geometry": {"type": "Point", "coordinates": [REQ["lon"], REQ["lat"]]}}

    uq = ad("umbra").prepare(REQ, "commission")
    assert uq["url"].endswith("/tasking/feasibilities") and uq["auth"]["kind"] == "bearer" and "taskName" not in uq["body"]
    assert ad("umbra").prepare(REQ, "commission", operation="order")["body"]["taskName"]

    po = ad("prolific").prepare(REQ, "commission", operation="order")
    assert po["headers"]["Authorization"] == "Token {PROLIFIC_API_TOKEN}" and "UNPUBLISHED" in po["notes"]
    assert po["body"]["external_study_url"] == "{STUDY_URL}" and po["body"]["total_available_places"] == 2
    assert ad("prolific").prepare(REQ, "commission")["body"] == {"reward": "{REWARD_CENTS}", "total_available_places": 2}

    pp = ad("proxypics").prepare(REQ, "commission")
    assert pp["body"]["address"] == "{STREET_ADDRESS}" and pp["body"]["additional_notes"] == REQ["need"]
    assert ad("proxypics").prepare(REQ, "commission", operation="read")["url"].endswith("/photo-request-templates")

    sp = ad("spexi").prepare(REQ, "commission")
    assert sp["body"]["intent"] == "new_capture" and sp["body"]["geometry"]["geometry"]["type"] == "Polygon"


def test_commission_dry_runs_still_send_the_same_body(monkeypatch):
    """The refactor shares one body builder between commission() and prepare()."""
    spec = {"lat": REQ["lat"], "lon": REQ["lon"]}
    assert ad("spexi").commission(spec)["would_send"]["body"] == ad("spexi").prepare(REQ, "commission")["body"]
    d = ad("skyfi").commission(dict(spec, quote=False))["would_send"]["body"]
    assert d["aoi"] == ad("skyfi").prepare(REQ, "commission")["body"]["aoi"]
    assert ad("proxypics").commission({"address": "1 Market St", "notes": "x"})["would_send"]["body"] == {"address": "1 Market St", "additional_notes": "x"}
    rah = ad("rentahuman").commission({"title": "t", "description": "d"})
    assert "price" not in rah["would_send"]["body"] and any(m.startswith("price") for m in rah["missing"])   # never invented
    assert ad("prolific").commission({"name": "n", "external_study_url": "https://s.example"})["would_send"]["body"]["reward"] == 100
    assert ad("umbra").commission(spec)["would_send"]["body"]["constraints"]["collectionType"] == "SPOTLIGHT"
    assert ad("hivemapper").commission(spec)["would_send"]["body"]["geojson"]["type"] == "Polygon"
    assert ad("planet").commission(dict(spec, quote=False))["would_send"]["body"]["geometry"]["type"] == "Point"


def test_no_secret_value_appears_in_any_ready_call(monkeypatch):
    """Sentinel: every env var these adapters read is set to a random value; no prepare() output (any route, operation
    or error) contains one."""
    names = {a.env for a in map(ad, ORIGINALS) if a.env} | {"USGS_WATER_API_KEY", "NWS_USER_AGENT"}
    sent = {n: "S3NT" + secrets.token_hex(10) for n in names}
    for n, v in sent.items():
        monkeypatch.setenv(n, v)
    outs = []
    for cid in ORIGINALS:
        for req in (REQ, dict(REQ, max_price_usd=25), {"need": "x"}):
            for route in ("live", "commission"):
                for op in (None,) + OPERATIONS:
                    try:
                        outs.append(ad(cid).prepare(req, route) if op is None else ad(cid).prepare(req, route, operation=op))
                    except Exception as e:
                        outs.append(repr(e))
    text = json.dumps(outs, default=str)
    assert not [n for n, v in sent.items() if v in text]
    assert sum(1 for o in outs if isinstance(o, dict)) >= 40


# --------------------------------------------------------------------------------------- Job.best(): the margin rule

def _opt(cid, fit, callable_, route="commission", needs_key=False):
    return {"card_id": cid, "name": cid.title(), "route": route, "fit": fit, "policy": [],
            "sdk": {"callable": callable_, "needs_key": needs_key}, "continue_url": f"https://{cid}.example/docs"}


def _job(*opts):
    j = broker.Job.__new__(broker.Job)
    j.plan = list(opts)
    return j


def test_best_prefers_a_far_better_directory_only_option():
    shaip, em = _opt("shaip", 67, False), _opt("execution-market", 18, True)
    assert _job(shaip, em).best() is shaip


def test_best_keeps_a_callable_option_within_the_margin():
    top, c = _opt("dir", 67, False), _opt("call", 67 - broker.CALLABLE_MARGIN, True)
    assert _job(top, c).best() is c
    c["fit"] -= 1
    assert _job(top, c).best() is top


def test_best_uses_the_tighter_margin_on_live_routes():
    top = _opt("dir", 80, False, route="live")
    near, far = _opt("near", 80 - broker.LIVE_MARGIN, True, route="live"), _opt("far", 80 - broker.LIVE_MARGIN - 1, True, route="live")
    assert _job(top, near).best() is near and _job(top, far).best() is top
    assert broker.LIVE_MARGIN == 10 and broker.CALLABLE_MARGIN == 15


def test_best_near_tie_rule_among_callables_is_unchanged():
    keyed, ready = _opt("keyed", 70, True, needs_key=True), _opt("ready", 66, True)
    assert _job(keyed, ready).best() is ready                       # within 5: callable now wins
    assert _job(keyed, _opt("ready", 64, True)).best() is keyed     # beyond 5: the better callable stays
    lv = _opt("keyed", 70, True, route="live", needs_key=True)
    assert _job(lv, _opt("ready", 61, True, route="live")).best()["card_id"] == "ready"   # 10 on a live route
    assert _job(_opt("p", 90, True) | {"policy": ["x"]}).best() is None


def test_plan_says_plainly_to_order_by_hand(validate):  # noqa: F811
    job = irlpls.request("consented landmark images with documented participant releases", geocode=False)
    best = job.best()
    assert best["card_id"] == "shaip" and not best["sdk"]["callable"]
    bh = best["by_hand"]
    assert "order it by hand" in bh["msg"] and bh["docs_url"] == best["continue_url"] and bh["docs_url"].startswith("https://")
    assert any("order it by hand" in e["msg"] for e in job.log)
    assert sum(1 for o in job.plan if o.get("by_hand")) == 1
    assert all(not o.get("by_hand") for o in job.plan if o["sdk"]["callable"])
    validate(json.loads(json.dumps(job.to_dict(), default=str)), json.loads((ROOT / "schema/job.schema.json").read_text())["$id"])


def test_by_hand_carries_the_cards_documented_endpoint_when_it_has_one():
    card = {"api": {"base_url": "https://api.example.com/v1", "endpoints": [
        {"action": "search", "method": "GET", "path": "/things"}, {"action": "order", "method": "POST", "path": "/orders", "notes": "n"}]}}
    bh = broker._by_hand(_opt("x", 50, False), card)
    assert bh["card_endpoint"] == {"action": "order", "method": "POST", "url": "https://api.example.com/v1/orders", "notes": "n"}
    assert broker._by_hand(_opt("x", 50, False, route="live"), card)["card_endpoint"]["url"].endswith("/things")
    assert "card_endpoint" not in broker._by_hand(_opt("x", 50, False), {})
    pc = {"operation": "read", "url": "https://p.example"}
    assert broker._by_hand(dict(_opt("x", 50, False), prepared_call=pc), {})["prepared_call"] == pc


def test_submit_on_a_directory_only_best_fails_with_the_by_hand_note():
    job = irlpls.request("consented landmark images with documented participant releases", geocode=False)
    job.state = "proposed"
    job.submit()
    assert job.state == "failed" and "order it by hand" in job.log[-1]["msg"]
