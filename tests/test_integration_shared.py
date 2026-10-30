"""Integrator's shared changes after adapter waves A1-A10 (docs/research/strategy/WRAPPER-TRIAGE.md, "Still needed"):
shim cooperative cancel, sub-national coverage, header masking and a secret-leak sweep over every adapter, http
return_headers (and the MCP transport on it), TokenCache(json=True), the USD-only firm rule, two-step approvals,
prepared_calls per operation, and the wallet catalog state. No network: every provider is a fake or a monkeypatch."""
import asyncio
import copy
import glob
import json
import os
import pathlib
import re
import secrets
from decimal import Decimal

import pytest

import irlpls
from irlpls import adapters, broker, catalog, http, ratelimit
from irlpls.adapters import _mcp, _oauth
from irlpls.adapters.base import Adapter, NotSupported, mask_headers, quote_price, usd_total
from irlpls.protocol import shim as S

ROOT = pathlib.Path(__file__).resolve().parents[1]
V = {"Irlpls-Version": S.VERSION}
NONCE = "q8XvY2m1Lw0aPp5sR3tU7w"


# ------------------------------------------------------------------------------------------------------------ helpers

class Tasker(Adapter):
    """A made-to-order provider with a documented cancel and a second approval step (like Capella / GLOBHE)."""
    card_id = "fake-tasker"
    env = "FAKE_TASKER_KEY"
    can_commission = True
    can_cancel = True
    status_map = {"open": "in_flight", "review": "in_flight", "cancelled": "failed", "done": "delivered"}

    def __init__(self, quote=None, status_resp=None, cancel_resp=None, cancel_err=None):
        self.q, self.status_resp, self.cancel_resp, self.cancel_err = quote, status_resp, cancel_resp, cancel_err
        self.placed, self.cancels, self.approved = [], [], []

    def commission(self, spec, confirm=False):
        if not confirm:
            d = self._dry("POST", "https://api.tasker.example/orders", spec, {"Authorization": "Bearer x"})
            if self.q is not None:
                d["quote"] = dict(self.q)
            return d
        self.placed.append(spec)
        return {"id": "P1", "status": "open"}

    def status(self, order):
        return self.status_resp or {"id": order["id"], "status": "open"}

    def cancel(self, order):
        self.cancels.append(order)
        if self.cancel_err:
            raise self.cancel_err
        return self.cancel_resp if self.cancel_resp is not None else {"id": order["id"], "status": "open"}

    def pending_approvals(self, resp):
        if (resp or {}).get("status") != "review":
            return []
        return [{"id": "est-1", "amount": Decimal("1000"), "currency": "USD", "label": "cost estimate"}]

    def approve(self, order, approval_id=None, confirm=False):
        if not confirm:
            return self._dry("PATCH", f"https://api.tasker.example/orders/{order['id']}", {"status": "approved"})
        self.approved.append((order["id"], approval_id))
        return {"id": order["id"], "status": "open"}


def tasker_card(areas=("US",)):
    c = copy.deepcopy(irlpls.card("proxypics"))
    c.update(id="fake-tasker", name="Fake Tasker")
    c["coverage"] = dict(c.get("coverage") or {}, areas=list(areas))
    c["delivery"] = dict(c["delivery"], modes=["commission"])
    return c


def shim(adapter, areas=("US",), **kw):
    return S.AdapterShim("fake-tasker", adapter=adapter, card=tasker_card(areas), base_url="https://shim.example.org/irlpls", **kw)


REQ = {"need": "photo of the storefront at 1 Main St", "lat": 33.75, "lon": -84.39, "country_code": "US", "mode": "commission"}


def quote(sh, req=REQ, cons=None):
    body = {"request": req}
    if cons:
        body["constraints"] = cons
    return sh.handle("POST", "/irlpls/quotes", V, json.dumps(body).encode())


