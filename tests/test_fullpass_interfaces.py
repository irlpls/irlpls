"""Ordering and interface fixes from the 2026-10-29 full pass (docs/research/fullpass/{agent-mcp,interfaces,live-answers,
data-docs}.md). Nothing here reaches a provider: http is replaced by a recorder that fails the test on any call."""
import asyncio
import json
import pathlib

import pytest

import irlpls
from irlpls import adapters, broker, catalog, cli, http
from irlpls.adapters.base import MissingDetails, normalise, street_address, unit_of, utc_z

ROOT = pathlib.Path(__file__).resolve().parent.parent

ASKS = {
    "proxypics": ("photo of the storefront at 123 Main St, Springfield, IL today",
                  dict(place="123 Main St, Springfield, IL", lat=39.8, lon=-89.64, country_code="us", within_minutes=600)),
    "rentahuman": ("photo of the specials board at Joe's Diner, 50 Elm St, Burlington, VT today",
                   dict(place="Joe's Diner, 50 Elm St, Burlington, VT", lat=44.47, lon=-73.21, country_code="us", within_minutes=600)),
    "globhe": ("drone photos of my roof at 45 Oak Ave, Austin, TX within 3 days",
               dict(place="45 Oak Ave, Austin, TX", lat=30.27, lon=-97.74, country_code="us", within_minutes=4320)),
    "human-for-ai": ("photo of the posted hours at the library at 801 SW 10th Ave, Portland, Oregon within 2 days",
                     dict(place="801 SW 10th Ave, Portland, OR", lat=45.52, lon=-122.68, country_code="us", within_minutes=2880)),
}


@pytest.fixture
def no_net(monkeypatch, tmp_path):
    sent = []

    def boom(*a, **kw):
        sent.append((a, kw))
        raise AssertionError(f"a provider call was made: {a[:2]}")
    for name in ("get", "post", "request"):
        monkeypatch.setattr(http, name, boom)
    monkeypatch.setattr(broker, "HOME", tmp_path)
    return sent


def job_for(cid, **extra):
    need, kw = ASKS[cid]
    j = irlpls.request(need, geocode=False, **dict(kw, **extra))
    assert cid in [o["card_id"] for o in j.plan], (cid, [o["card_id"] for o in j.plan])
    return j.choose(cid)


def _placeholder(v):
    return isinstance(v, str) and v.startswith("{") and v.endswith("}")


def _agrees(ready, sent, path="$"):
    """Every value the ready call fills in (not a {PLACEHOLDER}) is sent exactly so."""
    if isinstance(ready, dict):
        for k, v in ready.items():
            if _placeholder(v) or k in ("dryRun", "idempotencyKey", "template_token"):
                continue
            assert isinstance(sent, dict) and k in sent, f"{path}.{k} is in the ready call but not sent"
            _agrees(v, sent[k], f"{path}.{k}")
    elif not _placeholder(ready):
        assert ready == sent, f"{path}: ready call {ready!r} vs sent {sent!r}"


# ---------------------------------------------------------------------------------------------- 1. one source of truth

@pytest.mark.parametrize("cid", sorted(ASKS))
def test_dry_run_sends_exactly_the_ready_call(cid, no_net):
    j = job_for(cid)
    calls = (j.choice.get("prepared_calls") or {}) or {"order": j.choice["prepared_call"]}
    j.submit()
    ws = j.quote["would_send"]
    ready = next(c for c in calls.values() if c["url"] == ws["url"])
    _agrees(ready["body"], ws["body"])
    body = json.dumps(ws["body"])
    assert ASKS[cid][0][:40] in body                       # the task text always goes out
    assert not no_net


def test_each_provider_gets_address_need_deadline(no_net):
    pp = job_for("proxypics").submit().quote["would_send"]["body"]
    assert pp["address"] == "123 Main St, Springfield, IL" and "storefront" in pp["additional_notes"] and pp["expires_at"].endswith("Z")
    gl = job_for("globhe").submit().quote
    assert not gl.get("missing") and gl["would_send"]["body"]["deliveryDeadline"] and "roof" in gl["would_send"]["body"]["description"]
    hf = job_for("human-for-ai").submit().quote
    assert not hf.get("missing") and hf["would_send"]["body"]["deadline"].endswith("Z")
    assert hf["would_send"]["body"]["location_detail"] == "801 SW 10th Ave, Portland, OR"


