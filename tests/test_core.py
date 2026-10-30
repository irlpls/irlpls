from datetime import datetime, timedelta, timezone

import irlpls
from irlpls import Request, parse
from irlpls.match import in_scope, score
from irlpls.checks import compare


def reg():
    return irlpls.registry().cards


def test_only_new_data_providers_are_listed():
    ids = {c["id"] for c in reg()}
    assert "mapillary" not in ids          # archive only
    assert "mturk" not in ids              # defunct
    assert all(in_scope(c) for c in reg())


def test_every_card_has_seven_guarantees_and_delivery():
    keys = {k for k, _ in irlpls.ASSURANCES}
    for c in irlpls.registry().all:
        assert c.get("short") and c.get("access"), c["id"]
        assert set(c["verification"]) == keys, c["id"]
        assert "delivery" in c, c["id"]


def test_parse_live_and_commission():
    r = parse("is Shoal Creek in Austin, TX flooded right now")
    assert r.mode == "live" and r.within_minutes == 60
    r = parse("500 video clips of people opening bakery doors within 5 days")
    assert r.mode == "commission" and r.quantity == 500 and r.within_minutes == 5 * 1440


def test_right_thing_is_always_required():
    r = parse("photos of a storefront")
    assert r.require == ["time", "location", "content"] and r.default_proof
    r = parse("photos of a storefront by a vetted human today")
    assert set(r.require) == {"human", "time", "location", "content"}


def test_live_request_penalises_slow_capture():
    sky = irlpls.card("skyfi")
    r = Request(need="", terms=["satellite"], mode="live", within_minutes=60)
    assert any("not live" in g for g in score(sky, r)["gaps"])


def test_compare_metadata_to_request():
    now = datetime.now(timezone.utc)
    r = Request(need="x", lat=30.0, lon=-97.0, mode="commission")
    items = [{"phenomenon_time": (now - timedelta(days=2)).isoformat(), "lat": 30.0, "lon": -97.0},
             {"phenomenon_time": now.isoformat(), "lat": 30.0, "lon": -97.0},
             {"phenomenon_time": now.isoformat(), "lat": 31.0, "lon": -97.0}]
    res = compare(items, r, since=now - timedelta(hours=1), radius_km=1, check_content=lambda it, need: (True, "ok"))
    assert [i["verdict"] for i in res["items"]] == ["fail", "pass", "fail"]


def test_commission_is_dry_run_without_key():
    out = irlpls.commission("hivemapper", {"lat": 30.27, "lon": -97.74})
    assert out["dry_run"] and out["would_send"]["headers"]["Authorization"] == "***"


def test_policy_flags_and_blocks():
    from irlpls import Policy, PolicyViolation
    pol = Policy(require={"location": "evidenced"}, need_ai_training=True)
    assert "location must be evidenced, card says claimed" in pol.check(irlpls.card("hivemapper"))
    res = irlpls.find("street video in Austin within 3 days", geocode=False, policy=pol)
    assert all("policy" in m for m in res)
    out = irlpls.commission("hivemapper", {"lat": 30.27, "lon": -97.74}, policy=pol)
    assert out["blocked_by_policy"]
    try:
        irlpls.commission("hivemapper", {"lat": 30.27, "lon": -97.74}, policy=pol, confirm=True)
        assert False, "should have raised"
    except PolicyViolation:
        pass


def test_broker_live_route_and_clarify():
    job = irlpls.request("is the river high right now", geocode=False)
    assert job.state == "clarifying" and job.questions[0]["field"] == "place"
    job = irlpls.request("is the river high right now", geocode=False, lat=29.97, lon=-98.9, country_code="us")
    assert job.state == "proposed"
    assert any(o["route"] == "live" and o["card_id"] == "usgs-water" for o in job.plan)


def test_broker_commission_never_orders_without_confirm():
    job = irlpls.request("street video within 3 days", geocode=False, lat=30.27, lon=-97.74, country_code="us")
    job.choose("hivemapper").submit()
    assert job.state == "quoted" and job.quote["dry_run"]


def test_every_adapter_has_a_card_and_mode():
    for cid, a in irlpls.adapters.ADAPTERS.items():
        c = irlpls.card(cid)
        assert c, cid
        modes = c["delivery"]["modes"]
        assert (a.can_live and "live" in modes) or (a.can_commission and "commission" in modes), cid


def test_broker_prefers_ready_providers():
    job = irlpls.request("is traffic backed up on the Bay Bridge right now", geocode=False, lat=37.79, lon=-122.39, country_code="us")
    b = job.best()
    assert b and not b["sdk"]["needs_key"]


def test_state_coverage():
    from irlpls import geo
    caltrans = irlpls.card("caltrans-cctv")
    assert geo.covers(caltrans, "us", "texas") is False
    assert geo.covers(caltrans, "us", "california") is True
    assert geo.covers(irlpls.card("proxypics"), "us", "texas") is True


def test_free_only_policy():
    from irlpls import Policy
    pol = Policy(free_only=True)
    assert pol.check(irlpls.card("usgs-water")) == []
    assert "not free to use" in pol.check(irlpls.card("skyfi"))


def test_free_means_free():
    from irlpls.policy import is_free
    assert is_free(irlpls.card("nws")) and is_free(irlpls.card("openaq"))
    assert not is_free(irlpls.card("windy-webcams"))   # freemium is not free


def test_outside_coverage_scores_zero():
    from irlpls import Request
    r = Request(need="", terms=["traffic"], modalities=["photo"], country_code="us", state="texas")
    assert score(irlpls.card("caltrans-cctv"), r)["fit"] == 0


# --- Known gaps closed (docs/STANDARD.md section 11) --------------------------------------------------------------
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from irlpls.adapters.beemaps import BeeMaps

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema"