def place(sh, key="k-1", cap="5000"):
    q = quote(sh).body
    body = {"quote_id": q["id"], "offer_id": q["offers"][0]["id"], "nonce": NONCE,
            "constraints": {"max_total": {"amount": cap, "currency": "USD"}}}
    r = sh.handle("POST", "/irlpls/orders", {**V, "Idempotency-Key": key}, json.dumps(body).encode())
    assert r.status == 202, r.body
    return r.body


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("FAKE_TASKER_KEY", "fake-secret-value")


@pytest.fixture(scope="module")
def validate():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    reg = Registry()
    for f in list((ROOT / "schema").glob("*.schema.json")) + list((ROOT / "schema/protocol").glob("*.schema.json")):
        s = json.loads(f.read_text())
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))

    def check(instance, ref):
        v = Draft202012Validator({"$ref": ref}, registry=reg)
        errs = [f"{list(e.path)}: {e.message[:200]}" for e in v.iter_errors(instance)]
        assert not errs, errs
    return check


# -------------------------------------------------------------------------------------------- 1. shim cooperative cancel

def test_shim_cancel_calls_the_adapter_and_waits_for_the_provider(key):
    t = Tasker(quote={"total": 40, "currency": "USD"})
    sh = shim(t, confirm_orders=True)
    assert sh.discover().body["protocol"]["cancel"] is True
    o = place(sh)
    c = sh.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert c.status == 200 and len(t.cancels) == 1 and t.cancels[0] == {"id": "P1", "status": "open"}
    # cooperative: still in flight until the provider's own status says it ended
    assert c.body["state"] == "in_flight" and c.body["provider_status"] == "open"
    again = sh.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert again.status == 200 and len(t.cancels) == 1          # same key: the provider is asked once
    t.status_resp = {"id": "P1", "status": "cancelled"}
    s = sh.handle("GET", f"/irlpls/orders/{o['id']}", V)
    assert s.body["state"] == "failed" and s.body["reason_code"] == "cancelled"
    # terminal: returned unchanged, the provider is not asked again
    end = sh.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-2"})
    assert end.status == 200 and end.body["state"] == "failed" and len(t.cancels) == 1


