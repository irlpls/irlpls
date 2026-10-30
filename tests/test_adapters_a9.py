"""A9 contract-satellite adapters: ICEYE, Satellogic, Capella, Vantor Hub, Open Cosmos DataCosmos. Offline only: every
HTTP call goes through a fake irlpls.http.request fed with documented-shape fixtures (tests/fixtures/adapters/<card>/)."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import irlpls.adapters
from irlpls import catalog, http
from irlpls.adapters import _a9
from irlpls.adapters.base import MissingCredentials, NotSupported
from irlpls.protocol.shim import AdapterShim

FIX = Path(__file__).parent / "fixtures" / "adapters"
IDS = ("iceye", "satellogic", "capella", "vantor-hub", "open-cosmos-datacosmos")
SECRET, TOKEN = "sek-SECRET-9f3a", "tok-SECRET-77c1"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
ASK = {"need": "radar image of the Port of Rotterdam, Netherlands tonight", "place": "Rotterdam, Netherlands",
       "lat": 51.95, "lon": 4.14, "within_minutes": 4320, "quantity": 1}

ENV = {"ICEYE_CLIENT_ID": "iceye-id-" + SECRET, "ICEYE_CLIENT_SECRET": SECRET, "ICEYE_TOKEN_URL": "https://auth.iceye.example/token",
       "ICEYE_CONTRACT_ID": "c-123", "SATELLOGIC_CLIENT_ID": "sat-id-" + SECRET, "SATELLOGIC_CLIENT_SECRET": SECRET,
       "SATELLOGIC_CONTRACT_ID": "cont.1", "SATELLOGIC_SKU": "TSKPOI-M", "CAPELLA_API_KEY": "cap-" + SECRET,
       "VANTOR_API_KEY": "van-" + SECRET, "VANTOR_END_USE_CODE": "AGR", "OPEN_COSMOS_CLIENT_ID": "oc-id-" + SECRET,
       "OPEN_COSMOS_CLIENT_SECRET": SECRET, "OPEN_COSMOS_CONTRACT_ID": "456", "OPEN_COSMOS_ORGANISATION_ID": "42"}


def fx(card, name):
    return json.loads((FIX / card / name).read_text())


def ad(cid):
    return irlpls.adapters.ADAPTERS[cid]


class Fake:
    """Routes (METHOD, url prefix) to a fixture or an HTTPError; records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, params=None, headers=None, body=None, form=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body, "form": form})
        for (m, prefix), out in self.routes.items():
            if m == method and url.startswith(prefix):
                if isinstance(out, http.HTTPError):
                    raise out
                return json.loads(json.dumps(out))
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(_a9, "now", lambda: NOW)
    for cid in IDS:
        tc = getattr(ad(cid), "_tokens", None)
        if tc:
            tc.clear()
    monkeypatch.setattr(http, "request", Fake({}))   # any unexpected network call fails the test
    yield


