"""A10 adapters (field + drone): GLOBHE, FlyGuys, Zeitview, Snappr and Field Nation. Offline only: irlpls.http is
replaced with fixtures built from each provider's documented examples (tests/fixtures/adapters/<card>/SOURCE.txt,
fixture: documented-shape). Nothing here can place an order: every dry run is checked to send nothing (or, for the
Zeitview preview and the Snappr coverage read, only the documented no-commit call)."""
import json
import pathlib
from decimal import Decimal

import pytest

import irlpls
import irlpls.adapters
from irlpls import catalog, http
from irlpls.adapters import field_nation, flyguys
from irlpls.adapters.base import MissingCredentials

FIX = pathlib.Path(__file__).parent / "fixtures" / "adapters"
SECRET = "real-secret-value-9f8e7d"
CARDS = ("globhe", "flyguys", "zeitview", "snappr", "field-nation")
ENV = ("GLOBHE_API_KEY", "GLOBHE_STAGING", "FLYGUYS_CLIENT_ID", "FLYGUYS_CLIENT_SECRET", "FLYGUYS_PACKAGE_ID",
       "ZEITVIEW_API_CLIENT", "ZEITVIEW_API_SECRET", "ZEITVIEW_API_BASE", "ZEITVIEW_PACKAGE_ID", "SNAPPR_API_KEY",
       "SNAPPR_SANDBOX", "FIELDNATION_CLIENT_ID", "FIELDNATION_CLIENT_SECRET", "FIELDNATION_USERNAME",
       "FIELDNATION_PASSWORD", "FIELDNATION_COMPANY_ID", "FIELDNATION_SANDBOX", "FIELDNATION_TYPE_OF_WORK_ID")


def fx(card, name):
    return json.loads((FIX / card / name).read_text())


def no_network(*a, **kw):
    raise AssertionError("no network call expected")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for f in ("get", "post", "request", "head"):
        monkeypatch.setattr(http, f, no_network)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    flyguys._TOKENS.clear()
    field_nation._TOKENS.clear()


class Net:
    """Fake irlpls.http: answers (METHOD, url-substring) routes in order; an Exception answer is raised."""

    def __init__(self, monkeypatch, routes):
        self.routes, self.sent = list(routes), []
        monkeypatch.setattr(http, "request", self.request)
        monkeypatch.setattr(http, "post", lambda url, body=None, **kw: self.request("POST", url, body=body, **kw))
        monkeypatch.setattr(http, "get", lambda url, **kw: self.request("GET", url, **kw))

    def request(self, method, url, params=None, headers=None, body=None, form=None, **kw):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None) if params else "")
        self.sent.append({"method": method, "url": full, "headers": dict(headers or {}), "body": body, "form": form})
        for i, (m, frag, ans) in enumerate(self.routes):
            if m == method and frag in url:
                self.routes.pop(i)
                if isinstance(ans, Exception):
                    raise ans
                return json.loads(json.dumps(ans))
        raise AssertionError(f"unexpected {method} {url}")


def err(status, body="", headers=None, url="https://p.example/x"):
    return http.HTTPError(status, url, json.dumps(body) if not isinstance(body, str) else body, headers or {})


REQ = {"need": "drone photos of my roof at 45 Oak Ave, Austin, TX within 3 days", "place": "45 Oak Ave, Austin, TX",
       "lat": 30.27, "lon": -97.74, "within_minutes": 4320, "quantity": 1}


def dump(x):
    return json.dumps(x, default=str)


# ------------------------------------------------------------------------------------------------- registry and cards

def test_registered_commission_only_and_cards_point_at_modules():
    ad = catalog.adapters()
    for cid in CARDS:
        a = irlpls.adapters.get(cid)
        assert a is not None and a.can_commission and not a.can_live, cid
        assert ad[cid]["commission"] and ad[cid]["status"] and ad[cid]["env"] == a.env
        assert irlpls.card(cid)["adapter"] == type(a).__module__.rsplit(".", 1)[1]
        assert not a.configured()


def test_plan_carries_ready_calls_offline():
    job = irlpls.request(REQ["need"], geocode=False, lat=REQ["lat"], lon=REQ["lon"], country_code="us")
    got = {o["card_id"]: o.get("prepared_call") for o in job.plan}
    for cid in ("globhe", "zeitview", "flyguys"):
        assert got.get(cid), cid
        assert got[cid]["safe_to_run_without_approval"] is (cid == "zeitview")   # only the Zeitview preview is a dry run


