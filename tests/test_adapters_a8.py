"""A8 self-serve satellite adapters (up42, skywatch, pixxel, satvu): ready calls, quotes (firm only in USD), orders,
status mapping, error mapping and credential hygiene. Offline: irlpls.http.request is replaced by a fake that serves
fixtures from tests/fixtures/adapters/<card>/ (see each SOURCE file)."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from irlpls import http
from irlpls.adapters import _a8
from irlpls.adapters.base import MissingCredentials
from irlpls.adapters.pixxel import Pixxel
from irlpls.adapters.satvu import SatVu
from irlpls.adapters.skywatch import SkyWatch
from irlpls.adapters.up42 import UP42
from irlpls.protocol.shim import AdapterShim

FX = Path(__file__).parent / "fixtures" / "adapters"
REQ = {"need": "new satellite image of the Port of Los Angeles, CA", "lat": 33.74, "lon": -118.26}
SECRETS = {"UP42_USERNAME": "mo-secret-user@example.com", "UP42_PASSWORD": "up42-SECRET-pw-9876",
           "UP42_WORKSPACE_ID": "ws-1111", "UP42_DATA_PRODUCT_ID": "dp-2222",
           "SKYWATCH_API_KEY": "sw-SECRET-key-5555", "SKYWATCH_OFFERING_ID": "off-3333", "SKYWATCH_OUTPUT_ID": "out-4444",
           "PIXXEL_API_TOKEN": "px-SECRET-pat-7777", "PIXXEL_PROJECT_ID": "proj-1", "PIXXEL_BANDSET_ID": "bs-1",
           "SATVU_CLIENT_ID": "satvu-SECRET-cid-1", "SATVU_CLIENT_SECRET": "satvu-SECRET-cs-2",
           "SATVU_CONTRACT_ID": "80736ba9-efad-46b0-9d24-9c4a96c85fa9"}
SECRET_VALUES = [v for k, v in SECRETS.items() if "SECRET" in v or k == "UP42_USERNAME"]
TOKENS = ["eyJ0eXAiOiJKV1QiLCJraWQiOiIxIiwidG9rZW5fdHlw-FIXTURE", "satvu-fixture-token"]


def fx(card, name):
    return json.loads((FX / card / name).read_text())


class Fake:
    """Routes (METHOD, url substring) to a fixture dict or an HTTPError; records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, params=None, headers=None, body=None, form=None, **kw):
        self.calls.append({"method": method, "url": url, "params": params, "headers": dict(headers or {}), "body": body, "form": form})
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                if isinstance(resp, http.HTTPError):
                    raise resp
                return resp
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in SECRETS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(http, "request", Fake({}))   # no network unless a test installs routes
    UP42.tokens.clear()
    SatVu.tokens.clear()
    yield
    UP42.tokens.clear()
    SatVu.tokens.clear()


@pytest.fixture
def keys(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)


def install(monkeypatch, routes):
    f = Fake(routes)
    monkeypatch.setattr(http, "request", f)
    return f


def no_secrets(*objs):
    text = json.dumps(objs, default=str)
    for s in SECRET_VALUES + TOKENS:
        assert s not in text, f"credential leaked: {s[:6]}..."


ADAPTERS = [UP42, SkyWatch, Pixxel, SatVu]


# ---------------------------------------------------------------------------------------------------- ready calls

@pytest.mark.parametrize("cls", ADAPTERS)
def test_prepare_quote_order_status_offline_and_named_credentials_only(cls, keys):
    a = cls()
    q = a.prepare(REQ, "commission")
    assert q["operation"] == "quote" and q["method"] == "POST" and q["safe_to_run_without_approval"] is False
    o = a.prepare(REQ, "commission", operation="order")
    assert o["operation"] == "order" and o["method"] == "POST" and o["safe_to_run_without_approval"] is False
    s = a.prepare({"order_id": "abc-123"}, "commission", operation="status")
    assert s["operation"] == "status" and s["method"] == "GET" and "abc-123" in s["url"] and s["safe_to_run_without_approval"]
    for c in (q, o, s):
        assert c["auth"]["placeholder"] == "{" + c["auth"]["key_name"] + "}"
        assert c["auth"]["placeholder"] in json.dumps(c["headers"])
        assert c["docs_url"].startswith("https://")
    no_secrets(q, o, s)
    assert http.request.calls == []   # offline
    assert a.prepare({"need": "x"}, "commission") is None            # no place, no geometry
    assert a.prepare(REQ, "live") is None                             # these providers have no live feed