def test_shim_cancel_confirmed_at_once_and_refused(key):
    t = Tasker(quote={"total": 40}, cancel_resp={"id": "P1", "status": "cancelled"})
    sh = shim(t, confirm_orders=True)
    o = place(sh)
    c = sh.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert c.body["state"] == "failed" and c.body["reason_code"] == "cancelled"
    # the provider refuses (too late): 409 not_cancellable, the order unchanged, a new key may try again
    t2 = Tasker(quote={"total": 40}, cancel_err=http.HTTPError(409, "u", "already accepted"))
    sh2 = shim(t2, confirm_orders=True)
    o2 = place(sh2)
    r = sh2.handle("POST", f"/irlpls/orders/{o2['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert r.status == 409 and r.body["code"] == "not_cancellable"
    assert sh2.handle("GET", f"/irlpls/orders/{o2['id']}", V).body["state"] in ("submitted", "in_flight")
    t2.cancel_err = http.HTTPError(503, "u", "down")
    r = sh2.handle("POST", f"/irlpls/orders/{o2['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert r.status == 503 and r.body["code"] == "provider_unavailable" and len(t2.cancels) == 2


def test_shim_without_documented_cancel_stays_not_cancellable(key):
    t = Tasker(quote={"total": 40})
    t.can_cancel = False
    sh = shim(t, confirm_orders=True)
    assert sh.discover().body["protocol"]["cancel"] is False
    o = place(sh)
    r = sh.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert r.status == 409 and r.body["code"] == "not_cancellable" and not t.cancels


def test_fulfiller_cancel_follows_the_adapter(key):
    t = Tasker()
    f = S.AdapterFulfiller(shim(t))
    assert f.can_cancel is True
    assert f.cancel({"fstate": {"provider_order": {"id": "P1"}}}) == "accepted"
    t.cancel_resp = {"id": "P1", "status": "cancelled"}
    assert f.cancel({"fstate": {"provider_order": {"id": "P1"}}}) == "cancelled"
    t.cancel_err = http.HTTPError(422, "u", "claimed")
    assert f.cancel({"fstate": {"provider_order": {"id": "P1"}}}) == "refused"
    assert f.cancel({"fstate": {}}) == "refused"


# -------------------------------------------------------------------------------------------- 2. sub-national coverage

@pytest.mark.parametrize("areas,cc,ok", [
    (["US-GA"], "US", True), (["US-GA"], "us", True), (["KR-11"], "KR", True), (["US-GA"], "US-GA", True),
    (["US"], "US-GA", True), (["US-GA"], "US-CA", False), (["US-GA"], "KR", False), (["*"], "FR", True),
    (["GB-ENG", "GB-WLS"], "GB", True), (["USA"], "US", False)])
def test_coverage_matches_country_prefix(areas, cc, ok):
    assert S.covers(areas, cc) is ok


def test_shim_quote_accepts_country_for_subnational_card(key):
    sh = shim(Tasker(quote={"total": 40}), areas=("US-GA",))
    assert quote(sh).status == 201
    r = quote(sh, dict(REQ, country_code="KR"))
    assert r.status == 422 and r.body["code"] == "outside_coverage"
    assert quote(shim(Tasker(quote={"total": 9}), areas=("KR-11",)), dict(REQ, country_code="KR")).status == 201


# ---------------------------------------------------------------------------- 3. header masking + secret-leak sweep

def test_dry_masks_every_credential_like_header():
    h = {"Authorization": "a", "X-Api-Key": "b", "X-Access-Token": "c", "X-Client-Secret": "d", "Client-Id": "e",
         "X-Password": "f", "X-Session-Id": "g", "Cookie": "h", "Set-Cookie": "i", "AUTH": "j", "Accept": "json",
         "Content-Type": "application/json", "Idempotency": "k"}
    m = mask_headers(h)
    assert {k for k, v in m.items() if v == "***"} == set(h) - {"Accept", "Content-Type", "Idempotency"}
    d = Tasker()._dry("POST", "https://x.example", {}, h)
    assert d["would_send"]["headers"] == m and "fake-secret" not in json.dumps(d)


# Account configuration a ready call may legitimately fill in (ids and URLs, not credentials).
NOT_SECRET = re.compile(r"(PROJECT_ID|TYPE_OF_WORK_ID|PACKAGE_ID|TOKEN_URL|_SKU|TEAM_ID|INSPECTION_TYPE_ID|END_USE_CODE|"
                        r"API_BASE|_REGION|CONTRACT_ID|WORKSPACE_ID|DATA_PRODUCT_ID|BANDSET_ID|ORG_ID|TEMPLATE_CODE|"
                        r"PIPELINE_ID|USER_AGENT)$")


def _env_names():
    names = set()
    for f in glob.glob(str(ROOT / "irlpls" / "adapters" / "*.py")):
        names |= set(re.findall(r'"([A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9])"', pathlib.Path(f).read_text()))
    names |= {a.env for a in adapters.ADAPTERS.values() if a.env}
    return {n for n in names if not n.startswith("IRLPLS_")}


def test_no_adapter_puts_a_secret_in_a_ready_call_or_dry_run(monkeypatch):
    """Every env var an adapter reads is set to a random sentinel; every registered adapter's prepare() (both routes,
    every operation) and commission dry run must not contain a credential's value anywhere, errors included."""
    names = _env_names()
    sentinels = {n: "S3NT" + secrets.token_hex(10) for n in names}
    for n, v in sentinels.items():
        monkeypatch.setenv(n, v)
    secret = {n: v for n, v in sentinels.items() if not NOT_SECRET.search(n)}
    assert len(secret) > 40

    def no_net(*a, **k):
        raise http.HTTPError(500, "https://provider.example", "no network in this test")
    monkeypatch.setattr(http, "_open", no_net)
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(_mcp, "_post", lambda *a, **k: no_net())
    reqs = [dict(need="photo of the storefront", place="Paris", lat=48.85, lon=2.35, quantity=2, within_minutes=600,
                 country_code="FR", state=None, mode="commission", terms=[], specifics=[]),
            dict(need="river level now", place="Austin", lat=30.27, lon=-97.74, quantity=1, within_minutes=60,
                 country_code="US", state="TX", mode="live", terms=[], specifics=[])]
    checked, configured = 0, 0
    for cid, a in adapters.ADAPTERS.items():
        configured += bool(a.configured())
        outs = []
        for req in reqs:
            for route in ("live", "commission"):
                for op in (None,) + adapters.base.OPERATIONS:
                    try:
                        outs.append(a.prepare(req, route) if op is None else a.prepare(req, route, operation=op))
                    except Exception as e:   # an operation an adapter doesn't take is fine; its message is still checked
                        outs.append(repr(e))
            spec = {"lat": req["lat"], "lon": req["lon"], "address": req["place"], "notes": req["need"], "title": req["need"]}
            try:
                outs.append(a.commission(spec, confirm=False))
            except Exception as e:
                outs.append(repr(e))
        text = json.dumps(outs, default=str)
        leaked = [n for n, v in secret.items() if v in text]
        assert not leaked, f"{cid} wrote {leaked} into a ready call or dry run"
        checked += 1
    assert checked == len(adapters.ADAPTERS) >= 60 and configured >= 30


# ------------------------------------------------------------------------------------------- 4. http return_headers

class _Resp:
    def __init__(self, body=b'{"ok": 1}', headers=None, status=200):
        self.status, self._b = status, body
        import email.message
        self.headers = email.message.Message()
        for k, v in (headers or {"Content-Type": "application/json", "Mcp-Session-Id": "s-1"}).items():
            self.headers[k] = v

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_return_headers_keeps_checks_caps_and_cache(monkeypatch):
    sent = []
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(http, "_open", lambda req, timeout, ap=False: sent.append(req.full_url) or _Resp())
    r = http.request("GET", "https://api.example.org/x", return_headers=True)
    assert r == (200, {"content-type": "application/json", "mcp-session-id": "s-1"}, {"ok": 1})
    assert r.status == 200 and r.headers["mcp-session-id"] == "s-1" and r.body == {"ok": 1}
    assert http.request("GET", "https://api.example.org/x") == {"ok": 1}            # default unchanged
    with pytest.raises(http.UnsafeURL):
        http.request("POST", "http://api.example.org/x", body={}, return_headers=True, allow_http=False)
    monkeypatch.setattr(http, "_resolve", lambda h: ["10.0.0.5"])
    with pytest.raises(http.UnsafeURL):
        http.request("GET", "https://internal.example/x", return_headers=True)
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(http, "_open", lambda req, timeout, ap=False: _Resp(body=b"x" * 50))
    with pytest.raises(http.TooLarge):
        http.request("GET", "https://api.example.org/big", return_headers=True, max_bytes=10)
    # under a provider: paced and cached like any GET, and a cached answer keeps its headers (its own cache slot)
    ratelimit.reset() if hasattr(ratelimit, "reset") else ratelimit._cache.clear()
    sent.clear()
    monkeypatch.setattr(http, "_open", lambda req, timeout, ap=False: sent.append(req.full_url) or _Resp())
    with ratelimit.using("usgs-water"):
        a = http.request("GET", "https://api.example.org/c", return_headers=True)
        b = http.request("GET", "https://api.example.org/c", return_headers=True)
        c = http.request("GET", "https://api.example.org/c")
    assert a == b and isinstance(b, http.Response) and b.headers["mcp-session-id"] == "s-1" and c == {"ok": 1}
    assert len(sent) == 2   # one per variant (headers / body only), the repeat came from the cache


def test_mcp_transport_uses_http_request_and_the_rate_limiter(monkeypatch):
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw, ratelimit.current()))
        body = kw["body"]
        if body.get("method") == "initialize":
            return http.Response(200, {"content-type": "application/json", "mcp-session-id": "sess"},
                                 json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}}))
        if "id" not in body:
            return http.Response(202, {}, "")
        return http.Response(200, {"content-type": "text/event-stream"},
                             "data: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": {"ok": True}}}) + "\n\n")
    monkeypatch.setattr(http, "request", fake_request)
    out = _mcp.call_tool("https://mcp.quest.example/mcp", "tok", "whoAmI", {}, provider="quest")
    assert out == {"ok": True} and len(calls) == 3
    for method, url, kw, prov in calls:
        assert method == "POST" and kw["return_headers"] is True and kw["raw"] is True and kw["allow_http"] is False
        assert prov == "quest"   # every request paced under Quest's own limit
    assert calls[2][2]["headers"]["Mcp-Session-Id"] == "sess"