@pytest.fixture
def creds(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def route(monkeypatch, routes):
    f = Fake(routes)
    monkeypatch.setattr(http, "request", f)
    return f


def err(status, headers=None):
    return http.HTTPError(status, "https://provider.example/x", '{"code": "nope"}', headers=headers)


def no_secret(obj):
    s = json.dumps(obj, default=str)
    assert SECRET not in s and TOKEN not in s


TOKENS = {("POST", "https://auth.iceye.example/token"): {"access_token": TOKEN, "expires_in": 3600},
          ("POST", "https://auth.platform.satellogic.com/oauth/token"): {"access_token": TOKEN, "expires_in": 86400},
          ("POST", "https://login.open-cosmos.com/oauth/token"): {"access_token": TOKEN, "expires_in": 86400}}


# ------------------------------------------------------------------------------------------------ registry + ready calls

def test_registered_commission_only_with_named_credentials():
    cat = catalog.adapters()
    for cid in IDS:
        a = ad(cid)
        assert a.can_commission and not a.can_live
        assert cat[cid]["commission"] and cat[cid]["env"] == a.env
        assert irlpls.card(cid)["adapter"] == type(a).__module__.rsplit(".", 1)[1]
    assert not cat["vantor-hub"]["status"]   # no status() until the V2 URLs are verified


EXPECT = {  # card -> {operation: (method, url start, auth kind)}
    "iceye": {"quote": ("GET", "https://platform.iceye.com/api/tasking/v2/price?contractID={ICEYE_CONTRACT_ID}&", "oauth2"),
              "order": ("POST", "https://platform.iceye.com/api/tasking/v2/tasks", "oauth2"),
              "status": ("GET", "https://platform.iceye.com/api/tasking/v2/tasks/{ICEYE_TASK_ID}", "oauth2")},
    "satellogic": {"quote": ("POST", "https://api.satellogic.com/v2/analysis/opportunities", "oauth2"),
                   "order": ("POST", "https://api.satellogic.com/v2/orders", "oauth2"),
                   "status": ("GET", "https://api.satellogic.com/v2/orders/{SATELLOGIC_ORDER_ID}", "oauth2")},
    "capella": {"order": ("POST", "https://api.capellaspace.com/task", "api_key_header"),
                "status": ("GET", "https://api.capellaspace.com/task/{CAPELLA_TASKING_REQUEST_ID}", "api_key_header")},
    "vantor-hub": {"quote": ("POST", "https://api.maxar.com/tasking/v2/tasking/vantor/estimate", "api_key_header"),
                   "order": ("POST", "https://api.maxar.com/tasking/v2/tasking/vantor", "api_key_header"),
                   "status": ("GET", "https://api.maxar.com/tasking/v2/tasking/{VANTOR_TASKING_ID}", "api_key_header")},
    "open-cosmos-datacosmos": {
        "quote": ("GET", "https://app.open-cosmos.com/api/data/v0/order/price?collection={OPEN_COSMOS_COLLECTION}", "oauth2"),
        "order": ("POST", "https://app.open-cosmos.com/api/data/v0/order/orders", "oauth2"),
        "status": ("GET", "https://app.open-cosmos.com/api/data/v0/order/orders/{OPEN_COSMOS_ORDER_ID}", "oauth2")},
}


@pytest.mark.parametrize("cid", IDS)
def test_prepare_every_operation_offline_and_without_secrets(cid, creds):
    a = ad(cid)
    for op, (method, url, kind) in EXPECT[cid].items():
        pc = a.prepare(dict(ASK), "commission", operation=op)
        assert pc["operation"] == op and pc["method"] == method and pc["url"].startswith(url), (cid, op, pc["url"])
        assert pc["auth"]["kind"] == kind and pc["auth"]["placeholder"] in json.dumps(pc["headers"])
        assert pc["safe_to_run_without_approval"] is (op == "status")
        assert pc["docs_url"].startswith("https://") and pc["auth"]["get_key_url"].startswith("https://")
        if kind == "oauth2":
            assert "client_credentials" in pc["notes"] and "Token:" in pc["notes"]
        for p in pc.get("placeholders") or []:
            assert "{" + p["name"] + "}" in json.dumps(pc), (cid, op, p["name"])
        no_secret(pc)
        assert "c-123" not in json.dumps(pc) and "cont.1" not in json.dumps(pc)   # account values stay placeholders
    assert a.prepare(dict(ASK), "live") is None
    assert a.prepare(dict(ASK), "commission", operation="cancel") is None
    assert len(http.request.calls) == 0


def test_prepare_defaults_and_point_requirement():
    assert ad("iceye").prepare(ASK, "commission")["operation"] == "quote"
    assert ad("satellogic").prepare(ASK, "commission")["operation"] == "quote"
    assert ad("capella").prepare(ASK, "commission")["operation"] == "order"   # no safe quote exists
    assert ad("vantor-hub").prepare(ASK, "commission")["operation"] == "quote"
    assert ad("open-cosmos-datacosmos").prepare(ASK, "commission")["operation"] == "quote"
    for cid in ("iceye", "satellogic", "capella", "vantor-hub"):
        assert ad(cid).prepare({"need": "x"}, "commission") is None


def test_ready_calls_carry_documented_bodies():
    t = ad("iceye").prepare(ASK, "commission", operation="order")["body"]
    assert t["pointOfInterest"] == {"lat": 51.95, "lon": 4.14} and t["imagingMode"] == "SPOTLIGHT"
    s, e = (datetime.fromisoformat(t["acquisitionWindow"][k].replace("Z", "+00:00")) for k in ("start", "end"))
    assert (s - NOW).total_seconds() >= 12 * 3600 and (e - s).total_seconds() >= 24 * 3600 and (e - NOW).days < 14
    c = ad("capella").prepare(ASK, "commission")
    assert c["body"]["properties"]["preApproval"] is False and c["body"]["geometry"]["coordinates"] == [4.14, 51.95]
    assert "no quote call" in c["notes"]
    v = ad("vantor-hub").prepare(ASK, "commission")
    assert v["body"]["geometry"] == {"latitude": 51.95, "longitude": 4.14, "buffer": "8x8"}
    assert v["body"]["product_details"]["licensing"]["end_use_code"] == "{VANTOR_END_USE_CODE}" and "UNVERIFIED" in v["notes"]
    assert v["headers"] == {"maxar-api-key": "{VANTOR_API_KEY}"}
    sat = ad("satellogic").prepare(ASK, "commission")
    assert sat["headers"]["x-satellogic-contract-id"] == "{SATELLOGIC_CONTRACT_ID}" and sat["body"]["properties"]["sku"] == "{SATELLOGIC_SKU}"


def test_broker_plan_carries_the_ready_call_without_network():
    job = irlpls.request("new radar satellite image of the Port of Rotterdam, Netherlands within 3 days", geocode=False,
                         lat=51.95, lon=4.14, country_code="nl")
    ours = [o for o in job.plan if o["card_id"] in IDS]
    assert ours and all(o.get("prepared_call", {}).get("card_id") == o["card_id"] for o in ours)
    assert len(http.request.calls) == 0


# ------------------------------------------------------------------------------------------------------------- ICEYE

def test_iceye_dry_run_without_credentials_sends_nothing():
    a = ad("iceye")
    assert not a.configured()
    d = a.commission({"lat": 1.0, "lon": 2.0, "contract_id": "c-9"})
    assert d["dry_run"] and d["would_send"]["body"]["contractID"] == "c-9" and "quote" not in d
    assert a.commission({"lat": 1.0, "lon": 2.0})["missing"] == ["contract_id"]
    assert len(http.request.calls) == 0


def test_iceye_quote_is_firm_only_in_usd_and_token_uses_basic(monkeypatch, creds):
    f = route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/price"): fx("iceye", "price.json"),
                            ("POST", "https://platform.iceye.com/api/tasking/v2/feasibility"): fx("iceye", "feasibility.json")})
    d = ad("iceye").commission({"lat": 51.95, "lon": 4.14, "reference": "ext-1"})
    assert d["dry_run"] and d["quote"]["total"] == 5291.99 and d["quote"]["currency"] == "USD"
    assert d["feasibility"]["feasibility"][0]["result"] == "FEASIBLE"
    tok = f.calls[0]
    assert tok["form"] == {"grant_type": "client_credentials"} and tok["headers"]["Authorization"].startswith("Basic ")
    price = f.calls[1]
    assert "contractID=c-123" in price["url"] and "pointOfInterest[lat]=51.95" in price["url"]
    assert price["headers"]["Authorization"] == "Bearer " + TOKEN and f.calls[2]["body"][0]["reference"] == "ext-1"
    assert not [c for c in f.calls if c["url"].endswith("/tasking/v2/tasks")]   # nothing ordered
    no_secret(d)
    monkeypatch.setattr(http, "request", Fake({**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/price"): fx("iceye", "price_eur.json"),
                                               ("POST", "https://platform.iceye.com/api/tasking/v2/feasibility"): fx("iceye", "feasibility.json")}))
    q = ad("iceye").commission({"lat": 51.95, "lon": 4.14})["quote"]
    assert "total" not in q and q == {"amount_minor": 450000, "currency": "EUR", "source": "GET /tasking/v2/price",
                                      "provider_total": 4500.0}   # shown in EUR (major units), never firm
    from irlpls.adapters.base import quote_price
    assert quote_price({"quote": q}) == (Decimal("4500.0"), "EUR", False)