class FakeBeeMaps(BeeMaps):
    """Bee Maps with the network replaced: a quote of $30 when dry, an order id when confirmed, scripted statuses."""
    def __init__(self, statuses=()):
        self.confirmed, self.statuses = [], list(statuses)

    def configured(self):
        return True

    def commission(self, spec, confirm=False):
        if confirm:
            self.confirmed.append(spec)
            return {"id": "req-1"}
        return {"dry_run": True, "quote": {"total": 30}}

    def status(self, order):
        return self.statuses.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(statuses=()):
        a = FakeBeeMaps(statuses)
        monkeypatch.setitem(irlpls.adapters.ADAPTERS, "hivemapper", a)
        return a
    return install


def street_job(policy=None):
    job = irlpls.request("street video within 3 days", geocode=False, lat=30.27, lon=-97.74, country_code="us", policy=policy)
    return job.choose("hivemapper")


def test_auto_submit_requires_max_order_usd(fake):
    a = fake()
    job = street_job({"auto_submit": True}).submit()
    assert job.state == "quoted" and not a.confirmed          # no ceiling: no standing mandate
    job = street_job({"auto_submit": True, "max_order_usd": 50}).submit()
    assert job.state == "submitted" and len(a.confirmed) == 1  # within the ceiling: placed without asking
    job = street_job({"auto_submit": True, "max_order_usd": 10}).submit()
    assert job.state == "quoted" and job.quote["blocked_by_policy"] and len(a.confirmed) == 1


def test_commission_reaches_delivered(fake):
    fake([{"status": "pending"}, {"status": "fulfilled", "videos": [{"url": "https://v/1.mp4", "timestamp": "2026-10-25T10:00:00Z"}]}])
    job = street_job().submit(confirm=True, idempotency_key="test-1")
    assert job.state == "submitted"
    assert job.refresh().state == "in_flight" and job.provider_status == "pending"
    job.refresh()
    assert job.state == "delivered" and job.provider_status == "fulfilled"
    assert job.results[0]["url"] == "https://v/1.mp4" and job.results[0]["provider_proof"]
    assert job.refresh().state == "delivered"                  # terminal: no further polling


@pytest.mark.parametrize("status,state", [({"status": "expired"}, "failed"), ({"status": "cancelled"}, "failed"),
                                          ({"status": "something new"}, "in_flight"), ({}, "in_flight")])
def test_refresh_maps_only_known_statuses(fake, status, state):
    fake([status])
    job = street_job().submit(confirm=True, idempotency_key="test-1").refresh()
    assert job.state == state and job.provider_status == status.get("status")   # unmapped is shown as sent, never invented


def test_delivered_without_items_points_to_the_order(fake):
    fake([{"status": "fulfilled"}])
    job = street_job().submit(confirm=True, idempotency_key="test-1").refresh()
    assert job.state == "delivered" and job.results[0]["kind"] == "order" and job.results[0]["phenomenon_time"] is None


def test_adapter_status_maps_follow_provider_docs():
    from irlpls.adapters import get
    assert get("skyfi").map_status("DELIVERY_COMPLETED") == "delivered"
    assert get("skyfi").map_status("PAYMENT_FAILED") == "failed"
    assert get("skyfi").map_status("SOMETHING_ELSE") is None
    assert get("umbra").map_status("TASKED") == "in_flight" and get("umbra").map_status("ANOMALY") == "failed"
    assert get("umbra").deliverables({"collectIds": ["c1"]})[0]["collect_id"] == "c1"
    assert get("proxypics").map_status("FULFILLED") == "delivered" and get("proxypics").map_status("completed") == "in_flight"
    for cid in ("planet", "prolific", "rentahuman", "spexi"):   # docs list no status values: nothing mapped
        assert get(cid).map_status("COMPLETED") is None, cid


def test_policy_violation_fails_the_job_instead_of_raising(fake):
    a = fake()
    job = street_job({"deny": ["hivemapper"]}).submit(confirm=True, idempotency_key="test-1")
    assert job.state == "failed" and not a.confirmed
    assert "violates policy" in job.log[-1]["msg"]


def test_request_document_carries_why_and_policy():
    doc = {"need": "street video within 3 days", "why": "check a storefront", "lat": 30.27, "lon": -97.74,
           "country_code": "us", "policy": {"deny": ["hivemapper"]}}
    job = irlpls.request(doc, geocode=False)
    assert job.need == doc["need"] and job.why == doc["why"] and job.policy.deny == ["hivemapper"]
    assert job.request.terms and job.state == "proposed"
    assert next(o for o in job.plan if o["card_id"] == "hivemapper")["policy"] == ["on the deny list"]


def test_job_fields_are_in_the_schema(fake):
    s = json.loads((SCHEMA / "job.schema.json").read_text())
    fake([{"status": "fulfilled"}])
    d = street_job({"max_order_usd": 50, "auto_submit": True}).submit().refresh().to_dict()
    assert set(s["required"]) <= set(d) and set(d) <= set(s["properties"])
    assert d["state"] in s["properties"]["state"]["enum"]
    plan = s["properties"]["plan"]["items"]
    for o in d["plan"]:
        assert set(plan["required"]) <= set(o) and set(o) <= set(plan["properties"]), set(o) - set(plan["properties"])
    for r in d["results"]:
        assert set(s["properties"]["results"]["items"]["required"]) <= set(r)
    derived = set(irlpls.Request.__dataclass_fields__) - set(json.loads((SCHEMA / "request.schema.json").read_text())["properties"])
    assert derived <= set(s["properties"]["request"]["properties"])
    assert not {"draft", "authorized", "partial"} & set(s["properties"]["state"]["enum"])