# ----------------------------------------------------------------------------------------------------------- GLOBHE

def g():
    return irlpls.adapters.get("globhe")


def test_globhe_prepare_quote_status_order():
    q = g().prepare(REQ, "commission")
    assert q["operation"] == "quote" and q["method"] == "POST" and q["url"].endswith("/api/v2/orders")
    assert q["safe_to_run_without_approval"] is False and q["auth"]["kind"] == "basic"
    b = q["body"]
    assert len(b["locationMap"]) == 4 and b["mapTypes"] == ["originalPhotos"] and len(b["orderName"]) <= 20
    assert b["deliveryDeadline"] and "placeholders" not in q
    assert g().prepare(REQ, "live") is None
    bare = g().prepare({"need": "thermal orthomosaic of a solar farm"}, "commission")
    assert bare["body"]["mapTypes"] == ["thermalMap", "_2dMap"]
    assert {p["name"] for p in bare["placeholders"]} == {"GLOBHE_AREA", "DELIVERY_DEADLINE"}
    s = g().prepare(REQ, "commission", operation="status", order_id="o1")
    assert s["operation"] == "status" and s["url"].endswith("/getorder/o1") and s["safe_to_run_without_approval"]
    o = g().prepare(REQ, "commission", operation="order")
    assert o["operation"] == "order" and o["body"] == {"status": "accept"} and not o["safe_to_run_without_approval"]
    assert "{GLOBHE_ORDER_ID}" in o["url"] and "{GLOBHE_QUOTE_ID}" in o["url"]


def test_globhe_dry_run_never_sends_the_order_request(monkeypatch):
    monkeypatch.setenv("GLOBHE_API_KEY", SECRET)
    d = g().commission({"lat": 30.27, "lon": -97.74, "need": "roof photos", "deliver_by": "2026-11-20"})
    assert d["dry_run"] and d["would_send"]["method"] == "POST" and "missing" not in d
    assert d["would_send"]["headers"]["Authorization"] == "***" and SECRET not in dump(d)
    assert "quote" not in d   # no provider price before review
    d2 = g().commission({"need": "roof photos"})
    assert {m.split(":")[0] for m in d2["missing"]} == {"lat", "within_minutes"}
    acc = g().accept_quote({"id": "o1"}, "q1")
    assert acc["dry_run"] and acc["would_send"]["body"] == {"status": "accept"}


def test_globhe_order_request_status_quotes_and_deliverables(monkeypatch):
    monkeypatch.setenv("GLOBHE_API_KEY", SECRET)
    net = Net(monkeypatch, [("POST", "/orders", fx("globhe", "create_order.json")),
                            ("POST", "/getorder/adipisicing123456789", fx("globhe", "getorder_quote.json")),
                            ("POST", "/order/adipisicing123456789/quote/20240628-2", {"status": "Quote accepted"}),
                            ("POST", "/getorder/123456789", fx("globhe", "getorder_completed.json")),
                            ("DELETE", "/orders/adipisicing123456789", fx("globhe", "cancel_order.json"))])
    order = g().commission({"lat": 30.27, "lon": -97.74, "need": "roof photos", "deliver_by": "2026-11-20"}, confirm=True)
    assert net.sent[0]["headers"]["Authorization"].startswith("Basic ") and net.sent[0]["url"].startswith(
        "https://us-central1-globhe-app.cloudfunctions.net/api/v2/")
    assert g().map_status(g().provider_status(order)) == "in_flight"
    st = g().status(order)
    assert g().provider_status(st) == "quoteReceived" and g().map_status("quoteReceived") == "in_flight"
    assert g().quotes(st) == [{"quote_id": "20240628-2", "price": 1800, "currency": "USD", "delivery_by": "20 days",
                               "expires_at": "2024-03-06T10:59:30.730+01:00", "description": st["quotes"][0]["description"]}]
    assert g().order_total(st) is None   # nothing accepted yet
    assert g().accept_quote(order, "20240628-2", confirm=True) == {"status": "Quote accepted"}
    done = g().status({"id": "123456789"})
    assert g().map_status(g().provider_status(done)) == "delivered" and g().order_total(done) == Decimal("1800")
    items = g().deliverables(done)
    assert [i["kind"] for i in items] == ["photo", "photo", "image", "image"]
    assert all(i["phenomenon_time"] is None and i["provider"] == "globhe" for i in items)
    assert items[2]["map_type"] == "_2dMap" and items[0]["lat"] == pytest.approx(89.596, abs=1e-3)
    assert g().provider_status(g().cancel(order)) == "cancelled" and g().map_status("cancelled") == "failed"
    assert SECRET not in dump(net.sent[0]["body"])