# --------------------------------------------------------------------------------------------- 5. TokenCache(json=True)

def test_token_cache_json_body(monkeypatch):
    seen = []

    def post(url, body=None, form=None, headers=None, **kw):
        seen.append({"body": body, "form": form})
        return {"access_token": "T", "expires_in": 3600}
    monkeypatch.setattr(http, "post", post)
    assert _oauth.TokenCache(json=True).client_credentials("https://auth.example/t", "id", "sec", audience="aud") == "T"
    assert seen[-1]["form"] is None and seen[-1]["body"] == {"grant_type": "client_credentials", "audience": "aud",
                                                              "client_id": "id", "client_secret": "sec"}
    assert _oauth.TokenCache().client_credentials("https://auth.example/t", "id", "sec") == "T"
    assert seen[-1]["body"] is None and seen[-1]["form"]["grant_type"] == "client_credentials"
    from irlpls.adapters import truepic_vision
    assert type(truepic_vision.TruepicVision._tokens) is _oauth.TokenCache and truepic_vision.TruepicVision._tokens.json
    assert not hasattr(truepic_vision, "_JsonTokens")


# ------------------------------------------------------------------------------------------------ 6. firm only in USD

@pytest.mark.parametrize("dry,expect", [
    ({"quote": {"total": 40}}, (Decimal("40"), "USD", True)),
    ({"quote": {"totalPrice": 12.5, "currency": "usd"}}, (Decimal("12.5"), "USD", True)),
    ({"quote": {"total": 450, "currency": "EUR"}}, (Decimal("450"), "EUR", False)),
    ({"quote": {"total": 200, "currency": "USDC", "firm": True}}, (Decimal("200"), "USDC", False)),
    ({"quote": {"total": 20, "currency": "USD", "firm": False}}, (Decimal("20"), "USD", False)),
    ({"provider_quote": {"amount": 6000, "currency": "KRW"}}, (Decimal("6000"), "KRW", False)),
    ({"provider_quote": {"amount": 3000, "currency": "credits"}}, (Decimal("3000"), "CREDITS", False)),
    ({"quote": {"total": True}}, (None, None, False)), ({"quote": {"total": float("nan")}}, (None, None, False)),
    ({"quote": {"total": -1}}, (None, None, False)), ({}, (None, None, False))])