def test_rentahuman_never_invents_a_price_or_sends_an_empty_task(no_net):
    j = job_for("rentahuman").submit()
    body = j.quote["would_send"]["body"]
    assert "price" not in body and body["description"].startswith("photo of the specials board")
    assert body["location"]["country"] == "US"
    assert any(m.startswith("price") for m in j.quote["missing"]) and j.quote["complete"] is False
    q = next(q for q in j.questions if q["field"] == "price")
    assert q["blocking"] and q["in"] == "spec" and "spec.price" in q["hint"]
    ok = job_for("rentahuman").submit(spec={"price": 25}).quote
    assert ok["would_send"]["body"]["price"] == 25 and not ok.get("missing")
    assert ok["price"] == {"amount": "25", "currency": "USD", "firm": False}
    a = adapters.get("rentahuman")
    empty = a.commission({"title": "x"})
    assert {m.split(":")[0] for m in empty["missing"]} >= {"description", "price"}


def test_confirm_with_missing_details_sends_nothing_and_asks(no_net, monkeypatch):
    monkeypatch.setenv("PROXYPICS_API_KEY", "test-key")
    j = job_for("proxypics", place="Springfield, IL")       # a city, not a street address
    j.submit(confirm=True, idempotency_key="order-1")
    assert j.state == "quoted" and not no_net and not j.orders
    assert any(m.startswith("address") for m in j.quote["missing"])
    assert any(q["field"] == "address" and q["blocking"] for q in j.questions)
    with pytest.raises(MissingDetails):
        adapters.get("proxypics").commission({"lat": 1, "lon": 2, "notes": "a photo"}, confirm=True)
    with pytest.raises(MissingDetails):
        irlpls.commission("proxypics", {"lat": 1, "lon": 2}, confirm=True, idempotency_key="k-1")
    assert not no_net


def test_quantity_is_carried_or_refused(no_net):
    j = job_for("proxypics", quantity=100).submit()
    assert any(m.startswith("quantity") for m in j.quote["missing"])
    gl = adapters.get("globhe")
    with pytest.raises(MissingDetails):
        gl.commission({"lat": 1, "lon": 2}, confirm=True)       # no deadline, no description


def test_street_address_is_only_a_street_address():
    assert street_address("Joe's Diner, 50 Elm St, Burlington, VT") == "50 Elm St, Burlington, VT"
    assert street_address("801 SW 10th Ave, Portland, OR") == "801 SW 10th Ave, Portland, OR"
    assert street_address("Springfield, IL") is None and street_address(None) is None


# ---------------------------------------------------------------------------------------------- 2. price and default

def test_quote_always_has_a_price_field_and_the_default_is_explicit(no_net):
    j = job_for("proxypics")
    d = j.to_dict()
    assert d["best_card_id"] == "proxypics" and [o["card_id"] for o in d["plan"] if o.get("default")] == ["proxypics"]
    j.submit()
    assert "price" in j.quote and j.quote["price"] is None and "ask the provider" in j.quote["price_note"]
    g = job_for("globhe").submit().quote["price"]
    assert g["firm"] is False and g["source"] == "card" and g["currency"] == "USD" and isinstance(g["amount"], str)
    fresh = irlpls.request(*ASKS["proxypics"][:1], geocode=False, **ASKS["proxypics"][1]).to_dict()
    assert fresh["best_card_id"] and sum(1 for o in fresh["plan"] if o.get("default")) == 1


# ---------------------------------------------------------------------------------------------- 3. CLI parity

def run_cli(capsys, *args):
    try:
        cli.main(list(args))
        code = 0
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out + out.err + (code if isinstance(code, str) else "")


def test_cli_job_flow_and_messages(no_net, capsys):
    code, out = run_cli(capsys, "request", ASKS["rentahuman"][0], "--lat", "44.47", "--lon", "-73.21")
    assert code == 0 and "· default " in out and "ready call (" in out and "One sign-up by your agent" in out
    jid = out.split()[1]
    assert run_cli(capsys, "choose", jid, "rentahuman")[0] == 0
    code, out = run_cli(capsys, "submit", jid)
    assert code == 0 and "missing: price" in out and f"irlpls submit {jid} --spec" in out
    code, out = run_cli(capsys, "submit", jid, "--confirm")
    assert code and "--idempotency-key" in out and "idempotency_key=" not in out and "confirm=True" not in out
    code, out = run_cli(capsys, "approve", jid)
    assert code and f"irlpls refresh {jid}" in out and "refresh()" not in out
    code, out = run_cli(capsys, "answer", jid, "--max-order-usd", "30", "--json")
    assert code == 0 and json.loads(out)["policy"]["max_order_usd"] in (30, "30")
    code, out = run_cli(capsys, "commission", "proxypics", "--lat", "1", "--lon", "2", "--confirm")
    assert code and "--idempotency-key" in out
    code, out = run_cli(capsys, "request", "x", "--policy", "/nonexistent/policy.json")
    assert code and "can't read the policy file" in out and "Traceback" not in out
    assert not no_net