@pytest.mark.parametrize("status,code", [(401, "refused"), (403, "refused"), (404, "not_found"), (429, "rate_limited"), (500, "provider_error")])
def test_iceye_quote_errors_become_warnings(monkeypatch, creds, status, code):
    route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/price"): err(status, {"Retry-After": "120"}),
                        ("POST", "https://platform.iceye.com/api/tasking/v2/feasibility"): fx("iceye", "feasibility.json")})
    d = ad("iceye").commission({"lat": 51.95, "lon": 4.14})
    w = d["warnings"][0]
    assert d["dry_run"] and "quote" not in d and w["code"] == code and w["status"] == status
    if status == 429:
        assert w["retry_after_s"] == 120 and "retry after 120 s" in w["msg"]
    assert "nope" not in json.dumps(w) and "provider.example" not in json.dumps(w)   # body and URL never copied


def test_iceye_order_status_deliverables_cancel(monkeypatch, creds):
    tid = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXX"
    T = "https://platform.iceye.com/api/tasking/v2/tasks"
    f = route(monkeypatch, {**TOKENS, ("POST", T): fx("iceye", "task_created.json"),
                            ("GET", f"{T}/{tid}/scene"): fx("iceye", "scene.json"),
                            ("GET", f"{T}/{tid}/products"): fx("iceye", "products.json"),
                            ("GET", f"{T}/{tid}"): fx("iceye", "task_fulfilled.json"),
                            ("PATCH", f"{T}/{tid}"): {"id": tid, "status": "CANCELED"}})
    a = ad("iceye")
    order = a.commission({"lat": 60.1699, "lon": 24.9384, "idempotency_key": "k-1"}, confirm=True)
    sent = f.calls[-1]
    assert sent["method"] == "POST" and sent["body"]["contractID"] == "c-123" and sent["body"]["reference"] == "k-1"
    assert a.map_status(a.provider_status(order)) == "in_flight"
    st = a.status(order)
    assert a.map_status(a.provider_status(st)) == "delivered"
    [o] = a.deliverables(st)
    assert o["phenomenon_time"] == "2025-11-08T19:55:19Z" and o["label"] == "GRD" and o["url"].startswith("https://download.iceye.com/")
    assert (o["lat"], o["lon"]) == (60.1699, 24.9384)
    assert a.cancel(order)["status"] == "CANCELED" and f.calls[-1]["body"] == {"status": "CANCELED"}
    for raw, want in (("ACTIVE", "in_flight"), ("DONE", "delivered"), ("REJECTED", "failed"), ("CANCELED", "failed"),
                      ("FAILED", "failed"), ("SOMETHING_NEW", None)):
        assert a.map_status(raw) == want