def test_card_schema_defines_sources_and_evidence():
    s = json.loads((SCHEMA / "capture-card.schema.json").read_text())
    assert s["properties"]["sources"]["items"]["required"] == ["claim", "url"]
    assert s["properties"]["verification"]["additionalProperties"]["then"]["required"] == ["method", "evidence_url"]
    assert s["properties"]["pricing"]["properties"]["from_usd"]["minimum"] == 0
    for c in irlpls.registry().all:
        if c["status"] == "unclaimed":
            assert c.get("sources") and all(x.get("claim") and x.get("url") for x in c["sources"]), c["id"]
        for k, g in c["verification"].items():
            if g["level"] == "evidenced":
                assert g.get("method") and g.get("evidence_url"), (c["id"], k)


def test_zero_price_means_not_published():
    from irlpls import Policy
    c = dict(irlpls.card("proxypics"), pricing={"model": "per request", "from_usd": 0, "unit": "request"})
    assert "price not published" in Policy(max_unit_price_usd=100).check(c)


def test_free_provider_price_is_zero_and_unpublished_is_null():
    job = irlpls.request("is traffic backed up on the Bay Bridge right now", geocode=False, lat=37.79, lon=-122.39, country_code="us")
    assert next(o for o in job.plan if o["card_id"] == "caltrans-cctv")["price"] == 0   # access.cost free
    job = irlpls.request("photo of the storefront at 123 Main St, Springfield, IL today", geocode=False, lat=39.8, lon=-89.65,
                         country_code="us")
    assert next(o for o in job.plan if o["card_id"] == "proxypics")["price"] is None     # paid, price not published


def test_free_to_use_includes_free_with_key():
    from irlpls.policy import is_free
    assert is_free({"access": {"cost": "free with key"}}) and is_free({"access": {"cost": "free"}})
    assert not is_free({"access": {"cost": "freemium"}}) and not is_free({})


def test_matching_ignores_latency_and_freshness():
    r = parse("is Shoal Creek in Austin, TX flooded right now")
    c = irlpls.card("usgs-water")
    bare = {k: v for k, v in c.items() if k not in ("latency", "freshness")}
    assert score(c, r)["fit"] == score(bare, r)["fit"]


def test_policy_require_levels_are_claimed_or_evidenced():
    from irlpls import Policy
    for bad in ({"time": "none"}, {"time": "proven"}, {"weather": "claimed"}):
        with pytest.raises(ValueError):
            Policy(require=bad)
    assert Policy.from_dict({"require": {"time": "evidenced"}}).require == {"time": "evidenced"}


# --- The landing page's example 1, made true (docs/examples/mcp-run-guadalupe.md, "Page vs reality") ---------------
GUADALUPE = "is the Guadalupe at Comfort, TX good for tubing today"


def guadalupe(policy=None):
    return irlpls.request(GUADALUPE, why="user deciding whether to go tubing", geocode=False, lat=29.9677, lon=-98.905,
                          country_code="us", state="texas", policy=policy)


def test_ruled_out_providers_have_reasons():
    job = guadalupe({"max_order_usd": 0})
    assert job.best()["card_id"] == "usgs-water"
    out = {o["card_id"]: o for o in job.ruled_out}
    assert out["nws"]["reason"] == "no_match" and "tubing" in out["nws"]["why"]                       # "no flow"
    assert out["caltrans-cctv"]["reason"] == "outside_coverage" and out["caltrans-cctv"]["label"] == "wrong state"
    assert out["caltrans-cctv"]["why"] == "doesn't cover Texas"
    assert out["skyfi"]["reason"] == "policy:max_order_usd" and out["skyfi"]["label"] == "paid"      # "paid"
    assert out["skyfi"]["reasons"] == ["policy:max_order_usd", "too_slow", "no_match", "needs_key"]
    codes = re.compile(r"^(no_match|outside_coverage|too_slow|needs_key|terms|policy:[a-z_]+)$")
    for o in job.ruled_out:
        assert o["why"] and o["label"] and codes.match(o["reason"]) and o["reasons"][0] == o["reason"], o
        assert all(codes.match(c) for c in o["reasons"]), o
    # every in-scope provider is accounted for exactly once: in the plan or ruled out
    planned = [o["card_id"] for o in job.plan]
    assert sorted(planned + list(out)) == sorted(c["id"] for c in reg() if in_scope(c))
    d = job.to_dict()
    assert d["ruled_out"] == job.ruled_out and json.loads(json.dumps(d))["ruled_out"][0]["reason"]


def test_ruled_out_without_policy_gives_the_card_reasons():
    out = {o["card_id"]: o for o in guadalupe().ruled_out}
    assert out["skyfi"]["reason"] == "too_slow" and "not live" in out["skyfi"]["why"]
    assert not any(o["reason"].startswith("policy:") for o in out.values())


def test_max_order_usd_zero_excludes_paid_providers_at_plan_time():
    from irlpls import Policy
    pol = Policy(max_order_usd=0)
    assert pol.check(irlpls.card("skyfi")) and pol.violations(irlpls.card("skyfi"))[0][0] == "policy:max_order_usd"
    assert pol.check(irlpls.card("usgs-water")) == [] and pol.check(irlpls.card("openaq")) == []   # free, free with key
    assert Policy(max_order_usd=100).check(irlpls.card("umbra"))      # starts at $675: no order can fit $100
    assert Policy(max_order_usd=1000).check(irlpls.card("umbra")) == []
    job = irlpls.request("photo of the storefront at 123 Main St, Springfield, IL today", geocode=False, lat=39.8, lon=-89.65,
                         country_code="us", policy={"max_order_usd": 0})
    assert all(o["policy"] for o in job.plan) and job.best() is None   # paid commissions are flagged and never chosen
    assert job.submit().state == "failed"