def test_quote_price_is_firm_only_in_usd(dry, expect):
    assert quote_price(dry) == expect
    assert usd_total(dry) == (expect[0] if expect[2] else None)
    assert broker._quoted_total(dry) == usd_total(dry)
    assert S.AdapterShim._dry_total(dry) == ((expect[0], True) if expect[2] else (None, False))


def test_shim_offer_keeps_the_currency_and_is_never_firm_outside_usd(key, validate):
    eur = quote(shim(Tasker(quote={"total": 450, "currency": "EUR"}))).body
    o = eur["offers"][0]
    assert o["total"] == {"amount": "450", "currency": "EUR"} and o["firm"] is False
    validate(eur, "https://irlpls.com/2026-10-26/schemas/protocol/quote.json")
    usd = quote(shim(Tasker(quote={"total": 40, "currency": "USD"}))).body["offers"][0]
    assert usd["total"] == {"amount": "40", "currency": "USD"} and usd["firm"] is True
    usdc = quote(shim(Tasker(quote={"total": 200, "currency": "USDC", "firm": True}))).body["offers"][0]
    assert "total" not in usdc and usdc["firm"] is False
    assert usdc["x-com.irlpls.indicative_price"] == {"amount": "200", "currency": "USDC"}
    # a USD ceiling never passes another currency
    r = quote(shim(Tasker(quote={"total": 4, "currency": "EUR"})), cons={"max_total": {"amount": "1000", "currency": "USD"}})
    assert r.status == 422 and r.body["code"] == "over_max_total" and "never converts" in r.body["detail"]