def test_iceye_status_401_is_missing_credentials_and_404_raises(monkeypatch, creds):
    route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/tasks/t1"): err(401)})
    with pytest.raises(MissingCredentials) as e:
        ad("iceye").status({"id": "t1"})
    assert SECRET not in str(e.value)
    route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/tasks/t2"): err(404)})
    with pytest.raises(http.HTTPError) as e:
        ad("iceye").status({"id": "t2"})
    assert e.value.status == 404
    route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/tasks/t3"): err(429, {"Retry-After": "30"})})
    with pytest.raises(http.HTTPError) as e:
        ad("iceye").status({"id": "t3"})
    assert e.value.retry_after_s == 30


def test_iceye_refused_token_is_missing_credentials(monkeypatch, creds):
    route(monkeypatch, {("POST", "https://auth.iceye.example/token"): err(401)})
    with pytest.raises(MissingCredentials):
        ad("iceye").commission({"lat": 1.0, "lon": 2.0}, confirm=True)


def test_shim_quote_iceye_is_firm_in_usd(monkeypatch, creds):
    route(monkeypatch, {**TOKENS, ("GET", "https://platform.iceye.com/api/tasking/v2/price"): fx("iceye", "price.json"),
                        ("POST", "https://platform.iceye.com/api/tasking/v2/feasibility"): fx("iceye", "feasibility.json")})
    offer = AdapterShim("iceye").quote({"request": ASK}).body["offers"][0]
    assert offer["firm"] is True and offer["total"]["amount"] == "5291.99"
    no_secret(offer)