def test_prepare_details_per_provider():
    u = UP42().prepare(REQ, "commission")
    assert u["url"] == "https://api.up42.com/v2/orders/estimate" and u["auth"]["kind"] == "oauth2"
    assert u["body"]["dataProduct"] == "{UP42_DATA_PRODUCT_ID}" and "grant_type=password" in u["notes"]
    ring = u["body"]["featureCollection"]["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    assert "{UP42_WORKSPACE_ID}" in UP42().prepare(REQ, "commission", operation="order")["url"]
    r = UP42().prepare(REQ, "commission", operation="read")
    assert r["auth"] == {"kind": "none"} and r["safe_to_run_without_approval"] and "type=TASKING" in r["url"]

    sw = SkyWatch().prepare(REQ, "commission")
    assert sw["url"].endswith("/price/calculate") and sw["headers"] == {"x-api-key": "{SKYWATCH_API_KEY}"}
    assert sw["body"]["tasking"] is True and sw["body"]["interval"].endswith("d") and int(sw["body"]["interval"][:-1]) >= 7
    swo = SkyWatch().prepare(REQ, "commission", operation="order")
    assert swo["body"]["max_cost"] == "{MAX_COST}" and swo["body"]["output"]["mosaic"] == "unstitched"

    px = Pixxel().prepare(REQ, "commission")
    assert px["body"]["dry_run"] is True and px["auth"]["kind"] == "bearer"
    assert Pixxel().prepare(REQ, "commission", operation="order")["body"]["dry_run"] is False
    assert Pixxel().prepare(REQ, "commission", operation="read")["url"].endswith("/v0/bandsets")

    sv = SatVu().prepare(REQ, "commission")
    assert sv["url"] == "https://api.satellitevu.com/otm/v2/{SATVU_CONTRACT_ID}/tasking/price/"
    assert sv["body"]["geometry"] == {"type": "Point", "coordinates": [-118.26, 33.74]}
    assert "audience=https://api.satellitevu.com/" in sv["notes"]
    assert SatVu().prepare(REQ, "commission", operation="read") is None   # no keyless read


# ---------------------------------------------------------------------------------------------------- reads

def test_up42_collections_keyless_read_from_live_fixture(monkeypatch):
    f = install(monkeypatch, {("GET", "/v2/collections"): fx("up42", "collections_tasking.json")})
    cs = UP42().collections()
    assert f.calls[0]["params"] == {"type": "TASKING", "size": 50} and "Authorization" not in f.calls[0]["headers"]
    assert cs and all(c["type"] == "TASKING" and c["name"].endswith("-tasking") for c in cs)
    assert cs[0]["data_products"][0]["id"] and isinstance(cs[0]["integrations"], list)


def test_skywatch_offerings_needs_the_key(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "/products/offerings/search"): []})
    assert SkyWatch().offerings(33.74, -118.26) == []
    assert f.calls[0]["headers"]["x-api-key"] == SECRETS["SKYWATCH_API_KEY"] and f.calls[0]["body"]["category"] == "tasking"


# ---------------------------------------------------------------------------------------------------- quotes

def test_dry_run_without_credentials_sends_nothing():
    for cls in ADAPTERS:
        d = cls().commission({"lat": 33.74, "lon": -118.26})
        assert d["dry_run"] is True and "quote" not in d and d["would_send"]["method"] == "POST"
        no_secrets(d)
    assert http.request.calls == []