def test_shim_order_of_a_non_usd_offer_needs_a_ceiling_in_that_currency(key):
    t = Tasker(quote={"total": 450, "currency": "EUR"})
    sh = shim(t, confirm_orders=True)
    q = quote(sh).body

    def order(cap, k):
        body = {"quote_id": q["id"], "offer_id": "o_1", "nonce": NONCE, "constraints": {"max_total": cap}}
        return sh.handle("POST", "/irlpls/orders", {**V, "Idempotency-Key": k}, json.dumps(body).encode())
    assert order({"amount": "1000", "currency": "USD"}, "a").body["code"] == "over_max_total" and not t.placed
    r = order({"amount": "500", "currency": "EUR"}, "b")
    assert r.status == 202 and r.body["total"] == {"amount": "450", "currency": "EUR"} and len(t.placed) == 1


def test_broker_shows_non_usd_price_and_blocks_it_under_a_usd_ceiling(monkeypatch):
    from irlpls.adapters import errand
    monkeypatch.setenv("ERRAND_API_KEY", "x" * 20)
    monkeypatch.setattr(errand.Errand, "quote", lambda self, spec: {"total_charge_krw": 6000, "reward_krw": 5000})
    monkeypatch.setattr(errand.Errand, "configured", lambda self: True)
    job = irlpls.request("someone check if the cafe at Gangnam station is open now", geocode=False, lat=37.498, lon=127.028,
                         country_code="KR", within_minutes=120)
    job.choose("errand")
    job.submit(spec={"reward_krw": 5000, "lat": 37.498, "lon": 127.028})
    assert job.state == "quoted" and job.quote["price"] == {"amount": "6000", "currency": "KRW", "firm": False}
    assert any("KRW" in line["msg"] for line in job.log)
    # irlpls.commission with a USD ceiling: KRW is never a USD total
    d = irlpls.commission("errand", {"reward_krw": 5000, "lat": 37.498, "lon": 127.028, "title": "check"},
                          policy={"max_order_usd": 100})
    assert d["blocked_by_policy"] == ["no quoted total in USD to check against max_order_usd"]


# --------------------------------------------------------------------------------------------- 7. two-step approvals

def test_shim_second_step_is_explicit_per_order_and_exact(key):
    t = Tasker(quote={"total": 0}, status_resp={"id": "P1", "status": "review"})
    sh = shim(t, confirm_orders=True)
    o = place(sh)
    s = sh.handle("GET", f"/irlpls/orders/{o['id']}", V).body
    ap = s["x-com.irlpls.approval"]
    assert ap["required"] and ap["options"] == [{"id": "est-1", "label": "cost estimate", "total": {"amount": "1000", "currency": "USD"}}]
    url = f"/irlpls/orders/{o['id']}/approve"

    def approve(body, k="ap-1"):
        return sh.handle("POST", url, {**V, "Idempotency-Key": k}, json.dumps(body).encode())
    assert sh.handle("POST", url, V, b"{}").body["code"] == "idempotency_key_required"
    assert approve({"approval_id": "est-1"}).body["code"] == "invalid_request"            # no confirm: never sent
    assert approve({"approval_id": "est-1", "confirm": True}).body["code"] == "over_max_total"   # total missing
    assert approve({"approval_id": "est-1", "confirm": True, "total": {"amount": "999", "currency": "USD"}}).status == 422
    assert approve({"approval_id": "nope", "confirm": True, "total": {"amount": "1000", "currency": "USD"}}).status == 400
    assert not t.approved
    ok = approve({"approval_id": "est-1", "confirm": True, "total": {"amount": "1000", "currency": "USD"}})
    assert ok.status == 200 and t.approved == [("P1", "est-1")]
    assert ok.body["total"] == {"amount": "1000", "currency": "USD"} and "x-com.irlpls.approval" not in ok.body
    approve({"approval_id": "est-1", "confirm": True, "total": {"amount": "1000", "currency": "USD"}})
    assert len(t.approved) == 1   # replay: not sent twice


