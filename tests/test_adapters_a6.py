"""A6 adapters (OAuth + MCP): Quest and Airtasker over MCP Streamable HTTP (private irlpls/adapters/_mcp.py) and Hover
over REST with an authorization-code token. Offline only: the MCP transport and irlpls.http are replaced with fixtures
(tests/fixtures/adapters/<card>/, see each SOURCE.txt)."""
import json
import pathlib

import pytest

import irlpls
import irlpls.adapters
from irlpls import catalog, http
from irlpls.adapters import _mcp
from irlpls.adapters.base import MissingCredentials, NotSupported
from irlpls.need import Request

FIX = pathlib.Path(__file__).parent / "fixtures" / "adapters"
SECRET = "real-secret-token-value"


def fx(card, name):
    return (FIX / card / name).read_text()


class FakeMCP:
    """Stands in for _mcp._post: answers initialize, the initialized notification and one tools/call per fixture."""

    def __init__(self, tool_answers, session_id="sess-1", sse=()):
        self.answers, self.session_id, self.sse, self.sent = dict(tool_answers), session_id, set(sse), []

    def __call__(self, url, body, headers):
        self.sent.append((url, body, dict(headers)))
        m = body.get("method")
        if m == "initialize":
            h = {"content-type": "application/json"}
            if self.session_id:
                h["mcp-session-id"] = self.session_id
            return 200, h, fx("quest", "initialize.json")
        if m == "notifications/initialized":
            return 202, {}, ""
        name = body["params"]["name"]
        ans = self.answers[name]
        if isinstance(ans, Exception):
            raise ans
        return 200, {"content-type": "text/event-stream" if name in self.sse else "application/json"}, ans

    def tool_calls(self):
        return [b for _, b, _ in self.sent if b.get("method") == "tools/call"]


def no_network(*a, **kw):
    raise AssertionError("no network call expected")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(_mcp, "_post", no_network)
    for f in ("get", "post", "request", "head"):
        monkeypatch.setattr(http, f, no_network)
    for k in ("QUEST_API_KEY", "AIRTASKER_ACCESS_TOKEN", "HOVER_ACCESS_TOKEN", "HOVER_SANDBOX"):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------------------------- _mcp transport

def test_parse_response_json_sse_and_errors():
    assert _mcp.parse_response('{"jsonrpc":"2.0","id":3,"result":{"ok":1}}', "application/json", 3) == {"ok": 1}
    sse = 'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\ndata: {"jsonrpc":"2.0","id":3,"result":{"ok":2}}\n\n'
    assert _mcp.parse_response(sse, "text/event-stream; charset=utf-8", 3) == {"ok": 2}
    with pytest.raises(_mcp.MCPError, match="-32602"):
        _mcp.parse_response('{"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"bad args"}}', "application/json", 3)
    with pytest.raises(_mcp.MCPError, match="no JSON-RPC response"):
        _mcp.parse_response('{"jsonrpc":"2.0","id":9,"result":{}}', "application/json", 3)
    with pytest.raises(_mcp.MCPError, match="not JSON"):
        _mcp.parse_response("<html>", "text/html", 3)


def test_tool_result_prefers_structured_content_and_raises_tool_errors():
    assert _mcp.tool_result({"structuredContent": {"a": 1}, "content": [{"type": "text", "text": "x"}]}) == {"a": 1}
    assert _mcp.tool_result({"content": [{"type": "text", "text": '{"b": 2}'}]}) == {"b": 2}
    assert _mcp.tool_result({"content": [{"type": "text", "text": "plain"}]}) == "plain"
    with pytest.raises(_mcp.ToolError, match="nope"):
        _mcp.tool_result({"isError": True, "content": [{"type": "text", "text": "nope"}]})