# -------------------------------------------------------------------------------------------------------- Satellogic

def test_satellogic_dry_run_runs_opportunities_only(monkeypatch, creds):
    f = route(monkeypatch, {**TOKENS, ("POST", "https://api.satellogic.com/v2/analysis/opportunities"): fx("satellogic", "analysis_created.json")})
    d = ad("satellogic").commission({"lat": 51.95, "lon": 4.14})
    assert d["dry_run"] and d["opportunities"] == {"analysis_id": 8132} and "quote" not in d
    tok, opp = f.calls
    assert tok["form"]["audience"] == "https://api.satellogic.com/" and tok["form"]["grant_type"] == "client_credentials"
    assert opp["headers"]["x-satellogic-contract-id"] == "cont.1" and opp["body"]["properties"]["sku"] == "TSKPOI-M"
    assert opp["body"]["geometry"] == {"type": "Point", "coordinates": [4.14, 51.95]}
    no_secret(d)


def test_satellogic_missing_sku_and_no_creds(monkeypatch):
    d = ad("satellogic").commission({"lat": 1.0, "lon": 2.0})
    assert d["missing"] == ["sku"] and len(http.request.calls) == 0
    assert "opportunities" not in ad("satellogic").commission({"lat": 1.0, "lon": 2.0, "sku": "X"})


def test_satellogic_quote_rate_limit_is_a_warning(monkeypatch, creds):
    route(monkeypatch, {**TOKENS, ("POST", "https://api.satellogic.com/v2/analysis/opportunities"): err(429, {"Retry-After": "7"})})
    [w] = ad("satellogic").commission({"lat": 1.0, "lon": 2.0})["warnings"]
    assert w["code"] == "rate_limited" and w["retry_after_s"] == 7


def test_satellogic_order_status_events_deliverables_cancel(monkeypatch, creds):
    O = "https://api.satellogic.com/v2/orders"
    f = route(monkeypatch, {**TOKENS, ("POST", O): fx("satellogic", "order_created.json"),
                            ("GET", f"{O}/000123/deliverables"): fx("satellogic", "deliverables.json"),
                            ("GET", f"{O}/000123"): fx("satellogic", "order_closed.json"),
                            ("DELETE", f"{O}/000123"): None})
    a = ad("satellogic")
    order = a.commission({"lat": 1.0, "lon": 2.0, "sku": "TSKRSH"}, confirm=True)
    assert f.calls[-1]["method"] == "POST" and f.calls[-1]["url"] == O
    assert a.map_status(a.provider_status(order)) == "in_flight"
    st = a.status(order)
    assert a.provider_status(st) == "OrderClosed" and a.map_status("OrderClosed") == "delivered"
    [o] = a.deliverables(st)
    assert o["url"] == "https://api.satellogic.com/assets/deliverables/example/visual" and o["phenomenon_time"] is None
    a.cancel(order)
    assert f.calls[-1]["method"] == "DELETE"
    bare = {"properties": {"status": "closed"}}
    assert a.provider_status(bare) == "closed" and a.map_status("closed") is None   # Closed alone doesn't say how
    for ev in ("OrderAnalysisFailed", "OrderForceClosed", "OrderCancellationAccepted"):
        assert a.map_status(ev) == "failed"