def test_dry_run_asks_for_the_place():
    for cls in ADAPTERS:
        d = cls().commission({"name": "x"})
        assert d["dry_run"] and [m.split(":")[0] for m in d["missing"]] == ["lat", "lon"]


def test_up42_estimate_is_credits_so_not_firm(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "auth.up42.com"): fx("up42", "token.json"),
                              ("POST", "/v2/orders/estimate"): fx("up42", "estimate.json")})
    d = UP42().commission({"lat": 33.74, "lon": -118.26})
    tok = f.calls[0]
    assert tok["form"]["grant_type"] == "password" and tok["form"]["client_id"] == "up42-api"
    est = f.calls[1]
    assert est["headers"]["Authorization"].startswith("Bearer ") and est["body"]["dataProduct"] == "dp-2222"
    assert "quote" not in d and d["provider_quote"]["amount"] == 14424 and d["provider_quote"]["currency"] == "UP42_CREDITS"
    assert AdapterShim._dry_total(d) == (None, False)
    assert "workspaceId=ws-1111" in d["would_send"]["url"] and d["would_send"]["headers"]["Authorization"] == "***"
    # token reused (cached) on the next call
    UP42().commission({"lat": 1, "lon": 2})
    assert sum("auth.up42.com" in c["url"] for c in f.calls) == 1


def test_up42_without_a_data_product_asks_for_it(monkeypatch, keys):
    monkeypatch.delenv("UP42_DATA_PRODUCT_ID")
    d = UP42().commission({"lat": 1, "lon": 2})
    assert d["missing"][0].startswith("data_product_id") and http.request.calls == []


def test_skywatch_price_is_usd_so_firm(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "/price/calculate"): fx("skywatch", "price.json")})
    d = SkyWatch().commission({"lat": 33.74, "lon": -118.26})
    assert f.calls[0]["body"]["product"] == {"configuration": {"offering_id": "off-3333"}}
    assert AdapterShim._dry_total(d) == (Decimal("262.5"), True)
    assert d["would_send"]["body"]["max_cost"] == 262.5 and d["would_send"]["headers"]["x-api-key"] == "***"
    no_secrets(d)


def test_pixxel_dry_run_is_tokens_equal_usd_so_firm(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "/v0/orders"): fx("pixxel", "dry_run.json")})
    d = Pixxel().commission({"lat": 33.74, "lon": -118.26})
    assert f.calls[0]["body"]["dry_run"] is True and f.calls[0]["body"]["order_items"][0]["project_id"] == "proj-1"
    assert AdapterShim._dry_total(d) == (Decimal("800"), True) and d["feasibility"] == ["SUCCESS"]
    assert d["would_send"]["body"]["dry_run"] is False   # what confirm=True would send
    no_secrets(d)


def test_satvu_price_firm_only_in_usd(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "auth.satellitevu.com"): fx("satvu", "token.json"),
                              ("POST", "/tasking/price/"): fx("satvu", "price_gbp.json")})
    d = SatVu().commission({"lat": 33.74, "lon": -118.26})
    tok = f.calls[0]
    assert tok["form"]["grant_type"] == "client_credentials" and tok["form"]["audience"] == "https://api.satellitevu.com/"
    assert f"/{SECRETS['SATVU_CONTRACT_ID']}/tasking/price/" in f.calls[1]["url"]
    assert AdapterShim._dry_total(d) == (None, False) and d["provider_quote"]["currency"] == "GBP"
    assert d["provider_quote"]["amount"] == 1000.0      # 100000 pence: a unit change, never a conversion
    assert AdapterShim._dry_price(d) == (Decimal("1000.0"), "GBP", False)
    assert "not firm" in d["note"]
    install(monkeypatch, {("POST", "/tasking/price/"): fx("satvu", "price_usd.json")})
    d = SatVu().commission({"lat": 33.74, "lon": -118.26})
    assert AdapterShim._dry_total(d) == (Decimal("1200.0"), True)
    no_secrets(d)