def test_session_handshake_carries_session_id_and_protocol_version():
    fake = FakeMCP({"whoAmI": json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"userID": "u1"}}})})
    assert _mcp.call_tool("https://mcp.example/mcp", SECRET, "whoAmI", send=fake) == {"userID": "u1"}
    (u0, init, h0), (_, note, h1), (_, call, h2) = fake.sent
    assert init["method"] == "initialize" and init["params"]["protocolVersion"] == _mcp.PROTOCOL_VERSION
    assert "Mcp-Session-Id" not in h0 and "MCP-Protocol-Version" not in h0
    assert note == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert h1["Mcp-Session-Id"] == h2["Mcp-Session-Id"] == "sess-1" and h2["MCP-Protocol-Version"] == "2025-06-18"
    assert h2["Accept"] == "application/json, text/event-stream" and h2["Authorization"] == "Bearer " + SECRET
    assert call == {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoAmI", "arguments": {}}}
    # a server that issues no session id: no header is sent
    fake2 = FakeMCP({"whoAmI": json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": []}})}, session_id=None)
    _mcp.call_tool("https://mcp.example/mcp", SECRET, "whoAmI", send=fake2)
    assert all("Mcp-Session-Id" not in h for _, _, h in fake2.sent)


def test_transport_refuses_unsafe_urls_before_sending(monkeypatch):
    monkeypatch.undo()   # the real _post, which checks the URL before opening anything
    monkeypatch.delenv("IRLPLS_ALLOW_PRIVATE_HOSTS", raising=False)
    for url in ("http://mcp.example.com/mcp", "https://127.0.0.1/mcp", "file:///etc/passwd"):
        with pytest.raises(http.UnsafeURL):
            _mcp._post(url, {}, {})


def test_auth_errors_map_and_others_propagate_with_retry_after():
    with pytest.raises(MissingCredentials, match="HTTP 401") as ei:
        _mcp.raise_for_auth("x", http.HTTPError(401, "https://a/mcp", "invalid_token"), "Sign in again.")
    assert SECRET not in str(ei.value)
    with pytest.raises(MissingCredentials, match="HTTP 403"):
        _mcp.raise_for_auth("x", http.HTTPError(403, "https://a/mcp", "insufficient_scope"), "")
    with pytest.raises(http.HTTPError) as ei:
        _mcp.raise_for_auth("x", http.HTTPError(429, "https://a/mcp", "slow down", headers={"Retry-After": "30"}), "")
    assert ei.value.status == 429 and _mcp.retry_after(ei.value) == 30
    with pytest.raises(http.HTTPError) as ei:
        _mcp.raise_for_auth("x", http.HTTPError(404, "https://a/mcp", "not found"), "")
    assert ei.value.status == 404 and _mcp.retry_after(ei.value) is None


# --------------------------------------------------------------------------------------------- registry

def test_a6_adapters_are_registered_with_matching_modes():
    ads = catalog.adapters()
    for cid, env in (("quest", "QUEST_API_KEY"), ("airtasker", "AIRTASKER_ACCESS_TOKEN"), ("hover", "HOVER_ACCESS_TOKEN")):
        a = irlpls.adapters.get(cid)
        assert a and a.card_id == cid and a.env == env and a.can_commission and not a.can_live
        assert "commission" in irlpls.card(cid)["delivery"]["modes"]
        assert ads[cid]["commission"] and ads[cid]["env"] == env
    assert not ads["quest"]["status"]          # Quest has no status tool yet
    assert ads["airtasker"]["status"] and ads["hover"]["status"]


def test_broker_hook_builds_ready_calls_offline(monkeypatch):
    from irlpls import broker
    monkeypatch.setenv("QUEST_API_KEY", SECRET)
    req = Request(need="photo of the shelf at Plaza Singapura", place="68 Orchard Road, Singapore", country_code="sg",
                  lat=1.3005, lon=103.8452)
    for cid in ("quest", "airtasker", "hover"):
        pc = broker._prepared(irlpls.adapters.get(cid), req, "commission")
        assert pc and pc["operation"] == "order" and pc["safe_to_run_without_approval"] is False, cid
        assert SECRET not in repr(pc)


# --------------------------------------------------------------------------------------------- Quest

SG_REQ = {"need": "Check whether the blue ceramic mugs are on the shelf at the Daiso in Plaza Singapura",
          "place": "Plaza Singapura, 68 Orchard Road, Singapore", "country_code": "sg", "lat": 1.30053, "lon": 103.84522,
          "within_minutes": 2880, "quantity": 1}


def test_quest_prepares_post_and_browse_calls_without_the_key(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", SECRET)
    q = irlpls.adapters.get("quest")
    pc = q.prepare(SG_REQ, "commission")
    assert pc["method"] == "POST" and pc["url"] == "https://mcp.hireahuman.quest/mcp" and pc["operation"] == "order"
    assert pc["headers"]["Authorization"] == "Bearer {QUEST_API_KEY}" and pc["auth"]["kind"] == "bearer"
    assert pc["auth"]["get_key_url"] == "https://hireahuman.quest/profile/api-keys"
    assert pc["body"]["method"] == "tools/call" and pc["body"]["params"]["name"] == "postQuest"
    a = pc["body"]["params"]["arguments"]
    assert a["startingCountry"] == "Singapore" and a["startingAddress"] == SG_REQ["place"]
    assert a["price"] == "{PRICE}" and a["numOffers"] == 1 and a["paymentRecurrence"] == "one time payment"
    assert a["dueDateType"] == "before" and a["dueDate"].endswith("T00:00:00Z") and a["scopeOfReach"] == "local"
    assert [p["name"] for p in pc["placeholders"]] == ["PRICE"] and "SGD" in pc["placeholders"][0]["what"]
    assert "initialize" in pc["notes"] and pc["safe_to_run_without_approval"] is False
    assert SECRET not in repr(pc)

    rd = q.prepare(SG_REQ, "commission", operation="read")
    assert rd["operation"] == "read" and rd["safe_to_run_without_approval"] is True
    assert rd["body"]["params"] == {"name": "getQuestsV2", "arguments": {"countryCode": "SG", "location": "in-person",
                                    "lat": 1.3005, "lng": 103.8452, "radius": 5000, "sortOrder": "nearby"}}
    assert q.prepare(SG_REQ, "live") is None and q.prepare(SG_REQ, "commission", operation="status") is None

    us = q.prepare({"need": "photo of a storefront", "place": "1 Market St, San Francisco", "country_code": "us",
                    "max_price_usd": 25}, "commission")
    ua = us["body"]["params"]["arguments"]
    assert ua["price"] == 25 and ua["startingCountry"] == "United States" and "placeholders" not in us


def test_quest_dry_run_sends_nothing_without_a_key_and_lists_what_is_missing():
    q = irlpls.adapters.get("quest")
    out = q.commission({"title": "Shelf photo", "description": "Photo of the shelf", "country": "SG"})
    assert out["dry_run"] and out["would_send"]["body"]["params"]["name"] == "postQuest"
    assert any(m.startswith("price") for m in out["missing"]) and any(m.startswith("address") for m in out["missing"])
    assert out["would_send"]["headers"]["Authorization"] == "***" and "going_rates" not in out
    with pytest.raises(ValueError, match="price"):
        q.commission({"title": "x", "country": "SG"}, confirm=True)   # never posts an incomplete quest


def test_quest_dry_run_with_key_adds_going_rates_but_never_a_firm_total(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", SECRET)
    fake = FakeMCP({"getQuestsV2": fx("quest", "getQuestsV2.sse")}, sse={"getQuestsV2"})
    monkeypatch.setattr(_mcp, "_post", fake)
    out = irlpls.adapters.get("quest").commission({"title": "Shelf photo", "description": "Photo of the shelf", "country": "SG",
                                                   "address": "68 Orchard Road", "price": 15, "lat": 1.3, "lon": 103.85})
    assert out["dry_run"] and "missing" not in out
    assert out["going_rates"] == {"currency": "SGD", "count": 3, "median": 15, "min": 12, "max": 20}
    assert "quote" not in out and "total" not in repr(out)
    assert [b["params"]["name"] for b in fake.tool_calls()] == ["getQuestsV2"]   # postQuest never sent
    assert SECRET not in repr(out)


def test_quest_confirm_posts_and_maps_the_documented_statuses(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", SECRET)
    fake = FakeMCP({"postQuest": fx("quest", "postQuest.json")})
    monkeypatch.setattr(_mcp, "_post", fake)
    q = irlpls.adapters.get("quest")
    spec = {"title": "Shelf photo", "description": "Photo of the shelf", "country": "SG", "address": "68 Orchard Road",
            "price": 12, "due_date": "2026-10-30T00:00:00Z", "time_range": "3pm to 6pm"}
    resp = q.commission(spec, confirm=True)
    assert resp == {"status": "pending", "questID": "q-555"}
    args = fake.tool_calls()[0]["params"]["arguments"]
    assert args["price"] == 12 and args["timeRange"] == "3pm to 6pm" and args["startingCountry"] == "Singapore"
    assert q.map_status(q.provider_status(resp)) == "in_flight"
    for s in ("duplicate", "maximum price exceeded", "illegal-content", "auto-delete", "admin-delete", "user-banned"):
        assert q.map_status(s) == "failed", s
    assert q.map_status("something new") is None
    monkeypatch.setattr(_mcp, "_post", FakeMCP({"postQuest": fx("quest", "postQuest_max_price.json")}))
    r2 = q.commission(spec, confirm=True)
    assert r2["maxPrice"] == 500 and q.map_status(q.provider_status(r2)) == "failed"
    with pytest.raises(NotSupported):
        q.status(resp)   # no status tool yet ("coming soon")


@pytest.mark.parametrize("code,exc", [(401, MissingCredentials), (403, MissingCredentials), (404, http.HTTPError),
                                      (429, http.HTTPError)])
def test_quest_http_errors(monkeypatch, code, exc):
    monkeypatch.setenv("QUEST_API_KEY", SECRET)
    err = http.HTTPError(code, "https://mcp.hireahuman.quest/mcp", "err", headers={"Retry-After": "12"} if code == 429 else None)
    monkeypatch.setattr(_mcp, "_post", FakeMCP({"getQuestsV2": err}))
    with pytest.raises(exc) as ei:
        irlpls.adapters.get("quest").going_rates("SG")
    assert SECRET not in str(ei.value)
    if code == 429:
        assert _mcp.retry_after(ei.value) == 12


def test_quest_missing_key_is_reported():
    with pytest.raises(MissingCredentials, match="QUEST_API_KEY"):
        irlpls.adapters.get("quest").commission({"title": "x", "description": "y", "country": "SG", "address": "a",
                                                 "price": 10}, confirm=True)


# --------------------------------------------------------------------------------------------- Airtasker

SYD_REQ = {"need": "Photo of the shopfront and opening-hours sign at a cafe on King Street", "place": "Newtown, NSW, Australia",
           "country_code": "au", "lat": -33.89712, "lon": 151.17934, "within_minutes": 4320}


def test_airtasker_prepares_every_operation_with_the_oauth_flow_named(monkeypatch):
    monkeypatch.setenv("AIRTASKER_ACCESS_TOKEN", SECRET)
    at = irlpls.adapters.get("airtasker")
    pc = at.prepare(SYD_REQ, "commission")
    assert pc["operation"] == "order" and pc["url"] == "https://mcp.airtasker.com/mcp" and pc["auth"]["kind"] == "oauth2"
    assert pc["headers"]["Authorization"] == "Bearer {AIRTASKER_ACCESS_TOKEN}"
    oa = pc["auth"]["oauth"]
    assert oa["grant"] == "authorization_code" and oa["pkce"] == "S256"
    assert oa["authorize_url"] == "https://id.airtasker.com/authorize" and oa["token_url"] == "https://id.airtasker.com/oauth/token"
    assert oa["registration_url"] == "https://id.airtasker.com/oidc/register"
    assert oa["scopes"] == ["airtasker:read", "airtasker:write"]   # never asks for the payments scope
    a = pc["body"]["params"]["arguments"]
    assert pc["body"]["params"]["name"] == "post_task"
    assert a["location_type"] == {"type": "in_person", "latitude": -33.9, "longitude": 151.18, "display_name": "{SUBURB}"}
    assert a["price"] == "{PRICE}" and a["date_type"] == "before_date" and len(a["timeless_date"]) == 10
    assert {p["name"] for p in pc["placeholders"]} == {"PRICE", "SUBURB"}
    assert "accept_offer" in pc["notes"] and pc["safe_to_run_without_approval"] is False

    ops = {op: at.prepare(SYD_REQ, "commission", operation=op) for op in ("read", "quote", "status")}
    assert ops["read"]["body"]["params"]["name"] == "browse_tasks" and ops["read"]["safe_to_run_without_approval"]
    assert ops["quote"]["body"]["params"] == {"name": "get_offers", "arguments": {"task_id": "{AIRTASKER_TASK_ID}"}}
    assert ops["status"]["body"]["params"]["name"] == "get_my_tasks"
    for op in ops.values():
        assert "unverified" in op["notes"] and op["auth"]["oauth"]["authorize_url"]
    assert at.prepare(SYD_REQ, "live") is None and at.prepare(SYD_REQ, "commission", operation="cancel") is None
    assert SECRET not in repr([pc, ops])


def test_airtasker_dry_run_and_confirm(monkeypatch):
    at = irlpls.adapters.get("airtasker")
    out = at.commission({"title": "Shopfront photo", "description": "Photo of the shopfront", "lat": -33.897, "lon": 151.179})
    assert out["dry_run"] and {m.split(":")[0] for m in out["missing"]} == {"price", "suburb"}
    with pytest.raises(ValueError):
        at.commission({"title": "x", "lat": 1, "lon": 2}, confirm=True)
    monkeypatch.setenv("AIRTASKER_ACCESS_TOKEN", SECRET)
    fake = FakeMCP({"post_task": fx("airtasker", "post_task.json"), "get_my_tasks": fx("airtasker", "get_my_tasks.json")})
    monkeypatch.setattr(_mcp, "_post", fake)
    spec = {"title": "Shopfront photo on King Street", "description": "Photo of the shopfront and its opening-hours sign",
            "lat": -33.89712, "lon": 151.17934, "suburb": "Newtown", "price": 40, "date": "2026-10-31"}
    order = at.commission(spec, confirm=True)
    assert order["id"] == "at-9001"
    args = fake.tool_calls()[0]["params"]["arguments"]
    assert args["location_type"]["latitude"] == -33.9 and args["price"] == 40 and args["timeless_date"] == "2026-10-31"
    assert all(b["params"]["name"] != "accept_offer" for b in fake.tool_calls())
    st = at.status(order)
    assert st == {"id": "at-9001", "title": "Photo of a shopfront", "state": "open"}
    assert at.map_status("open") is None    # Airtasker documents no task statuses: nothing mapped, stays in flight
    assert at.deliverables(st) == []        # no tool to read proof
    assert SECRET not in repr([order, st])


def test_airtasker_remote_tasks_and_flexible_dates():
    a = irlpls.adapters.get("airtasker")._post_args({"title": "t", "description": "d", "remote": True, "price": 30})
    assert a["location_type"] == {"type": "remote"} and a["date_type"] == "flexible" and "timeless_date" not in a


def test_airtasker_tool_error_is_raised_as_data_not_followed(monkeypatch):
    monkeypatch.setenv("AIRTASKER_ACCESS_TOKEN", SECRET)
    fake = FakeMCP({"post_task": fx("airtasker", "tool_error.json")})
    monkeypatch.setattr(_mcp, "_post", fake)
    with pytest.raises(_mcp.ToolError, match="at least 25 characters"):
        irlpls.adapters.get("airtasker").commission({"title": "t", "description": "d", "remote": True, "price": 30}, confirm=True)
    assert [b["params"]["name"] for b in fake.tool_calls()] == ["post_task"]   # nothing else was called


@pytest.mark.parametrize("code,exc", [(401, MissingCredentials), (403, MissingCredentials), (404, http.HTTPError),
                                      (429, http.HTTPError)])
def test_airtasker_http_errors(monkeypatch, code, exc):
    monkeypatch.setenv("AIRTASKER_ACCESS_TOKEN", SECRET)
    err = http.HTTPError(code, "https://mcp.airtasker.com/mcp", "err", headers={"Retry-After": "7"} if code == 429 else None)
    monkeypatch.setattr(_mcp, "_post", FakeMCP({"get_my_tasks": err}))
    with pytest.raises(exc) as ei:
        irlpls.adapters.get("airtasker").status({"id": "at-1"})
    assert SECRET not in str(ei.value)
    if code == 429:
        assert _mcp.retry_after(ei.value) == 7


# --------------------------------------------------------------------------------------------- Hover

AUS_REQ = {"need": "roof measurements of my house at 45 Oak Ave, Austin, TX", "place": "45 Oak Ave, Austin, TX",
           "country_code": "us", "lat": 30.27, "lon": -97.74}


def test_hover_prepares_the_capture_request_and_names_the_auth_code_flow(monkeypatch):
    monkeypatch.setenv("HOVER_ACCESS_TOKEN", SECRET)
    h = irlpls.adapters.get("hover")
    pc = h.prepare(AUS_REQ, "commission")
    assert pc["method"] == "POST" and pc["url"] == "https://hover.to/api/v2/capture_requests" and pc["operation"] == "order"
    assert pc["headers"] == {"Authorization": "Bearer {HOVER_ACCESS_TOKEN}"} and pc["auth"]["kind"] == "oauth2"
    cr = pc["body"]["capture_request"]
    assert cr["capturing_user_name"] == "{CAPTURING_USER_NAME}" and cr["capturing_user_email"] == "{CAPTURING_USER_EMAIL}"
    assert cr["job_attributes"]["location_line_1"] == "45 Oak Ave, Austin, TX" and cr["job_attributes"]["deliverable_id"] == 2
    assert pc["body"]["current_user_email"] == "{HOVER_USER_EMAIL}"
    assert {p["name"] for p in pc["placeholders"]} == {"CAPTURING_USER_NAME", "CAPTURING_USER_EMAIL", "HOVER_USER_EMAIL"}
    oa = pc["auth"]["oauth"]
    assert oa["grant"] == "authorization_code" and oa["scopes"] == ["all"] and oa["token_url"] == "https://hover.to/oauth/token"
    assert oa["authorize_url"].startswith("https://hover.to/oauth/authorize?response_type=code&client_id={HOVER_CLIENT_ID}")
    assert oa["token_body"]["grant_type"] == "authorization_code" and oa["refresh_body"]["grant_type"] == "refresh_token"
    assert pc["safe_to_run_without_approval"] is False and SECRET not in repr(pc)

    st = h.prepare(AUS_REQ, "commission", operation="status")
    assert st["method"] == "GET" and st["url"].startswith("https://hover.to/api/v2/capture_requests/{HOVER_CAPTURE_REQUEST_ID}/details")
    assert st["safe_to_run_without_approval"] is True
    assert h.prepare(AUS_REQ, "live") is None and h.prepare(AUS_REQ, "commission", operation="quote") is None

    monkeypatch.setenv("HOVER_SANDBOX", "1")
    sb = h.prepare({"need": "photos of the house"}, "commission")
    assert sb["url"].startswith("https://sandbox.hover.to/") and sb["auth"]["oauth"]["token_url"].startswith("https://sandbox.hover.to/")
    assert sb["body"]["capture_request"]["job_attributes"] == {"location_line_1": "{ADDRESS}", "deliverable_id": 7,
                                                                "name": "photos of the house"}


def test_hover_deliverable_choice():
    from irlpls.adapters.hover import deliverable_for
    assert deliverable_for("roof measurements") == 2 and deliverable_for("interior floor plan") == 8
    assert deliverable_for("photos of the house") == 7 and deliverable_for("3d model of the exterior") == 3


def test_hover_dry_run_then_confirm(monkeypatch):
    h = irlpls.adapters.get("hover")
    out = h.commission({"address": "45 Oak Ave"})
    assert out["dry_run"] and len(out["missing"]) == 3 and out["would_send"]["headers"]["Authorization"] == "***"
    with pytest.raises(ValueError):
        h.commission({"address": "45 Oak Ave"}, confirm=True)
    monkeypatch.setenv("HOVER_ACCESS_TOKEN", SECRET)
    sent = []

    def post(url, body=None, headers=None, **kw):
        sent.append((url, body, headers))
        return json.loads(fx("hover", "capture_request_created.json"))
    monkeypatch.setattr(http, "post", post)
    spec = {"address": "123 Hover Way", "city": "Los Angeles", "region": "CA", "postal_code": "90210",
            "capturing_user_name": "Lucille Bluth", "capturing_user_email": "homeowner@hoverinc.com",
            "current_user_id": 1153372, "deliverable_id": 3, "external_identifier": "irl-1"}
    order = h.commission(spec, confirm=True)
    assert order["id"] == 1521803 and h.map_status(h.provider_status(order)) == "in_flight"
    url, body, headers = sent[0]
    assert url == "https://hover.to/api/v2/capture_requests" and headers == {"Authorization": "Bearer " + SECRET}
    assert body["current_user_id"] == 1153372 and "current_user_email" not in body
    assert body["capture_request"]["job_attributes"]["external_identifier"] == "irl-1"


def test_hover_status_follows_the_capture_request_then_the_job(monkeypatch):
    monkeypatch.setenv("HOVER_ACCESS_TOKEN", SECRET)
    calls = []

    def get(url, params=None, headers=None, **kw):
        calls.append((url, params))
        return json.loads(fx("hover", "capture_request_details.json" if "capture_requests" in url else "job_completed.json"))
    monkeypatch.setattr(http, "get", get)
    h = irlpls.adapters.get("hover")
    st = h.status({"id": 1521803})
    assert calls == [("https://hover.to/api/v2/capture_requests/1521803/details", {"id": 1521803}),
                     ("https://hover.to/api/v3/jobs/10168587", None)]
    assert h.provider_status(st) == "completed" and h.map_status("completed") == "delivered"
    res = h.deliverables(st)
    assert [r["kind"] for r in res] == ["measurements", "measurements", "photo", "photo"]   # the failed model is left out
    assert all(r["phenomenon_time"] is None and r["provider"] == "hover" for r in res)
    assert res[0]["url"].endswith("/models/15081683/artifacts/measurements.json") and res[0]["lat"] == pytest.approx(34.8785)
    assert res[2]["stored_at"] == "2025-03-16T23:56:03.946Z" and SECRET not in repr(st)


def test_hover_status_mapping_follows_the_docs():
    h = irlpls.adapters.get("hover")
    assert h.map_status(h.provider_status({"capture_request": {"state": "new"}})) == "in_flight"
    assert h.map_status(h.provider_status({"capture_request": {"state": "complete"}})) == "in_flight"   # scanned, not built
    assert h.map_status(h.provider_status({"capture_request": {"state": "deleted"}})) == "failed"
    assert h.map_status(h.provider_status({"capture_request": {"state": "complete"}, "job": {"reconstruction_state": "failed"}})) == "failed"
    assert h.map_status("working") == "in_flight" and h.map_status("uploading") is None
    assert h.deliverables({"job": {"reconstruction_state": "working", "models": []}}) == []


def test_hover_cancel_deletes_the_capture_request(monkeypatch):
    monkeypatch.setenv("HOVER_ACCESS_TOKEN", SECRET)
    sent = []
    monkeypatch.setattr(http, "request", lambda m, url, **kw: sent.append((m, url)))
    h = irlpls.adapters.get("hover")
    assert h.can_cancel and h.cancel({"id": 77}) == {"id": 77, "cancelled": True}
    assert sent == [("DELETE", "https://hover.to/api/v2/capture_requests/77")]


@pytest.mark.parametrize("code,exc", [(401, MissingCredentials), (403, MissingCredentials), (404, http.HTTPError),
                                      (429, http.HTTPError)])
def test_hover_http_errors(monkeypatch, code, exc):
    monkeypatch.setenv("HOVER_ACCESS_TOKEN", SECRET)

    def get(url, **kw):
        raise http.HTTPError(code, url, "err", headers={"Retry-After": "60"} if code == 429 else None)
    monkeypatch.setattr(http, "get", get)
    with pytest.raises(exc) as ei:
        irlpls.adapters.get("hover").status({"id": 1})
    assert SECRET not in str(ei.value)
    if code == 401:
        assert "refresh" in str(ei.value)
    if code == 429:
        assert _mcp.retry_after(ei.value) == 60


def test_hover_missing_token():
    with pytest.raises(MissingCredentials, match="HOVER_ACCESS_TOKEN"):
        irlpls.adapters.get("hover").status({"id": 1})