def test_globhe_status_map_and_currency():
    for s in ("reviewing", "pendingOperator", "operatorAssigned", "flyingDateSet", "flyingDateRescheduled"):
        assert g().map_status(s) == "in_flight", s
    for s in ("orderNotFeasible", "rejected", "deleted"):
        assert g().map_status(s) == "failed", s
    assert g().map_status("somethingNew") is None
    assert g().order_total(fx("globhe", "getorder_accepted_sek.json")) is None   # SEK is never a USD total


def test_globhe_staging_host(monkeypatch):
    monkeypatch.setenv("GLOBHE_API_KEY", SECRET)
    monkeypatch.setenv("GLOBHE_STAGING", "1")
    net = Net(monkeypatch, [("POST", "/getorder/x", fx("globhe", "getorder_quote.json"))])
    g().status("x")
    assert net.sent[0]["url"].startswith("https://us-central1-globhe-d3087.cloudfunctions.net/api/v2/")


# ---------------------------------------------------------------------------------------------------------- Zeitview

def z():
    return irlpls.adapters.get("zeitview")


def zkeys(monkeypatch, pkg=True):
    monkeypatch.setenv("ZEITVIEW_API_CLIENT", "client-123")
    monkeypatch.setenv("ZEITVIEW_API_SECRET", SECRET)
    monkeypatch.setenv("ZEITVIEW_API_BASE", "https://zeitview-host.example")
    if pkg:
        monkeypatch.setenv("ZEITVIEW_PACKAGE_ID", "rre-premium")


def test_zeitview_prepare():
    q = z().prepare(REQ, "commission")
    assert q["operation"] == "quote" and q["url"] == "{ZEITVIEW_API_BASE}/v2/partners/missions/preview"
    assert q["safe_to_run_without_approval"] is True and q["headers"]["X-Api-Secret"] == "{ZEITVIEW_API_SECRET}"
    assert q["body"]["package"] == {"id": "{ZEITVIEW_PACKAGE_ID}"}
    assert {"ZEITVIEW_API_BASE", "ZEITVIEW_PACKAGE_ID"} <= {p["name"] for p in q["placeholders"]}
    o = z().prepare(REQ, "commission", operation="order")
    assert o["url"].endswith("/v1/partners/missions/") and o["body"]["external_id"] == "{EXTERNAL_ID}"
    assert not o["safe_to_run_without_approval"]
    s = z().prepare(REQ, "commission", operation="status", order_id="DBM1")
    assert s["method"] == "GET" and s["url"].endswith("/v1/partners/missions/DBM1")


def test_zeitview_dry_run_without_keys_sends_nothing():
    d = z().commission({"lat": 30.27, "lon": -97.74, "need": "roof", "package_id": "rre-premium"})
    assert d["dry_run"] and "quote" not in d and "missing" not in d
    d2 = z().commission({"need": "roof"})
    assert [m.split(":")[0] for m in d2["missing"]] == ["package_id", "address"]


def test_zeitview_dry_run_runs_only_the_preview(monkeypatch):
    zkeys(monkeypatch)
    net = Net(monkeypatch, [("POST", "/v2/partners/missions/preview", fx("zeitview", "preview_v2.json"))])
    d = z().commission({"lat": 30.27, "lon": -97.74, "need": "roof"})
    assert [s["url"] for s in net.sent] == ["https://zeitview-host.example/v2/partners/missions/preview"]
    assert net.sent[0]["headers"] == {"X-Api-Client": "client-123", "X-Api-Secret": SECRET}
    assert d["dry_run"] and d["quote"]["airspace"] == "ok" and d["quote"]["available_slots"] == 2 and d["quote"]["slots"] == 3
    assert SECRET not in dump(d) and "client-123" not in dump(d)
    from irlpls.protocol.shim import AdapterShim
    assert AdapterShim._dry_total(d) == (None, False)   # no price: never a firm offer