def test_today_is_live_for_conditions_and_a_deadline_for_captures():
    r = parse(GUADALUPE)
    assert r.mode == "live" and r.terms == ["tubing"]
    for q in ("photo of the storefront at 123 Main St, Springfield, IL today",
              "is the bakery on Elm St in Springfield, IL open? send a photo of the door today",
              "200 participants to record a short video of their kitchen today"):
        assert parse(q).mode == "commission", q
    assert parse("active fire hotspots in Alberta, Canada today").mode == "live"
    assert guadalupe().plan[0]["route"] == "live" and guadalupe().request.mode == "live"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_today_parsing_parity_with_match_js():
    qs = [GUADALUPE, "photo of the storefront at 123 Main St, Springfield, IL today", "active fire hotspots in Alberta, Canada today",
          "is the bakery on Elm St in Springfield, IL open? send a photo of the door today", "satellite image of a farm today"]
    js = r"""const fs=require('fs');global.window={};eval(fs.readFileSync(process.argv[1],'utf8'));
      console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(q=>{const r=window.IRLMatch.parse(q,[]);return [r.mode,r.within_minutes]})))"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "src" / "match.js")], input=json.dumps(qs), capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [[parse(q).mode, parse(q).within_minutes] for q in qs]


def test_policy_echo_keeps_zero():
    from irlpls import Policy
    assert Policy(max_order_usd=0).to_dict() == {"max_order_usd": 0, "include_unclaimed": True}
    assert Policy(max_unit_price_usd=0, allow=[]).to_dict()["max_unit_price_usd"] == 0
    assert Policy(allow=[]).to_dict()["allow"] == []                  # allow nothing is a setting too
    assert guadalupe({"max_order_usd": 0}).to_dict()["policy"]["max_order_usd"] == 0
    assert Request(need="x", lat=0.0, lon=0.0, max_price_usd=0).to_dict()["lat"] == 0.0


# USGS adapter with the network replaced: OGC readings, the statistics API (observationNormals), and a rate-limited camera service.
STATS = [("08167000", "00060", "ft3/s", 51, 103, 201, 86)]


def normals(params, rows):
    """A USGS statistics API observationNormals reply (shape of fixtures/adapters/usgs-water/observation_normals.json):
    rows of (site number, parameter code, unit, p25, p50, p75, years) for the day the request asks for."""
    day = (params or {}).get("start_date")
    feats = {}
    for site, code, unit, p25, p50, p75, n in rows:
        f = feats.setdefault(site, {"type": "Feature", "properties": {"monitoring_location_id": "USGS-" + site, "data": []}})
        f["properties"]["data"].append({"parameter_code": code, "unit_of_measure": unit, "parent_statistic_id": "00003", "values": [
            {"time_of_year": day, "time_of_year_type": "day_of_year", "computation": "percentile", "sample_count": n,
             "percentiles": ["5", "10", "25", "50", "75", "90", "95"], "values": ["1", "2", str(p25), str(p50), str(p75), "900", "999"]}]})
    return {"type": "FeatureCollection", "features": list(feats.values()), "next": None}



@pytest.fixture
def usgs_net(monkeypatch):
    from irlpls import http
    from irlpls.adapters import usgs_water
    seen = []
    t = (datetime.now(timezone.utc) - timedelta(minutes=20)).replace(microsecond=0)
    local = t.astimezone(timezone(timedelta(hours=-5)))

    def get(url, params=None, headers=None, raw=False, **kw):
        seen.append(url)
        if "latest-continuous" in url:
            feats = [] if params["parameter_code"] != "00060" else [{"geometry": {"coordinates": [-98.897, 29.965]}, "properties": {
                "monitoring_location_id": "USGS-08167000", "time": local.isoformat(), "value": "76.3", "unit_of_measure": "ft3/s"}}]
            return {"features": feats}
        if "observationNormals" in url:
            assert params["monitoring_location_id"] == ["USGS-08167000"] and params["computation_type"] == "percentile"
            assert params["start_date"] == params["end_date"] == f"{local.month:02d}-{local.day:02d}"  # only the reading's day
            return normals(params, STATS)
        if "nims" in url:
            raise http.HTTPError(429, url, "OVER_RATE_LIMIT")
        raise AssertionError(url)
    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(usgs_water, "_cameras", None)
    monkeypatch.delenv("USGS_WATER_API_KEY", raising=False)
    return seen


def test_usgs_returns_daily_median_age_and_camera_warning(usgs_net):
    res = irlpls.live("usgs-water", lat=29.9677, lon=-98.905)
    (o,) = list(res)
    assert o["value"] == 76.3 and o["daily_median"] == 103.0
    assert o["daily_stats"]["p25"] == 51.0 and o["daily_stats"]["count"] == 86 and o["daily_stats"]["unit"] == "ft³/s"
    assert 1100 <= o["age_s"] <= 1300                                    # about 20 minutes old
    (w,) = res.warnings
    assert w["code"] == "camera_unavailable" and w["status"] == 429 and "USGS_WATER_API_KEY" in w["msg"]


def test_broker_surfaces_adapter_warnings(usgs_net):
    job = guadalupe({"max_order_usd": 0}).submit()
    assert job.state == "delivered" and job.results[0]["daily_median"] == 103.0
    assert [w["code"] for w in job.warnings] == ["camera_unavailable"]
    assert any("camera_unavailable" in e["msg"] for e in job.log) and job.to_dict()["warnings"] == job.warnings


def test_age_s_reads_any_offset():
    from irlpls.adapters.base import age_s
    now = datetime(2026, 10, 25, 22, 47, tzinfo=timezone.utc)
    assert age_s("2026-10-25T17:35:00.000-05:00", now) == 12 * 60
    assert age_s("2026-10-25T22:35:00Z", now) == 12 * 60
    assert age_s(None, now) is None and age_s("2026-10-25T22:35:00", now) is None


def test_every_mcp_tool_and_argument_is_described(monkeypatch):
    """Stand-ins for the mcp server and pydantic record what FastMCP/MCPServer would read: each tool's __doc__ and each
    argument's Field(description=...)."""
    import inspect
    import sys
    import types
    import typing
    tools = []

    class Server:
        def __init__(self, name, version=""):
            self.version = version

        def tool(self):
            return lambda fn: tools.append(fn) or fn

    class Field:
        def __init__(self, description=None):
            self.description = description
    for name, attrs in {"mcp": {}, "mcp.server": {}, "mcp.server.mcpserver": {"MCPServer": Server}, "pydantic": {"Field": Field}}.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    from irlpls import mcp_server
    assert mcp_server.build().version == irlpls.__version__
    names = {fn.__name__ for fn in tools}
    assert {"request_data", "answer_questions", "choose_provider", "submit_job", "get_job", "find_providers", "get_card"} <= names
    for fn in tools:
        assert fn.__doc__ and len(fn.__doc__) > 80, fn.__name__
        hints = typing.get_type_hints(fn, include_extras=True)
        for arg in inspect.signature(fn).parameters:
            meta = getattr(hints[arg], "__metadata__", ())
            assert meta and meta[0].description, (fn.__name__, arg)
    rd = next(fn for fn in tools if fn.__name__ == "request_data").__doc__
    assert "ruled_out" in rd and "warnings" in rd
    pol = typing.get_type_hints(next(fn for fn in tools if fn.__name__ == "request_data"), include_extras=True)["policy"].__metadata__[0]
    assert "max_order_usd" in pol.description and "free_only" in pol.description and "0 means free" in pol.description