# ----------------------------------------------------------------------------------------------------------- Capella

def test_capella_never_quotes_through_post_task(monkeypatch, creds):
    a = ad("capella")
    d = a.commission({"lat": 51.95, "lon": 4.14, "within_minutes": 2880})
    assert d["dry_run"] and "quote" not in d and d["would_send"]["body"]["properties"]["preApproval"] is False
    assert len(http.request.calls) == 0   # key set, still nothing sent
    no_secret(d)
    offer = AdapterShim("capella").quote({"request": ASK}).body["offers"][0]
    assert offer["firm"] is False and "total" not in offer and len(http.request.calls) == 0


def test_capella_order_review_estimate_approve(monkeypatch, creds):
    T = "https://api.capellaspace.com/task"
    tid = "45b0e94b-6107-4671-ab13-d526e45d88c2"
    f = route(monkeypatch, {("POST", T): fx("capella", "task_received.json"), ("PATCH", f"{T}/{tid}/status"): {"ok": True},
                            ("GET", f"{T}/{tid}"): fx("capella", "task_review.json")})
    a = ad("capella")
    order = a.commission({"lat": 42.35, "lon": -71.1, "idempotency_key": "k-2"}, confirm=True)
    sent = f.calls[-1]
    assert sent["headers"]["Authorization"] == "ApiKey cap-" + SECRET   # the key goes only to Capella
    assert sent["body"]["properties"]["preApproval"] is False and sent["body"]["properties"]["customAttribute1"] == "k-2"
    assert a.map_status(a.provider_status(order)) == "in_flight" and a.order_total(order) is None
    st = a.status(order)
    assert a.provider_status(st) == "review" and a.map_status("review") == "in_flight"
    assert a.estimate(st) == Decimal("1000.00") and a.order_total(st) == Decimal("1000.00")
    n = len(f.calls)
    dry = a.approve(order)
    assert dry["dry_run"] and len(f.calls) == n
    a.approve(order, confirm=True)
    assert f.calls[-1]["method"] == "PATCH" and f.calls[-1]["body"] == {"status": "approved"}
    done = fx("capella", "task_completed.json")
    assert a.provider_status(done) == "completed" and a.map_status("completed") == "delivered"
    for raw in ("rejected", "expired", "canceled", "error", "anomaly"):
        assert a.map_status(raw) == "failed"


def test_capella_errors(monkeypatch, creds):
    route(monkeypatch, {("GET", "https://api.capellaspace.com/task/x"): err(401)})
    with pytest.raises(MissingCredentials):
        ad("capella").status({"id": "x"})
    route(monkeypatch, {("GET", "https://api.capellaspace.com/task/y"): err(403)})
    with pytest.raises(http.HTTPError):
        ad("capella").status({"id": "y"})


# --------------------------------------------------------------------------------------------------------- Vantor Hub

def test_vantor_is_dry_run_only(monkeypatch, creds):
    a = ad("vantor-hub")
    d = a.commission({"lat": 40.5, "lon": -120.5})
    doc = fx("vantor-hub", "fastview_request.json")
    body = d["would_send"]["body"]
    assert set(doc) - {"notifications", "output_configs"} <= set(body)
    assert body["tasking_tier"] == "fastView" and body["geometry"] == {"latitude": 40.5, "longitude": -120.5, "buffer": "8x8"}
    assert body["product_details"]["licensing"]["end_use_code"] == "AGR" and d["quote_call"]["url"].endswith("/estimate")
    assert datetime.fromisoformat(body["start_collect_datetime"].replace("Z", "+00:00")) >= NOW.replace(hour=20)
    with pytest.raises(NotSupported):
        a.commission({"lat": 40.5, "lon": -120.5}, confirm=True)
    assert len(http.request.calls) == 0
    no_secret(d)