def test_zeitview_preview_airspace_rejection(monkeypatch):
    zkeys(monkeypatch)
    Net(monkeypatch, [("POST", "/preview", err(422, fx("zeitview", "preview_422.json")))])
    d = z().commission({"lat": 33.94, "lon": -118.40, "need": "LAX"})
    assert d["quote"] == {"airspace": "rejected", "errors": {"error": {"location": "This property is within protected airspace"}}}


def test_zeitview_order_status_deliverables_cancel(monkeypatch):
    zkeys(monkeypatch)
    net = Net(monkeypatch, [("POST", "/v1/partners/missions/", fx("zeitview", "mission_created.json")),
                            ("GET", "/v1/partners/missions/DBM0114440", fx("zeitview", "mission_complete.json")),
                            ("GET", "/images", fx("zeitview", "images.json")),
                            ("DELETE", "/v1/partners/missions/DBM0114440", {})])
    m = z().commission({"lat": 30.27, "lon": -97.74, "need": "roof", "external_id": "ext-1"}, confirm=True)
    assert net.sent[0]["body"]["external_id"] == "ext-1" and net.sent[0]["body"]["package"] == {"id": "rre-premium"}
    assert z().map_status(z().provider_status(m)) == "in_flight"
    done = z().status(m)
    assert z().map_status(z().provider_status(done)) == "delivered"
    items = z().deliverables(done)
    assert [i["kind"] for i in items] == ["photo", "image", "report"]
    assert items[0]["phenomenon_time"] == "2021-11-01T13:40:04+00:00" and items[0]["lat"] == pytest.approx(34.9006, abs=1e-3)
    page = z().images(m, per_page=5000)
    assert "per_page=1000" in net.sent[2]["url"] and z().deliverables(page)[0]["phenomenon_time"] == "2024-06-01T10:23:45Z"
    z().cancel(m)
    assert net.sent[-1]["method"] == "DELETE"
    hook = fx("zeitview", "webhook_canceled.json")
    assert z().map_status(z().provider_status(hook)) == "failed"


def test_zeitview_order_needs_package(monkeypatch):
    zkeys(monkeypatch, pkg=False)
    with pytest.raises(ValueError, match="package_id"):
        z().commission({"lat": 1, "lon": 2}, confirm=True)


def test_zeitview_status_map_is_the_documented_list():
    assert set(z().status_map) == {"created", "confirmed", "scheduling_call", "pilots_notified", "pilot_accepted",
                                   "pilot_arrived", "pilot_departed", "assets_uploaded", "assets_classified",
                                   "in_production", "complete", "put_on_hold", "resumed", "rescheduled", "canceled"}


# ------------------------------------------------------------------------------------------------------------ Snappr

def s_():
    return irlpls.adapters.get("snappr")


def test_snappr_prepare_read_availability_order_status():
    r = s_().prepare(REQ, "commission")
    assert r["operation"] == "read" and r["method"] == "GET" and r["safe_to_run_without_approval"]
    assert r["url"] == "https://api.snappr.com/coverage?latitude=30.27&longitude=-97.74"
    assert r["headers"] == {"Authorization": "Bearer {SNAPPR_API_KEY}", "accept-version": "1.0.0"}
    a = s_().prepare(REQ, "commission", operation="availability")
    assert "shoottype={SNAPPR_SHOOTTYPE}" in a["url"] and "date={DATE}" in a["url"]
    o = s_().prepare(REQ, "commission", operation="order", shoottype="real-estate", duration=60)
    assert o["method"] == "POST" and o["url"].endswith("/bookings") and o["body"]["start_at"] is None
    assert o["body"]["shoottype"] == "real-estate" and not o["safe_to_run_without_approval"]
    assert {"CUSTOMER_FIRSTNAME", "CUSTOMER_EMAIL", "CUSTOMER_MOBILEPHONE"} <= {p["name"] for p in o["placeholders"]}
    st = s_().prepare(REQ, "commission", operation="status", order_id="u1")
    assert st["url"] == "https://api.snappr.com/bookings/u1"
    noplace = s_().prepare({"need": "headshots"}, "commission")
    assert "address={ADDRESS}" in noplace["url"]