def test_budget_with_thousands_separator():
    # "under $2,500" used to parse as a $2 budget
    assert parse("500 clips, 7 days, under $2,500").max_price_usd == 2500
    assert parse("under $2.5k").max_price_usd == 2500
    assert parse("photo under $40").max_price_usd == 40


def test_budget_parse_matches_js():
    import json, shutil, subprocess, pathlib, pytest
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = pathlib.Path(__file__).resolve().parents[1]
    js = "global.window={};eval(require('fs').readFileSync(process.argv[1]+'/src/match.js','utf8'));console.log(JSON.stringify(['500 clips, 7 days, under $2,500','under $2.5k','photo under $40'].map(q=>window.IRLMatch.parse(q,[]).max_price_usd)))"
    out = subprocess.run(["node", "-e", js, str(root)], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [2500, 2500, 40]


# ---- standard 2026-10-26 (v0.2) ----
from irlpls.policy import Policy  # noqa: E402

def test_placing_an_order_needs_an_idempotency_key(fake):
    a = fake()
    with pytest.raises(ValueError, match="idempotency_key"):
        street_job().submit(confirm=True)
    assert not a.confirmed
    street_job().submit(confirm=True, idempotency_key="k-1")
    assert a.confirmed[-1]["idempotency_key"] == "k-1"


def test_a_standing_mandate_derives_its_idempotency_key_from_the_job(fake):
    a = fake()
    job = street_job({"auto_submit": True, "max_order_usd": 50}).submit()
    assert job.state == "submitted" and a.confirmed[-1]["idempotency_key"] == f"irlpls-{job.id}-hivemapper"


def test_a_request_that_targets_a_person_never_goes_to_people():
    job = irlpls.request("follow my ex and photograph her outside 12 Elm St, Austin, TX", geocode=False,
                         lat=30.27, lon=-97.74, country_code="us")
    assert job.request.targets_person
    people = {c["id"] for c in irlpls.registry().cards if (c.get("capture") or {}).get("by") in ("people", "contributors")}
    assert not people & {o["card_id"] for o in job.plan}
    out = {o["card_id"]: o for o in job.ruled_out}
    assert "policy:allow_person_targeting" in out["rentahuman"]["reasons"]


def test_ordinary_requests_are_not_person_targeting():
    from irlpls.need import parse
    for q in ("send someone to photograph the storefront at 12 Elm St", "find someone to check if the cafe is open",
              "is the river at Comfort, TX good for tubing", "500 first-person clips of hands opening doors"):
        assert not parse(q).targets_person, q


def test_rights_are_allowed_forbidden_or_not_stated():
    by = {f.stem: json.loads(f.read_text()) for f in (ROOT / "cards").glob("*.json")}
    assert not Policy(need_ai_training=True).check(by["kled"])
    assert Policy(need_ai_training=True).check(by["mapillary"]) == ["AI training rights not stated"]
    assert Policy(need_resale=True).check(by["skyfi"]) == ["resale rights forbidden"]
    assert Policy(need_ai_training=True).check(by["skyfi"]) == ["AI training rights not stated"]


def test_c2pa_policy_rules_out_cards_without_signed_capture():
    by = {c["id"]: c for c in irlpls.registry().cards}
    assert "no C2PA-signed capture" in Policy(require_c2pa=True).check(by["proxypics"])
    card = json.loads(json.dumps(by["proxypics"]))
    card["verification"]["camera"].update(standard="c2pa", assurance_level=1)
    assert not Policy(require_c2pa=True).check(card)
    assert Policy(min_assurance_level=2).check(card) == ["C2PA assurance level 1, need 2"]


def test_coverage_areas_decide_before_the_text():
    from irlpls import geo
    by = {c["id"]: c for c in irlpls.registry().cards}
    assert geo.covers(by["wsdot-cameras"], "us", "washington") is True
    assert geo.covers(by["wsdot-cameras"], "us", "texas") is False
    assert geo.covers(by["proxypics"], "gb") is False and geo.covers(by["rentahuman"], "ke") is True


def test_cards_follow_the_dated_standard():
    jsonschema = pytest.importorskip("jsonschema")
    s = json.loads((ROOT / "schema" / "capture-card.schema.json").read_text())
    for f in sorted((ROOT / "cards").glob("*.json")):
        d = json.loads(f.read_text())
        jsonschema.validate(d, s)
        assert d["spec_version"] == "2026-10-26" and d["capability"] == "com.irlpls.capture.card"


def test_validate_catches_what_the_schema_cannot_say():
    from irlpls import validate
    good = json.loads((ROOT / "cards" / "skyfi.json").read_text())
    assert not [p for p in validate.problems(good) if p[0] == "error"]
    bad = json.loads(json.dumps(good))
    bad["verification"]["time"] = {"level": "evidenced", "unknown": "x"}
    bad["status"], bad["sources"] = "unclaimed", []
    bad["expires"] = "2030-01-01"
    bad["spec_version"] = "0.1"
    msgs = " | ".join(f"{w}: {m}" for s, w, m in validate.problems(bad) if s == "error")
    assert "verification.time" in msgs and "sources" in msgs and "expires" in msgs and "spec_version" in msgs


def test_a_provider_maintained_card_must_come_from_its_own_domain():
    from irlpls import validate
    card = json.loads((ROOT / "cards" / "skyfi.json").read_text())
    card["status"] = "provider-maintained"
    ok = validate.problems(card, fetched_from="https://skyfi.com/.well-known/irlpls")
    off = validate.problems(card, fetched_from="https://irlpls.com/cards/skyfi.json")
    assert not [p for p in ok if p[1] == "status"]
    assert [p for p in off if p[1] == "status" and p[0] == "error"]


def test_every_schema_is_served_at_the_address_its_id_names():
    for f in (ROOT / "schema").glob("*.schema.json"):
        sid = json.loads(f.read_text())["$id"]
        assert sid.startswith("https://irlpls.com/2026-10-26/schemas/"), sid


# ---- adapters for the global keyless feeds (recorded responses; no network) ----

def _fake_get(routes):
    def get(url, params=None, **kw):
        for k, v in routes.items():
            if k in url:
                return v
        raise AssertionError("unexpected URL " + url)
    return get


def test_uk_ea_flood_returns_latest_level_with_units(monkeypatch):
    from irlpls import http
    st = {"items": [{"@id": "http://x/id/stations/0006", "label": "Westminster", "lat": 51.5012, "long": -0.1238, "riverName": "Thames Tideway",
                     "measures": [{"@id": "http://x/id/measures/0006-level", "parameter": "level", "qualifier": "Tidal Level", "unitName": "mAOD"}]}]}
    rd = {"items": [{"@id": "http://x/data/readings/1", "dateTime": "2026-10-27T02:15:00Z", "measure": "http://x/id/measures/0006-level", "value": 3.71}]}
    monkeypatch.setattr(http, "get", _fake_get({"/stations/0006/readings": rd, "/id/stations": st}))
    o = irlpls.live("uk-ea-flood", lat=51.5, lon=-0.12)[0]
    assert (o["value"], o["unit"], o["label"], o["phenomenon_time"]) == (3.71, "m", "water level (tidal level)", "2026-10-27T02:15:00Z")
    assert o["datum"] == "Ordnance Datum (AOD)"   # one unit vocabulary: the datum is kept out of the unit


def test_pegelonline_returns_level_and_flow(monkeypatch):
    from irlpls import http
    st = [{"uuid": "u1", "longname": "KÖLN", "latitude": 50.937, "longitude": 6.963, "water": {"longname": "RHEIN"},
           "timeseries": [{"shortname": "W", "unit": "cm", "currentMeasurement": {"timestamp": "2026-10-27T03:30:00+01:00", "value": 153.0}},
                          {"shortname": "Q", "unit": "m³/s", "currentMeasurement": {"timestamp": "2026-10-27T03:15:00+01:00", "value": 1010.0}}]}]
    monkeypatch.setattr(http, "get", _fake_get({"stations.json": st}))
    got = {o["label"]: o["value"] for o in irlpls.live("pegelonline", lat=50.94, lon=6.96)}
    assert got == {"water level": 153.0, "flow": 1010.0}


def test_sensor_community_drops_impossible_readings(monkeypatch):
    from irlpls import http
    rows = [{"timestamp": "2026-10-27 02:35:41", "sensor": {"id": 1, "sensor_type": {"name": "SDS011"}}, "location": {"latitude": "52.52", "longitude": "13.40", "indoor": 0},
             "sensordatavalues": [{"value_type": "P2", "value": "3.3"}, {"value_type": "P1", "value": "14.5"}]},
            {"timestamp": "2026-10-27 02:35:48", "sensor": {"id": 2, "sensor_type": {"name": "BME280"}}, "location": {"latitude": "52.53", "longitude": "13.41", "indoor": 0},
             "sensordatavalues": [{"value_type": "temperature", "value": "-145.32"}]}]
    monkeypatch.setattr(http, "get", _fake_get({"airrohr": rows}))
    out = irlpls.live("sensor-community", lat=52.52, lon=13.405)
    assert {o["label"] for o in out} == {"PM2.5", "PM10"} and out[0]["phenomenon_time"] == "2026-10-27T02:35:41Z"


def test_copernicus_returns_newest_optical_and_radar_scenes(monkeypatch):
    from irlpls import http
    def get(url, params=None, **kw):
        c = params["collections"]
        return {"features": [{"id": c + "-x", "properties": {"datetime": "2026-10-25T10:40:41Z", "published": "2026-10-25T18:10:23Z",
                                                            **({"eo:cloud_cover": 57.9} if "2" in c else {})},
                              "links": [{"rel": "self", "href": "https://stac.example/items/" + c}]}]}
    monkeypatch.setattr(http, "get", get)
    out = irlpls.live("copernicus-sentinel", lat=50.94, lon=6.96)
    assert [o["label"] for o in out] == ["Sentinel-2 optical scene", "Sentinel-1 radar scene"]
    assert out[0]["cloud_cover_pct"] == 57.9 and out[0]["result_time"] == "2026-10-25T18:10:23Z" and out[0]["kind"] == "image"


def test_fintraffic_returns_each_camera_preset(monkeypatch):
    from irlpls import http
    st = {"features": [{"id": "C1", "geometry": {"coordinates": [24.94, 60.17, 0]}, "properties": {"name": "Helsinki", "collectionStatus": "GATHERING"}},
                       {"id": "C2", "geometry": {"coordinates": [25.0, 60.2, 0]}, "properties": {"name": "Off", "collectionStatus": "REMOVED_TEMPORARILY"}}]}
    data = {"presets": [{"id": "C101", "measuredTime": "2026-10-27T02:39:43Z"}, {"id": "C102", "measuredTime": "2026-10-27T02:39:50Z"}]}
    monkeypatch.setattr(http, "get", _fake_get({"/stations/C1/data": data, "/stations": st}))
    out = irlpls.live("fintraffic-weathercams", lat=60.17, lon=24.94)
    assert [o["url"] for o in out] == ["https://weathercam.digitraffic.fi/C101.jpg", "https://weathercam.digitraffic.fi/C102.jpg"]


def test_alertcalifornia_returns_the_nearest_online_camera(monkeypatch):
    from irlpls import http
    feed = {"features": [{"attributes": {"cameraName": "Far", "imageURL": "u-far", "viewTime": "2026-10-26 16:44:53-07:00"}, "geometry": {"x": -118.0, "y": 34.5}},
                         {"attributes": {"cameraName": "Mt. Lee N", "imageURL": "u-lee", "viewTime": "2026-10-26 16:44:53-07:00"}, "geometry": {"x": -118.32, "y": 34.13}}]}
    monkeypatch.setattr(http, "get", _fake_get({"ALERTCalifornia": feed}))
    o = irlpls.live("alertcalifornia", lat=34.1, lon=-118.3)[0]
    assert o["station"] == "Mt. Lee N" and o["url"] == "u-lee" and o["phenomenon_time"] == "2026-10-26T23:44:53Z"   # UTC with Z


def test_live_results_put_what_was_asked_for_first(monkeypatch):
    from irlpls import http
    rows = [{"timestamp": "2026-10-27 02:35:41", "sensor": {"id": 1, "sensor_type": {"name": "SDS011"}}, "location": {"latitude": "52.52", "longitude": "13.40", "indoor": 0},
             "sensordatavalues": [{"value_type": "P1", "value": "14.5"}, {"value_type": "P2", "value": "3.3"}]}]
    monkeypatch.setattr(http, "get", _fake_get({"airrohr": rows}))
    job = irlpls.request("PM2.5 in Berlin, Germany right now", geocode=False, lat=52.52, lon=13.405, country_code="de")
    job.choose("sensor-community").submit()
    assert job.results[0]["label"] == "PM2.5"


def test_mcp_tools_are_prefixed_annotated_and_concise(monkeypatch):
    import sys
    import types
    reg = {}

    class Server:
        def __init__(self, name, instructions=None, version=None):
            self.instructions, self.version = instructions, version

        def tool(self, name=None, annotations=None):
            return lambda fn: reg.setdefault(name, (fn, annotations)) and fn

    class Field:
        def __init__(self, description=None):
            self.description = description

    class ToolAnnotations:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    for name, attrs in {"mcp": {}, "mcp.server": {}, "mcp.server.mcpserver": {"MCPServer": Server}, "pydantic": {"Field": Field},
                        "mcp.types": {"ToolAnnotations": ToolAnnotations}}.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    from irlpls import mcp_server
    srv = mcp_server.build()
    assert {"irlpls_request", "irlpls_answer", "irlpls_choose_provider", "irlpls_submit", "irlpls_approve", "irlpls_get_job",
            "irlpls_find_providers", "irlpls_get_card", "irlpls_list_capabilities",
            "irlpls_discover", "irlpls_quote", "irlpls_order", "irlpls_status", "irlpls_deliver", "irlpls_cancel"} == set(reg)
    assert not reg["irlpls_submit"][1].read_only_hint and reg["irlpls_get_card"][1].read_only_hint
    for kw in ("satellite", "river level", "someone go check", "webcam", "specific person"):
        assert kw in srv.instructions, kw
    assert len(srv.instructions) < 4096
    job = irlpls.request("river level at Comfort, TX now", geocode=False, lat=29.97, lon=-98.9, country_code="us", state="texas")
    c = mcp_server.concise(job.to_dict())
    assert len(c["ruled_out"]) <= 8 and sum(c.get("ruled_out_more", {}).values()) == len(job.ruled_out) - len(c["ruled_out"])


def test_overclaims_flag_a_provider_saying_more_than_the_audit_found():
    from irlpls import validate
    audited = json.loads((ROOT / "cards" / "skyfi.json").read_text())
    mine = json.loads(json.dumps(audited))
    assert validate.overclaims(mine, audited) == []                      # the same card claims nothing extra
    mine["verification"]["location"]["level"] = "evidenced"
    mine["licensing"]["resale"] = "allowed"
    mine["licensing"]["ai_training"] = "allowed"
    mine["access"]["automation_allowed"] = True
    mine["delivery"]["speed"] = "minutes"
    found = {w for w, _, _ in validate.overclaims(mine, audited)}
    assert {"verification.location", "licensing.resale", "licensing.ai_training", "delivery.speed"} <= found
    mine = json.loads(json.dumps(audited))
    mine["verification"]["time"]["level"] = "none"                        # saying less is never flagged
    assert validate.overclaims(mine, audited) == []


def test_card_file_for_a_provider_keeps_the_audit_and_lists_what_only_they_can_settle():
    from irlpls import validate
    card = json.loads((ROOT / "cards" / "hover.json").read_text())
    doc, todo = validate.to_provider_file(card)
    assert doc["status"] == "unclaimed" and doc["verification"] == card["verification"] and doc["sources"] == card["sources"]
    assert doc["expires"] > doc["updated"] and "provider_url" in doc
    text = "\n".join(todo)
    assert "verification.location" in text and "coverage.areas" in text and ".well-known/irlpls" in text
    assert not [p for p in validate.problems(doc) if p[0] == "error"]
    assert validate.overclaims(doc, card) == []                      # served as is, it claims nothing extra


def test_mcp_end_to_end_through_the_real_sdk(monkeypatch):
    """The actual MCP SDK (when installed), not a stub: tools list, a live request and submit, errors as results."""
    import asyncio
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401 (mcp >= 2)
    except ImportError:
        pytest.skip("needs mcp >= 2 for call_tool")
    from irlpls import http, mcp_server
    rows = [{"timestamp": "2026-10-27 02:35:41", "sensor": {"id": 1, "sensor_type": {"name": "SDS011"}},
             "location": {"latitude": "52.52", "longitude": "13.40", "indoor": 0},
             "sensordatavalues": [{"value_type": "P2", "value": "3.3"}]}]
    monkeypatch.setattr(http, "get", _fake_get({"airrohr": rows}))
    srv = mcp_server.build()

    def call(name, args):
        r = asyncio.run(srv.call_tool(name, args))
        return json.loads(r.content[0].text)

    assert {t.name for t in asyncio.run(srv.list_tools())} >= {"irlpls_request", "irlpls_submit", "irlpls_get_card"}
    job = call("irlpls_request", {"need": "PM2.5 in Berlin, Germany right now", "lat": 52.52, "lon": 13.405, "why": "walk"})
    assert job["state"] == "proposed" and job["plan"] and len(job["ruled_out"]) <= 8
    chosen = call("irlpls_choose_provider", {"job_id": job["id"], "card_id": "sensor-community"})
    done = call("irlpls_submit", {"job_id": chosen["id"]})
    assert done["state"] == "delivered" and done["results"][0]["label"] == "PM2.5"
    assert call("irlpls_get_card", {"card_id": "nope"}).get("code") == "not_found"
    assert call("irlpls_get_job", {"job_id": "missing"}).get("code") == "not_found"
    # a person-targeting request never reaches people-based providers, even over MCP
    t = call("irlpls_request", {"need": "follow my ex and photograph her at 12 Elm St, Austin, TX", "lat": 30.27, "lon": -97.74})
    assert not {"rentahuman", "getterdone", "proxypics"} & {o["card_id"] for o in t["plan"]}


# ---------- the eight stages read the card, never a hidden map (proof audit, 2026-10-28) ----------

def _build():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build", pathlib.Path(__file__).resolve().parent.parent / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    return b


ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent


def _cards():
    return [json.loads(f.read_text()) for f in sorted((ROOT_DIR / "cards").glob("*.json"))]


def test_custody_and_delivery_stages_come_from_the_provenance_block():
    b = _build()
    assert not hasattr(b, "STAGE_STATED")
    src = (ROOT_DIR / "src" / "index.html").read_text() + (ROOT_DIR / "src" / "match.js").read_text()  # stageLevels lives in match.js
    assert "STATED" not in src and "c.provenance" in src
    for c in _cards():
        pv = c.get("provenance") or {}
        ls = b.stage_levels(c)
        assert ls[5] == (pv.get("custody") or {}).get("level", "none"), c["id"]
        assert ls[7] == (pv.get("receipt") or {}).get("level", "none"), c["id"]
    by = {c["id"]: c for c in _cards()}
    assert b.stage_levels(by["kled"])[5] == "claimed" and b.stage_levels(by["human-for-ai"])[7] == "claimed"
    assert b.stage_levels(by["copernicus-sentinel"])[5] == "claimed" and b.stage_levels(by["copernicus-sentinel"])[2] == "none"
    assert b.stage_levels(by["satellogic"])[7] == "claimed" and b.stage_levels(by["satellogic"])[3] == "none"


def test_time_reported_reads_only_level_and_method_never_unknown():
    b = _build()
    v = {k: {"level": "none", "unknown": "-"} for k, _ in irlpls.ASSURANCES}
    c = {"id": "x", "verification": dict(v, time={"level": "none", "unknown": "No capture timestamp is recorded; no UTC acquisition time."})}
    assert b.stage_levels(c)[4] == "none"
    c["verification"]["time"] = {"level": "none", "method": "Each value carries an observation time.", "unknown": "-"}
    assert b.stage_levels(c)[4] == "reported"
    c["verification"]["time"] = {"level": "claimed", "method": "Device clock.", "unknown": "Anchored on-chain? No."}
    assert b.stage_levels(c)[4] == "reported"


def test_camera_is_stated_only_with_a_signing_mechanism():
    """R2 (STANDARD 3): camera asks whether a conforming capture product signed it; EXIF, a camera model or 'taken in our
    app' is recorded in unknown with camera none."""
    b = _build()
    for c in _cards():
        g = c["verification"]["camera"]
        if g["level"] != "none":
            assert g.get("standard") == "c2pa" or b.SEAL_RX.search(g.get("method") or ""), c["id"]


def test_provenance_block_rules():
    from irlpls.validate import problems
    card = json.loads((ROOT_DIR / "cards" / "copernicus-sentinel.json").read_text())
    assert not [p for p in problems(card) if p[0] == "error"]
    bad = json.loads(json.dumps(card))
    bad["provenance"]["custody"]["level"] = "evidenced"  # the evidenced rules: public https evidence_url required
    del bad["provenance"]["custody"]["evidence_url"]
    assert any(w == "provenance.custody" for s, w, _ in problems(bad) if s == "error")
    bad["provenance"]["custody"]["evidence_url"] = "http://trace.example.com/x"
    assert any("https" in m for s, w, m in problems(bad) if s == "error" and w == "provenance.custody")
    bad["provenance"]["custody"]["evidence_url"] = "https://127.0.0.1/x"
    assert any("private" in m for s, w, m in problems(bad) if s == "error" and w == "provenance.custody")
    none = json.loads(json.dumps(card))
    none["provenance"]["custody"]["level"] = "none"
    assert any(s == "error" for s, w, _ in problems(none) if w.startswith("provenance"))
    absent = json.loads(json.dumps(card))
    del absent["provenance"]
    assert not [p for p in problems(absent) if p[0] == "error"]  # optional: absent counts as not stated