def test_shim_quote_uses_the_adapter_end_to_end(monkeypatch, keys):
    install(monkeypatch, {("POST", "/price/calculate"): fx("skywatch", "price.json")})
    s = AdapterShim("skywatch", adapter=SkyWatch())
    r = s.quote({"request": {"need": "new satellite image of the port", "lat": 33.74, "lon": -118.26}})
    offer = r.body["offers"][0]
    assert offer["firm"] is True and offer["total"]["amount"] in ("262.5", "262.50") and offer["total"]["currency"] == "USD"
    no_secrets(r.body)


# ---------------------------------------------------------------------------------------------------- orders

def test_orders_post_to_the_documented_endpoints(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "auth.up42.com"): fx("up42", "token.json"),
                              ("POST", "api.up42.com/v2/orders"): fx("up42", "order_created.json")})
    r = UP42().commission({"lat": 1, "lon": 2}, confirm=True)
    assert f.calls[-1]["params"] == {"workspaceId": "ws-1111"} and f.calls[-1]["url"].endswith("/v2/orders")
    assert UP42._order_id(r) == "68567134-27ad-7bd7-4b65-d61adb11fc78" and UP42.order_total is None   # credits: no USD total

    f = install(monkeypatch, {("POST", "/price/calculate"): fx("skywatch", "price.json"),
                              ("POST", "/pipelines"): fx("skywatch", "pipeline_created.json")})
    r = SkyWatch().commission({"lat": 1, "lon": 2}, confirm=True)
    assert f.calls[-1]["body"]["max_cost"] == 262.5 and f.calls[-1]["body"]["output"]["id"] == "out-4444"
    assert SkyWatch().provider_status(r) == "pending"

    f = install(monkeypatch, {("POST", "/v0/orders"): fx("pixxel", "order.json")})
    r = Pixxel().commission({"lat": 1, "lon": 2}, confirm=True)
    assert f.calls[-1]["body"]["dry_run"] is False and Pixxel().order_total(r) == Decimal("800")

    f = install(monkeypatch, {("POST", "auth.satellitevu.com"): fx("satvu", "token.json"),
                              ("POST", "/tasking/orders/"): fx("satvu", "order.json")})
    r = SatVu().commission({"lat": 1, "lon": 2}, confirm=True)
    assert SatVu().provider_status(r) == "staged" and SatVu().order_total(r) is None   # GBP: never a USD total


def test_orders_refuse_without_account_ids(monkeypatch, keys):
    monkeypatch.delenv("UP42_WORKSPACE_ID")
    monkeypatch.delenv("SKYWATCH_OUTPUT_ID")
    monkeypatch.delenv("PIXXEL_BANDSET_ID")
    for cls in (UP42, SkyWatch, Pixxel):
        with pytest.raises(MissingCredentials):
            cls().commission({"lat": 1, "lon": 2}, confirm=True)
    monkeypatch.delenv("SATVU_CONTRACT_ID")
    with pytest.raises(MissingCredentials):
        SatVu().commission({"lat": 1, "lon": 2}, confirm=True)
    assert http.request.calls == []


def test_spec_from_request_hook():
    s = SkyWatch().spec_from_request({"need": "port", "lat": 1.0, "lon": 2.0}, "ext-1", "2026-12-01T00:00:00Z")
    assert s == {"lat": 1.0, "lon": 2.0, "name": "port", "deliver_by": "2026-12-01T00:00:00Z", "external_id": "ext-1"}