def test_snappr_dry_run_reads_coverage_only(monkeypatch):
    spec = {"lat": 34.0522, "lon": -118.2437, "shoottype": "event", "duration": 120}
    d = s_().commission(spec)   # no key: nothing at all
    assert d["dry_run"] and "quote" not in d
    assert [m.split(":")[0] for m in d["missing"]] == ["customer_firstname", "customer_email", "customer_mobilephone"]
    monkeypatch.setenv("SNAPPR_API_KEY", SECRET)
    net = Net(monkeypatch, [("GET", "/coverage", fx("snappr", "coverage.json"))])
    d = s_().commission(spec)
    assert [x["method"] for x in net.sent] == ["GET"] and "shoottype=event" in net.sent[0]["url"]
    assert d["quote"] == {"coverage": True, "available_providers": 12, "shoottype": "event"}
    assert d["would_send"]["headers"]["Authorization"] == "***" and SECRET not in dump(d)


def test_snappr_booking_status_images(monkeypatch):
    monkeypatch.setenv("SNAPPR_API_KEY", SECRET)
    net = Net(monkeypatch, [("POST", "/bookings", fx("snappr", "booking_created.json")),
                            ("GET", "/bookings/5e3c1a2b-0d4f-4a7e-9b1c-2f3e4d5c6b7a/images", fx("snappr", "images.json")),
                            ("GET", "/bookings/5e3c1a2b-0d4f-4a7e-9b1c-2f3e4d5c6b7a", fx("snappr", "booking_completed.json")),
                            ("GET", "/availability", fx("snappr", "availability.json"))])
    spec = {"lat": 34.0522, "lon": -118.2437, "shoottype": "event", "duration": 120, "start_at": "2018-12-01T07:30:00Z",
            "customer_firstname": "Mary", "customer_email": "test@snappr.com", "customer_mobilephone": "+14153339966"}
    b = s_().commission(spec, confirm=True)
    assert net.sent[0]["headers"]["Authorization"] == "Bearer " + SECRET and net.sent[0]["headers"]["accept-version"] == "1.0.0"
    assert s_().map_status(s_().provider_status(b)) == "in_flight" and b["credits"] == 300
    assert s_().order_total is None   # credits are not dollars: no USD total is ever reported
    imgs = s_().images(b)
    done = s_().status(b)
    assert s_().map_status(s_().provider_status(done)) == "delivered"
    assert [i["label"] for i in s_().deliverables(done)] == ["gallery (ZIP)"]
    assert [i["url"] for i in s_().deliverables(imgs)] == [r["url_original"] for r in imgs["results"]]
    av = s_().availability("2018-12-01", "event", 120, lat=34.0522, lon=-118.2437)
    assert av["available_times"] and "duration=120" in net.sent[-1]["url"]
    assert s_().map_status("paid_pending_schedule") == "in_flight" and s_().map_status("cancelled") == "failed"
    assert s_().map_status("refunded") is None


def test_snappr_sandbox_host(monkeypatch):
    monkeypatch.setenv("SNAPPR_API_KEY", SECRET)
    monkeypatch.setenv("SNAPPR_SANDBOX", "1")
    net = Net(monkeypatch, [("GET", "/coverage", fx("snappr", "coverage.json"))])
    s_().coverage(34.0, -118.0)
    assert net.sent[0]["url"].startswith("https://sandbox.snappr.com/coverage")


# ----------------------------------------------------------------------------------------------------------- FlyGuys

def f():
    return irlpls.adapters.get("flyguys")


def test_flyguys_prepare():
    o = f().prepare(REQ, "commission")
    assert o["operation"] == "order" and o["url"] == "https://api.flyguys.com/Jobs" and o["auth"]["kind"] == "oauth2"
    assert "https://api.flyguys.com/token" in o["notes"] and o["body"]["package"] == {"id": "{FLYGUYS_PACKAGE_ID}"}
    assert o["body"]["location"]["coordinate"] == {"latitude": 30.27, "longitude": -97.74}
    s = f().prepare(REQ, "commission", operation="status", order_id="j1")
    assert s["method"] == "GET" and s["url"].endswith("/Jobs/j1") and s["safe_to_run_without_approval"]
    assert f().prepare(REQ, "commission", operation="quote") is None   # no quote endpoint