def test_vantor_missing_end_use_code_and_statuses():
    a = ad("vantor-hub")
    assert a.commission({"lat": 1.0, "lon": 2.0})["missing"] == ["end_use_code"]
    for raw, want in (("INITIAL", "in_flight"), ("DELIVERY-SUCCESS", "in_flight"), ("FULFILLED", "delivered"),
                      ("DENIED", "failed"), ("UNFULFILLED", "failed"), ("PARTIALLY-FULFILLED", None)):
        assert a.map_status(raw) == want


# --------------------------------------------------------------------------------------------- Open Cosmos DataCosmos

ITEM = {"collection": "sentinel-2-l2a", "item": "S2A_MSIL2A_20230615T100559_N0509_R022_T33UUP_20230615T135959", "level": "L2A"}


def test_open_cosmos_needs_the_catalogue_item():
    d = ad("open-cosmos-datacosmos").commission({"lat": 1.0, "lon": 2.0})
    assert set(d["missing"]) == {"collection", "item", "level", "contract_id", "organisation"} and "tasking tool" in d["note"]
    assert len(http.request.calls) == 0


def test_open_cosmos_price_is_reported_never_firm(monkeypatch, creds):
    f = route(monkeypatch, {**TOKENS, ("GET", "https://app.open-cosmos.com/api/data/v0/order/price"): fx("open-cosmos-datacosmos", "price.json")})
    d = ad("open-cosmos-datacosmos").commission(dict(ITEM))
    assert d["catalogue_price"] == {"value": "150.00", "discount": "0.00", "final": "150.00", "currency": "USD"} and "quote" not in d
    assert d["would_send"]["body"]["organisation"] == 42 and d["would_send"]["body"]["contract_id"] == 456
    assert f.calls[0]["form"]["audience"] == "https://beeapp.open-cosmos.com" and "contract_id=456" in f.calls[1]["url"]
    no_secret(d)
    route(monkeypatch, {**TOKENS, ("GET", "https://app.open-cosmos.com/api/data/v0/order/price"): err(404)})
    assert ad("open-cosmos-datacosmos").commission(dict(ITEM))["warnings"][0]["code"] == "not_found"


def test_open_cosmos_order_total_status_deliverables(monkeypatch, creds):
    O = "https://app.open-cosmos.com/api/data/v0/order/orders"
    oid = "550e8400-e29b-41d4-a716-446655440000"
    route(monkeypatch, {**TOKENS, ("POST", O): fx("open-cosmos-datacosmos", "order_paid.json"),
                        ("GET", f"{O}/{oid}"): fx("open-cosmos-datacosmos", "order_unpaid.json")})
    a = ad("open-cosmos-datacosmos")
    order = a.commission(dict(ITEM), confirm=True)
    assert a.order_total(order) == Decimal("150.00")
    assert a.provider_status(order) == "PAID" and a.map_status("PAID") == "in_flight"
    st = a.status(order)
    assert a.provider_status(st) == "UNPAID" and a.deliverables(st) == []
    order["data"]["order_line_items"][0]["delivered"] = True
    [o] = a.deliverables(order)
    assert o["item"] == ITEM["item"] and o["phenomenon_time"] is None


# --------------------------------------------------------------------------------------------------------- helpers

def test_window_bounds_and_usd_parsing():
    s, e = _a9.window({"within_minutes": 60}, lead_h=24, min_width_h=24, max_end_h=14 * 24, default_width_h=72)
    assert (e - s).total_seconds() == 24 * 3600
    s, e = _a9.window({"window_days": 40}, lead_h=24, min_width_h=24, max_end_h=14 * 24, default_width_h=72)
    assert (e - NOW).total_seconds() == 14 * 24 * 3600
    s, e = _a9.window({"deliver_by": "2026-11-02T00:00:00Z"}, lead_h=1, min_width_h=1, max_end_h=None, default_width_h=1)
    assert _a9.iso(e) == "2026-11-02T00:00:00Z"
    assert _a9.usd("$1,000.00") == Decimal("1000.00") and _a9.usd("150.00") == Decimal("150.00")
    assert _a9.usd("EUR 5") is None and _a9.usd(None) is None