def test_cli_adapters_show_wallet_providers_as_wallet(capsys):
    code, out = run_cli(capsys, "adapters")
    rows = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    for cid in ("vangrid", "execution-market"):
        assert " wallet " in rows[cid] and "needs key" not in rows[cid]
    assert "wallet" in out.splitlines()[-1]


# ---------------------------------------------------------------------------------------------- 4. MCP

@pytest.fixture
def srv():
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        pytest.skip("needs mcp >= 2")
    from irlpls import mcp_server
    return mcp_server.build()


def call(srv, name, args):
    r = asyncio.run(srv.call_tool(name, args))
    return bool(r.is_error), json.loads(r.content[0].text), r.structured_content


def test_mcp_one_error_shape_everywhere(srv, no_net):
    cases = [("irlpls_get_job", {"job_id": "nope"}), ("irlpls_get_card", {"card_id": "nope"}),
             ("irlpls_request", {"need": "x", "quantity": "lots"}), ("irlpls_list_capabilities", {"type": "bogus"}),
             ("irlpls_quote", {"quote_request": {"request": {}}}), ("irlpls_order", {"order_request": {}})]
    for name, args in cases:
        err, body, sc = call(srv, name, args)
        assert err and sc == body, name
        assert {"code", "message", "hint"} <= set(body) and body["message"] and body["hint"], (name, body)
    _, job, _ = call(srv, "irlpls_request", {"need": ASKS["proxypics"][0], "lat": 39.8, "lon": -89.64})
    err, body, _ = call(srv, "irlpls_approve", {"job_id": job["id"]})
    assert err and body["code"] == "no_approval" and "irlpls_get_job" in body["hint"] and "refresh()" not in body["message"]
    err, body, _ = call(srv, "irlpls_submit", {"job_id": job["id"], "confirm": True})
    assert err and body["code"] == "idempotency_key_required" and "confirm=true" in body["hint"]


def test_mcp_output_schemas_and_find_shape(srv):
    from irlpls import mcp_server
    tools = asyncio.run(srv.list_tools())
    assert tools and all(t.output_schema and t.output_schema.get("type") == "object" for t in tools)
    err, out, sc = call(srv, "irlpls_find_providers", {"need": "river level at Comfort, TX now", "limit": 2})
    assert not err and isinstance(out, dict) and "results" in out and sc == out
    assert "terms|policy:<rule>" in mcp_server.JOB_DOC


def test_mcp_job_validates_against_the_job_schema(srv, no_net):
    pytest.importorskip("jsonschema")
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    reg = Registry()
    for f in list((ROOT / "schema").glob("*.schema.json")) + list((ROOT / "schema/protocol").glob("*.schema.json")):
        s = json.loads(f.read_text())
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    schema = json.loads((ROOT / "schema" / "job.schema.json").read_text())
    assert schema["additionalProperties"] is False
    v = Draft202012Validator(schema, registry=reg)
    _, job, sc = call(srv, "irlpls_request", {"need": ASKS["proxypics"][0], "lat": 39.8, "lon": -89.64})
    assert not [e.message for e in v.iter_errors(job)]
    _, done, _ = call(srv, "irlpls_submit", {"job_id": job["id"]})
    assert not [e.message for e in v.iter_errors(done)] and "price" in done["quote"]
    assert job["request"]["country_code"] == "US" and job["best_card_id"]
    assert list(v.iter_errors(dict(job, surprise=1)))


def test_agents_manual_lists_every_mcp_tool(srv):
    text = (ROOT / "docs" / "AGENTS.md").read_text()
    for t in asyncio.run(srv.list_tools()):
        assert f"`{t.name}" in text, t.name
    for field in ("read_check", "ruled_out_more", "age_s", "best_card_id", "price_note"):
        assert field in text, field


# ---------------------------------------------------------------------------------------------- 5. formats