def test_flyguys_dry_run_and_order(monkeypatch):
    d = f().commission({"lat": 30.22, "lon": -92.01, "need": "aerial photos"})
    assert d["dry_run"] and d["missing"][0].startswith("package_id")
    monkeypatch.setenv("FLYGUYS_CLIENT_ID", "fg-client")
    monkeypatch.setenv("FLYGUYS_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("FLYGUYS_PACKAGE_ID", "demo-catalog-aerial")
    d = f().commission({"lat": 30.22, "lon": -92.01, "need": "aerial photos"})
    assert "missing" not in d and SECRET not in dump(d)
    net = Net(monkeypatch, [("POST", "/token", fx("flyguys", "token.json")),
                            ("POST", "/Jobs", fx("flyguys", "job_created.json")),
                            ("GET", "/Jobs/fcc27cc8-98a6-4b86-8934-76445d4001b4", fx("flyguys", "job_get.json")),
                            ("GET", "/Files/fcc27cc8", fx("flyguys", "files.json"))])
    job = f().commission({"lat": 30.22, "lon": -92.01, "need": "aerial photos"}, confirm=True)
    tok = net.sent[0]
    assert tok["form"] == {"grant_type": "client_credentials", "client_id": "fg-client", "client_secret": SECRET}
    assert net.sent[1]["headers"] == {"Authorization": "Bearer fg-access-token-abc", "client_id": "fg-client"}
    assert net.sent[1]["body"]["package"] == {"id": "demo-catalog-aerial"} and net.sent[1]["body"]["external_id"]
    assert f().map_status(f().provider_status(job)) == "in_flight"
    st = f().status(job)
    assert f().provider_status(st) == "confirmed" and f().map_status("confirmed") == "in_flight"
    assert f().files(job)["fileList"][0]["file_name"] == "1711662.JPG"
    assert len([x for x in net.sent if "/token" in x["url"]]) == 1   # token cached
    assert f().map_status("complete") is None   # not in FlyGuys' docs: left unmapped


# ------------------------------------------------------------------------------------------------------ Field Nation

def fn():
    return irlpls.adapters.get("field-nation")


def fnkeys(monkeypatch):
    for k, v in (("FIELDNATION_CLIENT_ID", "fn-client"), ("FIELDNATION_CLIENT_SECRET", SECRET),
                 ("FIELDNATION_USERNAME", "api-user"), ("FIELDNATION_PASSWORD", "pw-123"), ("FIELDNATION_COMPANY_ID", "777")):
        monkeypatch.setenv(k, v)


def test_field_nation_prepare():
    o = fn().prepare(REQ, "commission")
    assert o["operation"] == "order" and o["url"] == ("https://api.fieldnation.com/api/rest/v2/workorders"
                                                      "?access_token={FIELDNATION_ACCESS_TOKEN}")
    b = o["body"]
    assert b["location"] == {"mode": "custom", "address1": "45 Oak Ave, Austin, TX"} and b["require_gps"] is True
    assert b["schedule"]["service_window"]["mode"] == "between" and b["pay"]["base"]["amount"] == "{PAY_USD}"
    assert b["types_of_work"][0]["id"] == "{FIELDNATION_TYPE_OF_WORK_ID}" and not o["safe_to_run_without_approval"]
    assert "/authentication/api/oauth/token" in o["notes"]
    s = fn().prepare(REQ, "commission", operation="status", order_id="12345")
    assert s["url"].endswith("/workorders/12345?access_token={FIELDNATION_ACCESS_TOKEN}") and s["safe_to_run_without_approval"]


def test_field_nation_dry_run_lists_missing():
    d = fn().commission({"address": "123 Main St", "need": "photo the storefront"})
    assert d["dry_run"] and "access_token=***" in d["would_send"]["url"]
    assert [m.split(":")[0] for m in d["missing"]] == ["type_of_work_id", "within_minutes", "pay_usd"]


def test_field_nation_order_publish_status_deliverables_cancel(monkeypatch):
    fnkeys(monkeypatch)
    net = Net(monkeypatch, [("POST", "/authentication/api/oauth/token", fx("field-nation", "token.json")),
                            ("POST", "/workorders/12345/publish", fx("field-nation", "publish.json")),
                            ("POST", "/workorders", fx("field-nation", "workorder_draft.json")),
                            ("GET", "/workorders/12345", fx("field-nation", "workorder_assigned.json")),
                            ("GET", "/workorders/12345", fx("field-nation", "workorder_approved.json")),
                            ("DELETE", "/workorders/12345", {})])
    spec = {"location": {"address1": "123 Main St", "city": "Phoenix", "state": "AZ", "zip": "85001", "country": "US"},
            "need": "photo the storefront", "type_of_work_id": 62, "pay_usd": 40, "start": "2026-11-02 15:00:00"}
    wo = fn().commission(spec, confirm=True)
    tok = net.sent[0]
    assert tok["body"] == {"grant_type": "password", "client_id": "fn-client", "client_secret": SECRET,
                           "username": "api-user", "password": "pw-123"}
    create = net.sent[1]
    assert create["url"].endswith("/api/rest/v2/workorders?access_token=eyJ0eXAiOiJKV1QiLCJhbG-fn-token")
    assert create["headers"] == {"x-fn-company-id": "777"}
    assert create["body"]["schedule"] == {"service_window": {"mode": "exact", "start": {"utc": "2026-11-02 15:00:00"}}}
    assert net.sent[2]["url"].split("?")[0].endswith("/workorders/12345/publish") and wo["publish"]["status"]["name"] == "Published"
    a = fn().status(wo)
    assert fn().provider_status(a) == "assigned" and fn().map_status("assigned") == "in_flight"   # sub-status "On My Way", id 3
    done = fn().status(wo)
    assert fn().map_status(fn().provider_status(done)) == "delivered"
    items = fn().deliverables(done)
    assert [(i["kind"], i["phenomenon_time"]) for i in items] == [("report", None), ("photo", None)]
    assert items[1]["uploaded_at"] == "2026-01-15T14:05:00Z" and items[1]["label"] == "Completion Photos: storefront.jpg"
    fn().cancel(wo, reason="Duplicate")
    assert net.sent[-1]["method"] == "DELETE" and "cancel_reason=Duplicate" in net.sent[-1]["url"]
    assert fn().map_status("Work Done") == "in_flight" and fn().map_status("Cancelled") == "failed"
    assert fn().provider_status({"status": {"id": 10, "name": "Deleted"}}) == "Deleted"


def test_field_nation_errors_never_carry_the_token(monkeypatch):
    fnkeys(monkeypatch)
    tok = fx("field-nation", "token.json")["access_token"]
    leak = f"https://api.fieldnation.com/api/rest/v2/workorders/9?access_token={tok}"
    Net(monkeypatch, [("POST", "/oauth/token", fx("field-nation", "token.json")),
                      ("GET", "/workorders/9", err(404, f"not found: {leak}", url=leak)),
                      ("GET", "/workorders/9", err(429, "slow down", {"Retry-After": "60"}, url=leak))])
    with pytest.raises(http.HTTPError) as e:
        fn().status({"id": 9})
    assert e.value.status == 404 and tok not in str(e.value) and tok not in e.value.body and tok not in e.value.url
    with pytest.raises(http.HTTPError) as e:
        fn().status({"id": 9})
    assert e.value.status == 429 and e.value.retry_after == 60 and tok not in str(e.value)


# ------------------------------------------------------------------------------------------------- shared error rules

@pytest.mark.parametrize("cid,setup,call", [
    ("globhe", lambda m: m.setenv("GLOBHE_API_KEY", SECRET), lambda a: a.status("o1")),
    ("zeitview", lambda m: zkeys(m), lambda a: a.status("DBM1")),
    ("snappr", lambda m: m.setenv("SNAPPR_API_KEY", SECRET), lambda a: a.status("u1")),
])
def test_error_mapping(monkeypatch, cid, setup, call):
    setup(monkeypatch)
    a = irlpls.adapters.get(cid)
    method = "POST" if cid == "globhe" else "GET"
    Net(monkeypatch, [(method, "", err(401, {"code": "Unauthorized"})), (method, "", err(403, {"error": "Unauthorized"})),
                      (method, "", err(404, {"error": "Not Found"})), (method, "", err(429, {"code": "RateLimit"}, {"Retry-After": "30"}))])
    for _ in range(2):
        with pytest.raises(MissingCredentials) as e:
            call(a)
        assert SECRET not in str(e.value)
    with pytest.raises(http.HTTPError) as e:
        call(a)
    assert e.value.status == 404 and e.value.retry_after is None
    with pytest.raises(http.HTTPError) as e:
        call(a)
    assert e.value.status == 429 and e.value.retry_after == 30


def test_flyguys_errors(monkeypatch):
    monkeypatch.setenv("FLYGUYS_CLIENT_ID", "fg-client")
    monkeypatch.setenv("FLYGUYS_CLIENT_SECRET", SECRET)
    Net(monkeypatch, [("POST", "/token", err(401, "invalid_client"))])
    with pytest.raises(MissingCredentials):
        f().status("j1")
    Net(monkeypatch, [("POST", "/token", fx("flyguys", "token.json")),
                      ("GET", "/Jobs/j1", err(404, fx("flyguys", "job_404.json"))),
                      ("GET", "/Jobs/j1", err(429, "", {"Retry-After": "5"})),
                      ("GET", "/Jobs/j1", err(401, ""))])
    with pytest.raises(http.HTTPError) as e:
        f().status("j1")
    assert e.value.status == 404 and "Mission Id not found" in e.value.body
    with pytest.raises(http.HTTPError) as e:
        f().status("j1")
    assert e.value.retry_after == 5
    with pytest.raises(MissingCredentials):
        f().status("j1")


def test_snappr_provider_error_code(monkeypatch):
    monkeypatch.setenv("SNAPPR_API_KEY", SECRET)
    Net(monkeypatch, [("POST", "/bookings", err(400, fx("snappr", "error_nocoverage.json")))])
    spec = {"address": "1 Remote Rd", "shoottype": "event", "duration": 60, "customer_firstname": "A",
            "customer_email": "a@b.c", "customer_mobilephone": "+1"}
    with pytest.raises(http.HTTPError) as e:
        s_().commission(spec, confirm=True)
    assert e.value.status == 400 and e.value.provider_code == "NoCoverage"


def test_missing_credentials_raise_before_any_call():
    with pytest.raises(MissingCredentials):
        g().status("o1")
    with pytest.raises(MissingCredentials):
        z().status("m1")
    with pytest.raises(MissingCredentials):
        s_().status("u1")
    with pytest.raises(MissingCredentials):
        f().status("j1")
    with pytest.raises(MissingCredentials):
        fn().status("w1")


def test_keys_never_appear_in_ready_calls_or_dry_runs(monkeypatch):
    zkeys(monkeypatch)
    fnkeys(monkeypatch)
    for k in ("GLOBHE_API_KEY", "SNAPPR_API_KEY", "FLYGUYS_CLIENT_SECRET"):
        monkeypatch.setenv(k, SECRET)
    monkeypatch.setenv("FLYGUYS_CLIENT_ID", "fg-client")
    Net(monkeypatch, [("POST", "/preview", fx("zeitview", "preview_v2.json")), ("GET", "/coverage", fx("snappr", "coverage.json"))])
    for cid in CARDS:
        a = irlpls.adapters.get(cid)
        for op in ("read", "quote", "order", "status", "availability"):
            pc = a.prepare(REQ, "commission", operation=op)
            assert SECRET not in dump(pc) and "pw-123" not in dump(pc), (cid, op)
        d = a.commission(a.spec_from_request(REQ, "ext-1", "2026-11-20T00:00:00Z"))
        assert d["dry_run"] and SECRET not in dump(d) and "pw-123" not in dump(d), cid


def test_shim_quotes_are_dry_and_never_firm():
    from irlpls.protocol import shim as S
    body = {"request": {"need": "drone photos of the roof at 45 Oak Ave, Austin, TX", "place": "45 Oak Ave, Austin, TX",
                        "lat": 30.27, "lon": -97.74}, "constraints": {"deliver_by": "2026-11-20T00:00:00Z"}}
    r = S.AdapterShim("globhe").quote(body)
    (o,) = r.body["offers"]
    assert r.status == 201 and o["firm"] is False and "total" not in o
    ws = o["x-com.irlpls.shim"]
    assert ws["dry_run"] and ws["would_send"]["body"]["deliveryDeadline"] == "2026-11-20"
    with pytest.raises(S.Problem) as e:   # Zeitview cannot be asked without a package id
        S.AdapterShim("zeitview").quote(body)
    assert e.value.code == "needs_input" and e.value.body["questions"][0]["field"] == "package_id"