def test_shim_approval_refused_on_a_quote_only_operator(key):
    t = Tasker(quote={"total": 0}, status_resp={"id": "P1", "status": "review"})
    sh = shim(t, confirm_orders=True)
    o = place(sh)
    sh.handle("GET", f"/irlpls/orders/{o['id']}", V)
    sh.confirm_orders = False
    r = sh.handle("POST", f"/irlpls/orders/{o['id']}/approve", {**V, "Idempotency-Key": "a"},
                  json.dumps({"approval_id": "est-1", "confirm": True, "total": {"amount": "1000", "currency": "USD"}}).encode())
    assert r.status == 403 and r.body["code"] == "insufficient_scope" and not t.approved


def _capella_job(monkeypatch, policy=None):
    monkeypatch.setenv("CAPELLA_API_KEY", "capella-secret-123")
    review = json.loads((ROOT / "tests/fixtures/adapters/capella/task_review.json").read_text())
    sent = []
    monkeypatch.setattr(http, "get", lambda url, **kw: review)
    monkeypatch.setattr(http, "request", lambda method, url, **kw: sent.append((method, url, kw.get("body"))) or {"status": "approved"})
    req = irlpls.make_request("new radar image of the port of Boston within 3 days", geocode=False, lat=42.35, lon=-71.05,
                              country_code="US")
    job = broker.Job(req.need, req, irlpls.Policy.from_dict(policy) if policy else None)
    job.state, job.choice, job.provider_response = "submitted", {"card_id": "capella"}, review
    return job.refresh(), sent


def test_broker_capella_estimate_needs_an_explicit_approval(monkeypatch):
    job, sent = _capella_job(monkeypatch)
    assert job.state == "in_flight" and job.provider_status == "review"
    assert job.approvals == [{"id": "estimate", "amount": "1000", "currency": "USD", "label": job.approvals[0]["label"]}]
    assert job.to_dict()["approvals"] == job.approvals
    dry = job.approve()
    assert dry["dry_run"] and dry["would_send"]["method"] == "PATCH" and not sent
    with pytest.raises(ValueError, match="idempotency_key"):
        job.approve(confirm=True)
    with pytest.raises(ValueError, match="total=1000"):
        job.approve(confirm=True, idempotency_key="ok-1", total="900")
    assert not sent
    job.approve(confirm=True, idempotency_key="ok-1", total="1000.00")
    assert sent == [("PATCH", "https://api.capellaspace.com/task/45b0e94b-6107-4671-ab13-d526e45d88c2/status", {"status": "approved"})]
    assert job.approvals == [] and "capella-secret-123" not in json.dumps(job.to_dict(), default=str)


def test_standing_mandate_never_approves_and_ceiling_still_applies(monkeypatch):
    job, sent = _capella_job(monkeypatch, {"max_order_usd": 500, "auto_submit": True})
    job.refresh().refresh()
    assert job.approvals and not sent                         # polling never accepts a price
    with pytest.raises(irlpls.PolicyViolation):
        job.approve(confirm=True, idempotency_key="k", total="1000")   # over max_order_usd even with a yes
    assert not sent