def test_units_and_times_have_one_form():
    assert unit_of("degC") == ("°C", None, None) and unit_of("ft^3/s")[0] == unit_of("cfs")[0] == "ft³/s"
    assert unit_of("m3/s")[0] == "m³/s" and unit_of("ug/m3")[0] == "µg/m³" and unit_of("degT")[0] == "°"
    assert unit_of("m above MLLW") == ("m", "MLLW", None) and unit_of("mASD")[:2] == ("m", "stage datum")
    assert unit_of("ml", "usgs-earthquakes") == ("M", None, "ml") and unit_of("MW", "nasa-firms")[0] == "MW"
    assert utc_z("2026-10-29T07:00:00+01:00") == "2026-10-29T06:00:00Z" and utc_z("2026-10-29T05:50:00.92Z") == "2026-10-29T05:50:00.92Z"
    o = normalise({"provider": "pegelonline", "phenomenon_time": "2026-10-29T07:00:00+01:00", "unit": "m3/s", "value": 1})
    assert o["phenomenon_time"] == "2026-10-29T06:00:00Z" and o["unit"] == "m³/s" and isinstance(o["age_s"], int)


def test_sensor_networks_are_devices_and_people_are_people():
    assert broker._sensor_kind(irlpls.card("sensor-community")) == "device"
    assert broker._sensor_kind(irlpls.card("proxypics")) == "human"


def test_caltrans_returns_no_stream_link(monkeypatch):
    from irlpls.adapters import caltrans_cctv as cc
    cam = {"location": {"latitude": "38.6", "longitude": "-121.5", "locationName": "I-80", "route": "I-80"}, "inService": "true",
           "imageData": {"static": {"currentImageURL": "https://cwwp2.dot.ca.gov/x.jpg"}, "streamingVideoURL": "https://wzmedia.dot.ca.gov/x.m3u8"}}
    monkeypatch.setattr(cc, "cameras", lambda d: [cam])
    monkeypatch.setattr(cc, "captured", lambda url: None)
    res = adapters.get("caltrans-cctv").live(38.6, -121.5)
    assert res and all("video" not in o for o in res)


def test_empty_live_read_is_a_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(broker, "HOME", tmp_path)
    j = irlpls.request("PM2.5 near Portland, OR now", geocode=False, lat=45.52, lon=-122.68, country_code="us", policy={"max_order_usd": 0})
    j.choose("sensor-community")
    monkeypatch.setattr(irlpls, "live", lambda *a, **kw: adapters.base.Results([], []))
    monkeypatch.setattr(broker, "live", irlpls.live, raising=False)
    j.submit()
    assert j.state == "delivered" and any(w["code"] == "empty_read" and w["card_id"] == "sensor-community" for w in j.warnings)


# ---------------------------------------------------------------------------------------------- 6. readiness agrees

def test_readiness_follows_what_the_library_needs():
    cat = catalog.load()["providers"] if "rentahuman" in catalog.load()["providers"] else None
    from irlpls.readiness import readiness_for
    ad = catalog.adapters()
    rah = readiness_for(irlpls.card("rentahuman"), ad["rentahuman"])
    assert rah["level"] != "ready_now" and "RENTAHUMAN_API_KEY" in rah["line"] and rah["card"]["via"] == "wallet_signup"
    cop = readiness_for(irlpls.card("copernicus-sentinel"), ad["copernicus-sentinel"])
    assert cop["level"] == "ready_now" and "search" in cop["line"] and "account" in cop["line"]
    built, _ = catalog.build(list(irlpls.registry().cards))
    for pid, p in built["providers"].items():
        st, lvl = p["adapter"]["state"], p["readiness"]["level"]
        if st == "sdk-callable-with-key":
            assert p["readiness"]["via"] not in ("keyless", "wallet", "wallet_signup"), pid
        if st == "sdk-callable-no-key":
            assert lvl in ("ready_now", "contract"), pid
    del cat


def test_a_down_live_host_is_a_warning_not_a_crash(monkeypatch, tmp_path):
    import urllib.error
    monkeypatch.setattr(broker, "HOME", tmp_path)
    j = irlpls.request("PM2.5 near Portland, OR now", geocode=False, lat=45.52, lon=-122.68, country_code="us", policy={"max_order_usd": 0})
    j.choose("sensor-community")

    def down(*a, **kw):
        raise urllib.error.URLError("_ssl.c:1064: The handshake operation timed out")
    monkeypatch.setattr(irlpls, "live", down)
    j.submit()
    assert j.state == "failed" and j.warnings[0]["code"] == "provider_down" and j.warnings[0]["card_id"] == "sensor-community"