def test_window_honours_lead_time_and_deadline(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(_a8, "now", lambda: datetime(2026, 10, 28, 10, 30, tzinfo=timezone.utc))
    s, e = _a8.window({}, lead_h=48, min_days=7)
    assert s == datetime(2026, 10, 30, 10, tzinfo=timezone.utc) and (e - s).days == 7
    s, e = _a8.window({"deliver_by": "2026-11-20T00:00:00Z"}, lead_h=24)
    assert e == datetime(2026, 11, 20, tzinfo=timezone.utc)
    s, e = _a8.window({"deliver_by": "2026-10-29T00:00:00Z"}, lead_h=24)   # too soon: keep the default window
    assert (e - s).days == 7


# ---------------------------------------------------------------------------------------------------- status

def test_up42_status_mapping_and_delivery(monkeypatch, keys):
    a = UP42()
    for raw, want in (("CREATED", "in_flight"), ("BEING_PLACED", "in_flight"), ("PLACED", "in_flight"),
                      ("PLACEMENT_FAILED", "in_flight"), ("BEING_FULFILLED", "in_flight"), ("FULFILLED", "delivered"),
                      ("FAILED_PERMANENTLY", "failed"), ("CANCELED", "failed"), ("SOMETHING_NEW", None)):
        assert a.map_status(raw) == want
    f = install(monkeypatch, {("POST", "auth.up42.com"): fx("up42", "token.json"),
                              ("GET", "/v2/orders/"): fx("up42", "order_fulfilled.json")})
    resp = a.status(fx("up42", "order_created.json"))
    assert f.calls[-1]["url"].endswith("/v2/orders/68567134-27ad-7bd7-4b65-d61adb11fc78")
    assert a.map_status(a.provider_status(resp)) == "delivered"
    [o] = a.deliverables(resp)
    assert o["order_id"] == resp["id"] and o["acquisition_start"] == "2026-10-29T00:00:00Z" and o["phenomenon_time"] is None
    assert a.deliverables(fx("up42", "order_created_status.json")) == []


def test_skywatch_status_mapping_and_delivery():
    a = SkyWatch()
    assert [a.map_status(s) for s in ("pending", "active", "complete", "failed", "inactive", "deactivating")] == [
        "in_flight", "in_flight", "delivered", "failed", None, None]
    resp = fx("skywatch", "pipeline_complete.json")
    assert a.provider_status(resp) == "complete"
    [o] = a.deliverables(resp)
    assert o["url"].endswith(f"/pipelines/{resp['data']['id']}/interval_results")
    assert a.deliverables(fx("skywatch", "pipeline_created.json")) == []


def test_pixxel_status_reads_order_items_not_payment():
    a = Pixxel()
    assert a.provider_status(fx("pixxel", "order.json")) == "ACTIVE"   # order.status is PAID (payment), not fulfilment
    assert a.map_status("ACTIVE") == "in_flight" and a.map_status("PARTIALLY_FULFILLED") == "in_flight"
    assert a.map_status("CANCELLED") == "failed" and a.map_status("DRAFT") == "in_flight"
    done = fx("pixxel", "order_fulfilled.json")
    assert a.map_status(a.provider_status(done)) == "delivered"
    [o] = a.deliverables(done)
    assert o["stac_item_id"] == "FF04_20261102_101010_0001"
    mixed = {"order_items": [{"status": "FULFILLED"}, {"status": "FAILED"}]}
    assert a.provider_status(mixed) == "FAILED" and a.provider_status({}) is None


def test_satvu_status_mapping_and_delivery():
    a = SatVu()
    for raw, want in (("staged", "in_flight"), ("in progress", "in_flight"), ("fulfilled", "delivered"),
                      ("failed", "failed"), ("cancelled", "failed"), ("expired", "failed")):
        assert a.map_status(raw) == want
    resp = fx("satvu", "order_fulfilled.json")
    [o] = a.deliverables(resp)
    assert o["url"].endswith(f"/{resp['contract_id']}/tasking/orders/{resp['id']}/download") and o["lat"] == 51.5


def test_cancel_calls(monkeypatch, keys):
    f = install(monkeypatch, {("POST", "auth.up42.com"): fx("up42", "token.json"),
                              ("POST", "/cancellation"): fx("up42", "cancel.json")})
    assert UP42().cancel({"id": "o1"})["status"] == "CANCELED" and f.calls[-1]["url"].endswith("/v2/orders/o1/cancellation")
    f = install(monkeypatch, {("DELETE", "/pipelines/p1"): None})
    SkyWatch().cancel({"data": {"id": "p1"}})
    assert f.calls[-1]["method"] == "DELETE"
    f = install(monkeypatch, {("POST", "auth.satellitevu.com"): fx("satvu", "token.json"), ("POST", "/cancel"): None})
    assert SatVu().cancel({"id": "s1"}) == {"id": "s1", "cancel_requested": True}
    assert Pixxel.can_cancel is False and all(c.can_cancel for c in (UP42, SkyWatch, SatVu))


# ---------------------------------------------------------------------------------------------------- errors

def _err(status, url, body="", headers=None):
    return http.HTTPError(status, url, body, headers=headers)


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_become_missing_credentials(monkeypatch, keys, status):
    install(monkeypatch, {("POST", "/price/calculate"): _err(status, "https://api.skywatch.co/earthcache/price/calculate",
                                                             "bad key " + SECRETS["SKYWATCH_API_KEY"])})
    with pytest.raises(MissingCredentials) as e:
        SkyWatch().commission({"lat": 1, "lon": 2})
    assert f"HTTP {status}" in str(e.value) and "SKYWATCH_API_KEY" in str(e.value)
    no_secrets(str(e.value))


def test_token_refusal_is_missing_credentials(monkeypatch, keys):
    install(monkeypatch, {("POST", "auth.up42.com"): _err(401, "https://auth.up42.com/x", "invalid_grant")})
    with pytest.raises(MissingCredentials):
        UP42().commission({"lat": 1, "lon": 2})
    install(monkeypatch, {("POST", "auth.satellitevu.com"): _err(403, "https://auth.satellitevu.com/oauth/token")})
    with pytest.raises(MissingCredentials) as e:
        SatVu().status({"id": "x"})
    no_secrets(str(e.value))


def test_404_is_not_found(monkeypatch, keys):
    install(monkeypatch, {("GET", "/v0/orders/nope"): _err(404, "https://api.pixxel.space/v0/orders/nope", "{}")})
    with pytest.raises(_a8.ProviderError) as e:
        Pixxel().status({"id": "nope"})
    assert e.value.code == "not_found" and e.value.status == 404 and isinstance(e.value, http.HTTPError)


def test_429_carries_retry_after(monkeypatch, keys):
    install(monkeypatch, {("POST", "auth.up42.com"): fx("up42", "token.json"),
                          ("GET", "/v2/orders/"): _err(429, "https://api.up42.com/v2/orders/o1?x=" + SECRETS["UP42_PASSWORD"],
                                                       "slow down", {"Retry-After": "30"})})
    with pytest.raises(_a8.ProviderError) as e:
        UP42().status({"id": "o1"})
    assert e.value.code == "rate_limited" and e.value.retry_after_s == 30.0 and e.value.retry_after == 30.0
    assert "?" not in e.value.url
    no_secrets(str(e.value), e.value.url, e.value.body)


def test_400_keeps_a_scrubbed_excerpt(monkeypatch, keys):
    body = json.dumps(fx("pixxel", "error_400.json")) + " " + SECRETS["PIXXEL_API_TOKEN"]
    install(monkeypatch, {("POST", "/v0/orders"): _err(400, "https://api.pixxel.space/v0/orders", body)})
    with pytest.raises(_a8.ProviderError) as e:
        Pixxel().commission({"lat": 1, "lon": 2})
    assert e.value.code == "invalid_request" and "insufficient_tokens" in str(e.value)
    no_secrets(str(e.value))


def test_shim_maps_provider_errors(monkeypatch, keys):
    install(monkeypatch, {("POST", "/price/calculate"): _err(503, "https://api.skywatch.co/earthcache/price/calculate", "",
                                                             {"Retry-After": "12"})})
    from irlpls.protocol.shim import Problem
    with pytest.raises(Problem) as e:
        AdapterShim("skywatch", adapter=SkyWatch()).quote({"request": {"need": "image", "lat": 1, "lon": 2}})
    assert e.value.code == "provider_unavailable" and "503" in e.value.body["detail"]