def test_globhe_quote_approval_keeps_its_currency(monkeypatch):
    g = adapters.get("globhe")
    resp = json.loads((ROOT / "tests/fixtures/adapters/globhe/getorder_quote.json").read_text())
    opts = g.pending_approvals(resp)
    assert opts and all(o["id"] and o["currency"] for o in opts)
    monkeypatch.setenv("GLOBHE_API_KEY", "globhe-secret")
    d = g.approve({"id": "o1"}, approval_id=opts[0]["id"])
    assert d["dry_run"] and d["would_send"]["body"] == {"status": "accept"} and "globhe-secret" not in json.dumps(d)
    with pytest.raises(ValueError):
        g.approve({"id": "o1"})
    with pytest.raises(NotSupported):
        Adapter().approve({})


def test_mcp_approve_tool_is_explicit(monkeypatch):
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        pytest.skip("needs mcp >= 2")
    from irlpls import mcp_server
    assert "irlpls_approve" not in {t.name for t in asyncio.run(mcp_server.build(read_only=True).list_tools())}
    srv = mcp_server.build(read_only=False)
    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    assert "irlpls_approve" in tools and "SPENDS MONEY" in tools["irlpls_approve"].description
    job, sent = _capella_job(monkeypatch)
    broker._JOBS[job.id] = job

    def call(args):
        return json.loads(asyncio.run(srv.call_tool("irlpls_approve", args)).content[0].text)
    out = call({"job_id": job.id})
    assert out["approval_dry_run"]["dry_run"] and out["id"] == job.id and not sent
    err = call({"job_id": job.id, "confirm": True, "idempotency_key": "m-1"})       # no total
    assert err["code"] == "total_required" and "total" in err["hint"]
    done = call({"job_id": job.id, "confirm": True, "idempotency_key": "m-1", "total": "1000"})
    assert done["state"] == "in_flight" and len(sent) == 1


# ------------------------------------------------------------------------------------------ 8. prepared_calls per op

def test_prepared_calls_map_each_operation(validate):
    req = irlpls.make_request("drone photos of a farm within 7 days", geocode=False, lat=-1.29, lon=36.82, country_code="KE")
    pcs = broker._prepared_all(adapters.get("globhe"), req, "commission")
    assert set(pcs) >= {"quote", "order", "status"} and all(pcs[op]["operation"] == op for op in pcs)
    assert pcs["quote"]["safe_to_run_without_approval"] is False and pcs["status"]["safe_to_run_without_approval"] is True
    assert broker._prepared_all(adapters.get("getterdone"), req, "live") == {}   # takes no operation: nothing invented
    job = irlpls.request("drone survey photos of a solar farm near Austin, TX within 7 days", geocode=False, lat=30.27,
                         lon=-97.74, country_code="US", state="TX")
    items = [o for o in job.plan if o.get("prepared_calls")]
    assert items
    for o in items:
        if o.get("prepared_call"):
            assert o["prepared_calls"][o["prepared_call"]["operation"]] == o["prepared_call"]
    validate(json.loads(json.dumps(job.to_dict(), default=str)), json.loads((ROOT / "schema/job.schema.json").read_text())["$id"])


# ------------------------------------------------------------------------------------------- 9. wallet catalog state

def test_wallet_adapters_have_their_own_state_and_never_look_keyless():
    ad = catalog.adapters()
    for cid in ("vangrid", "execution-market"):
        st = catalog.adapter_state(irlpls.card(cid), ad)
        assert st["state"] == "sdk-callable-wallet" and "No key needed" not in st["note"]
        assert adapters.get(cid).needs_wallet and not adapters.get(cid).configured()
    assert catalog.adapter_state(irlpls.card("nws"), ad)["state"] == "sdk-callable-no-key"
    from irlpls.adapters import _a7
    assert _a7.WALLET_SIGNING is False
    job = irlpls.request("someone check if the cafe at 1 Main St, Austin, TX is open within 3 hours", geocode=False,
                         lat=30.27, lon=-97.74, country_code="US", state="TX")
    for o in job.plan:
        if o["card_id"] in ("vangrid", "execution-market"):
            assert o["sdk"]["needs_wallet"] and o["sdk"]["needs_key"]
