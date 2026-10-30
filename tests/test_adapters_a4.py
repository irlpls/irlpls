"""A4 agent-marketplace adapters: Human For AI, Errand, GetterDone, Mundane. Offline: every HTTP call goes through a fake
irlpls.http.request that answers from tests/fixtures/adapters/<card>/ (recorded keyless reads, or documented-shape
fixtures; see each SOURCE file). No order endpoint is ever reached: the fake refuses them unless a test allows it."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

import irlpls.adapters
from irlpls import http
from irlpls.adapters import getterdone as gd_mod
from irlpls.adapters.base import MissingCredentials, NotSupported
from irlpls.protocol.shim import AdapterShim, Problem

FIX = Path(__file__).parent / "fixtures" / "adapters"
SECRET = "sk-REAL-SECRET-123"
GD_SECRET = "gd_agent_abc:gd_secret_REALSECRET"


def fx(card, name):
    return json.loads((FIX / card / name).read_text())


class Net:
    """Fake http.request: routes (method, url prefix) -> response or exception; records every call."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, body=None, form=None, **kw):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers or {}, "body": body, "form": form})
        for (m, prefix), resp in sorted(self.routes.items(), key=lambda kv: -len(kv[0][1])):
            if m == method and url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(http, "request", n)
    gd_mod._TOKENS.clear()
    return n


def err(status, body="", headers=None):
    return http.HTTPError(status, "https://provider.invalid/x", body, headers=headers)


REQ = {"need": "photo of the storefront at 42 Main St, Atlanta, GA today, is it open", "place": "42 Main St, Atlanta, GA",
       "lat": 33.749, "lon": -84.388, "within_minutes": 600, "max_price_usd": 15}
SEOUL = {"need": "photograph the queue at the pop-up near Gangnam Station", "place": "Gangnam Station exit 11",
         "lat": 37.4979, "lon": 127.0276, "within_minutes": 180}


def get(cid):
    return irlpls.adapters.get(cid)


# ------------------------------------------------------------------------------------------------- registry and cards

def test_all_four_are_registered_commission_only_and_cards_point_at_them():
    for cid, mod in (("human-for-ai", "human_for_ai"), ("errand", "errand"), ("getterdone", "getterdone"), ("mundane", "mundane")):
        a = get(cid)
        assert a is not None and a.can_commission and not a.can_live, cid
        assert irlpls.card(cid)["adapter"] == mod
    assert get("human-for-ai").env is None and get("human-for-ai").configured()
    assert get("errand").can_cancel and get("getterdone").can_cancel and get("mundane").can_cancel
    assert not get("human-for-ai").can_cancel


# ------------------------------------------------------------------------------------------------------ ready calls

def test_prepare_human_for_ai_is_the_order_call_never_safe(monkeypatch, net):
    pc = get("human-for-ai").prepare(REQ, "commission")
    assert pc["operation"] == "order" and pc["method"] == "POST" and pc["url"] == "https://humanforai.dev/api/v1/tasks"
    assert pc["safe_to_run_without_approval"] is False and pc["auth"] == {"kind": "none"}
    b = pc["body"]
    assert b["task_type"] == "real_world_verification" and b["delivery"] == "status_poll" and b["output_format"] == "text_report"
    assert b["location_detail"] == "42 Main St, Atlanta, GA" and b["deadline"].endswith("Z")
    assert "real human operator" in pc["notes"]
    assert get("human-for-ai").prepare(REQ, "live") is None
    assert net.calls == []


def test_prepare_errand_is_the_quote_with_key_placeholder(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    pc = get("errand").prepare(SEOUL, "commission")
    assert pc["operation"] == "quote" and pc["url"] == "https://errand.be/api/v1/quote"
    assert pc["headers"]["Authorization"] == "Bearer {ERRAND_API_KEY}" and pc["auth"]["kind"] == "bearer"
    assert pc["body"] == {"task_type": "queue", "lat": 37.4979, "lng": 127.0276, "reward_krw": "{REWARD_KRW}"}
    assert pc["placeholders"][0]["name"] == "REWARD_KRW" and pc["safe_to_run_without_approval"] is False
    assert SECRET not in repr(pc)
    assert get("errand").prepare({"need": "x"}, "commission") is None
    assert net.calls == []


def test_prepare_getterdone_is_the_quote_with_oauth_and_reward_from_budget(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    pc = get("getterdone").prepare(REQ, "commission")
    assert pc["operation"] == "quote" and pc["url"] == "https://getterdone.ai/api/tasks/quote"
    assert pc["auth"]["kind"] == "oauth2" and pc["auth"]["key_name"] == "GETTERDONE_API_KEY"
    assert "https://getterdone.ai/api/auth/agent/token" in pc["notes"] and "client_credentials" in pc["notes"]
    assert pc["headers"]["Authorization"] == "Bearer {GETTERDONE_ACCESS_TOKEN}"
    b = pc["body"]
    assert b["reward"] == 13.0 and b["category"] == "Photography" and b["expiresInHours"] == 10.0
    assert b["location"] == {"lat": 33.749, "lng": -84.388, "label": "42 Main St, Atlanta, GA"}
    assert "gd_secret" not in repr(pc) and "REALSECRET" not in repr(pc)
    no_budget = get("getterdone").prepare({k: v for k, v in REQ.items() if k != "max_price_usd"}, "commission")
    assert no_budget["body"]["reward"] == "{REWARD_USD}" and any(p["name"] == "REWARD_USD" for p in no_budget["placeholders"])
    assert net.calls == []


def test_prepare_mundane_is_the_free_worker_search(monkeypatch, net):
    monkeypatch.setenv("MUNDANE_API_KEY", SECRET)
    pc = get("mundane").prepare(REQ, "commission")
    assert pc["operation"] == "read" and pc["method"] == "GET" and pc["safe_to_run_without_approval"] is True
    assert pc["url"].startswith("https://api.mundane.market/v1/workers?lat=33.74900&lng=-84.38800")
    assert "capability=photo_documentation" in pc["url"] and pc["headers"] == {"Authorization": "Bearer {MUNDANE_API_KEY}"}
    assert SECRET not in repr(pc)
    assert net.calls == []


def test_reward_for_total_follows_the_fee_tiers():
    r = gd_mod.reward_for_total
    assert r(15) == 13.0 and r(22) == 20.0 and r(60) == 50.0 and r(115) == 100.0 and r(500) == 100.0
    assert r(2.5) is None and r("x") is None


# ------------------------------------------------------------------------------------------------- keyless reads

def test_human_for_ai_services_parses_the_recorded_catalogue(net):
    net.routes[("GET", "https://humanforai.dev/api/v1/services")] = fx("human-for-ai", "services.json")
    s = get("human-for-ai").services()
    assert {"real_world_verification", "local_physical_task"} <= {x["id"] for x in s} and s.warnings == []
    assert net.calls[0]["params"]["limit"] == 50


def test_errand_coverage_and_mundane_capabilities_from_recordings(net):
    net.routes[("GET", "https://errand.be/api/public/coverage")] = fx("errand", "public_coverage.json")
    cov, w = get("errand").coverage(37.4979, 127.0276)
    assert w is None and cov["dispatchable"] is False and "reachable_workers" in cov
    assert net.calls[0]["params"] == {"lat": 37.4979, "lng": 127.0276, "radius_m": 3000}
    assert "Authorization" not in net.calls[0]["headers"]
    net.routes[("GET", "https://api.mundane.market/v1/signup/capabilities")] = fx("mundane", "signup_capabilities.json")
    caps = get("mundane").capabilities()
    assert "photo_documentation" in {c["slug"] for c in caps}


def test_read_errors_become_warnings_with_retry_after(net):
    net.routes[("GET", "https://humanforai.dev/api/v1/services")] = err(429, '{"error":"rate_limited"}', {"Retry-After": "120"})
    s = get("human-for-ai").services()
    assert list(s) == [] and s.warnings[0]["code"] == "rate_limited" and s.warnings[0]["retry_after_s"] == 120
    net.routes[("GET", "https://errand.be/api/public/coverage")] = err(429, '{"error":"RATE_LIMITED","message":"slow down"}',
                                                                       {"Retry-After": "30"})
    cov, w = get("errand").coverage(37.5, 127.0)
    assert cov is None and w["code"] == "rate_limited" and w["retry_after_s"] == 30 and w["provider_code"] == "RATE_LIMITED"
    assert "slow down" not in repr(w)                     # provider free text is never copied
    net.routes[("GET", "https://api.mundane.market/v1/signup/capabilities")] = err(404)
    assert get("mundane").capabilities().warnings[0]["code"] == "not_found"
    net.routes[("GET", "https://api.mundane.market/v1/signup/capabilities")] = err(403, '{"error":"blocked"}')
    w = get("mundane").capabilities().warnings[0]
    assert w["code"] == "refused" and w["status"] == 403


# ---------------------------------------------------------------------------------------------------- dry runs

def test_human_for_ai_dry_run_sends_nothing(net):
    a = get("human-for-ai")
    d = a.commission(a.spec_from_request(REQ, "ext-1", "2026-11-01T00:00:00Z"))
    assert d["dry_run"] is True and net.calls == [] and "quote" not in d
    assert d["would_send"]["url"] == "https://humanforai.dev/api/v1/tasks"
    assert d["would_send"]["headers"] == {"Idempotency-Key": "***"} and d["would_send"]["body"]["deadline"] == "2026-11-01T00:00:00Z"
    short = a.commission({"description": "hi"})
    assert any(m.startswith("description") for m in short["missing"])
    assert any(m.startswith("contact_email") for m in a.commission({"description": "x" * 20, "delivery": "email"})["missing"])
    with pytest.raises(NotSupported):
        a.commission({"description": "hi"}, confirm=True)
    assert net.calls == []


def test_errand_dry_run_quotes_in_krw_and_never_dispatches(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    net.routes[("POST", "https://errand.be/api/v1/quote")] = fx("errand", "quote.json")
    a = get("errand")
    spec = dict(a.spec_from_request(SEOUL, None, None), reward_krw=5000)
    d = a.commission(spec)
    assert d["dry_run"] and d["quote"]["total_charge_krw"] == 6000 and d["quote"]["can_dispatch"] is True
    assert [c["url"] for c in net.calls] == ["https://errand.be/api/v1/quote"]
    assert net.calls[0]["body"] == {"task_type": "queue", "lat": 37.4979, "lng": 127.0276, "reward_krw": 5000}
    assert net.calls[0]["headers"]["Authorization"] == "Bearer " + SECRET     # the key goes to the provider only
    ws = d["would_send"]
    assert ws["headers"]["Authorization"] == "***" and SECRET not in repr(d)
    assert ws["body"]["expires_in_minutes"] == 180 and ws["body"]["radius_m"] == 3000 and "Korean" in d["note"]
    # KRW: never a firm USD total in the protocol shim
    assert AdapterShim._dry_total(d) == (None, False)
    # without a reward the dry run asks for one and still checks coverage keylessly
    monkeypatch.delenv("ERRAND_API_KEY")
    net.routes[("GET", "https://errand.be/api/public/coverage")] = fx("errand", "public_coverage.json")
    d2 = a.commission(a.spec_from_request(SEOUL))
    assert any(m.startswith("reward_krw") for m in d2["missing"]) and d2["coverage"]["radius_m"] == 3000


def test_errand_quote_errors_are_warnings_not_crashes(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    a = get("errand")
    spec = dict(a.spec_from_request(SEOUL), reward_krw=5000)
    net.routes[("POST", "https://errand.be/api/v1/quote")] = err(403, '{"error":"CAP_AREA","message":"outside"}')
    d = a.commission(spec)
    assert d["dry_run"] and d["warnings"][0]["provider_code"] == "CAP_AREA" and d["warnings"][0]["code"] == "refused"
    net.routes[("POST", "https://errand.be/api/v1/quote")] = err(401, '{"error":"UNAUTHORIZED"}')
    d = a.commission(spec)
    assert d["warnings"][0]["code"] == "needs_key" and SECRET not in repr(d)


def test_getterdone_dry_run_gets_a_firm_usd_sandbox_quote(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = fx("getterdone", "quote_sandbox.json")
    a = get("getterdone")
    d = a.commission(a.spec_from_request(REQ))
    assert d["dry_run"] and d["quote"]["total"] == 12 and d["quote"]["sandbox"] is True
    assert AdapterShim._dry_total(d) == (Decimal("12"), True)
    tok, q = net.calls
    assert tok["form"]["grant_type"] == "client_credentials" and tok["form"]["client_id"] == "gd_agent_abc"
    assert q["headers"]["Authorization"] == "Bearer gd_tok_fixture" and "confirmationToken" not in q["body"]
    assert "REALSECRET" not in repr(d) and "gd_tok_fixture" not in repr(d)
    assert all(c["url"] != "https://getterdone.ai/api/tasks" for c in net.calls)


def test_getterdone_non_usd_quote_is_never_firm(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    q = fx("getterdone", "quote_sandbox.json")
    q["data"]["currency"] = "EUR"
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = q
    d = get("getterdone").commission(get("getterdone").spec_from_request(REQ))
    assert "total" not in d["quote"] and d["quote"]["provider_total"] == 12 and AdapterShim._dry_total(d) == (None, False)


def test_getterdone_without_key_or_reward_sends_nothing(monkeypatch, net):
    monkeypatch.delenv("GETTERDONE_API_KEY", raising=False)
    a = get("getterdone")
    d = a.commission(a.spec_from_request({k: v for k, v in REQ.items() if k != "max_price_usd"}))
    assert any(m.startswith("reward") for m in d["missing"]) and net.calls == []
    d = a.commission(a.spec_from_request(REQ))
    assert "quote" not in d and net.calls == []
    with pytest.raises(MissingCredentials):
        a.commission(a.spec_from_request(REQ), confirm=True)


def test_getterdone_bad_credentials_and_429(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", "no-colon")
    with pytest.raises(MissingCredentials) as e:
        get("getterdone").status({"id": "t1"})
    assert "no-colon" not in str(e.value)
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = err(401, '{"error":"invalid_client"}')
    with pytest.raises(MissingCredentials) as e:
        get("getterdone").status({"id": "t1"})
    assert "REALSECRET" not in str(e.value)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = err(429, '{"success":false,"error":"rate"}', {"Retry-After": "300"})
    d = get("getterdone").commission(get("getterdone").spec_from_request(REQ))
    assert d["warnings"][0]["code"] == "rate_limited" and d["warnings"][0]["retry_after_s"] == 300


def test_getterdone_require_sandbox_refuses_a_live_agent(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    live = fx("getterdone", "quote_sandbox.json")
    live["data"].update(sandbox=False, tokenStatus="ready")
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = live
    a = get("getterdone")
    with pytest.raises(NotSupported):
        a.commission(dict(a.spec_from_request(REQ), require_sandbox=True), confirm=True)
    assert all(c["url"] != "https://getterdone.ai/api/tasks" for c in net.calls)
    # a sandbox agent: quote, then create with the confirmation token
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = fx("getterdone", "quote_sandbox.json")
    net.routes[("POST", "https://getterdone.ai/api/tasks")] = fx("getterdone", "task_created.json")
    resp = a.commission(dict(a.spec_from_request(REQ), require_sandbox=True, sandbox_scenario="bad-proof"), confirm=True)
    created = net.calls[-1]
    assert created["url"] == "https://getterdone.ai/api/tasks" and created["body"]["confirmationToken"] == "qc_fixture"
    assert created["body"]["sandboxScenario"] == "bad-proof"
    assert resp["data"]["id"].startswith("sbx_") and a.order_total(resp) == Decimal("12")
    assert a.provider_status(resp) == "open" and a.map_status("open") == "in_flight"


def test_mundane_dry_run_searches_workers_and_checks_the_offer(monkeypatch, net):
    monkeypatch.setenv("MUNDANE_API_KEY", SECRET)
    net.routes[("GET", "https://api.mundane.market/v1/workers")] = fx("mundane", "workers.json")
    net.routes[("GET", "https://api.mundane.market/v1/offer-check")] = fx("mundane", "offer_check.json")
    a = get("mundane")
    d = a.commission(a.spec_from_request(REQ, "ext-9", "2026-11-01T00:00:00Z"))
    assert d["dry_run"] and d["quote"]["workers_found"] == 2 and d["quote"]["lowest_ask_minor"] == 1200
    assert d["quote"]["offer_check"] == {"ok": True, "reasons": []}
    assert AdapterShim._dry_total(d) == (None, False)          # the fee shows only in the offer response
    assert [c["method"] for c in net.calls] == ["GET", "GET"]
    assert net.calls[1]["params"] == {"total_minor": 1500, "new_task": "true"}
    body = d["would_send"]["body"]
    assert body["budget_max_minor"] == 1500 and body["deadline"] == "2026-11-01T00:00:00Z" and body["currency"] == "USD"
    assert body["idempotency_key"] == "ext-9" and body["required_capabilities"] == ["photo_documentation"]
    assert d["then"]["body"]["idempotency_key"] == "ext-9-offer" and SECRET not in repr(d)
    nodeadline = a.commission({"instructions": "photo", "lat": 1, "lon": 2})
    assert {m.split(":")[0] for m in nodeadline["missing"]} >= {"budget_max_minor", "deadline"}


def test_mundane_confirm_posts_then_offers_only_with_a_named_worker(monkeypatch, net):
    monkeypatch.setenv("MUNDANE_API_KEY", SECRET)
    net.routes[("POST", "https://api.mundane.market/v1/tasks")] = fx("mundane", "task_created.json")
    net.routes[("POST", "https://api.mundane.market/v1/offers")] = fx("mundane", "offer.json")
    a = get("mundane")
    spec = a.spec_from_request(REQ, "ext-9", "2026-11-01T00:00:00Z")
    r = a.commission(spec, confirm=True)
    assert r["task_id"] == "t_42" and "offer" not in r and "next" in r and len(net.calls) == 1
    assert a.order_total(r) is None
    r = a.commission(dict(spec, worker_id="w_2", amount_minor=1500), confirm=True)
    assert net.calls[-1]["body"] == {"task_id": "t_42", "worker_id": "w_2", "amount_minor": 1500, "currency": "USD",
                                     "idempotency_key": "ext-9-offer"}
    assert a.order_total(r) == Decimal("15.75") and a.provider_status(r) == "open"


# ------------------------------------------------------------------------------------------- status and delivery

def test_status_maps_follow_each_providers_docs():
    h, e, g, m = get("human-for-ai"), get("errand"), get("getterdone"), get("mundane")
    assert [h.map_status(s) for s in ("submitted", "accepted", "delivered", "rejected", "weird")] == \
        ["in_flight", "in_flight", "delivered", "failed", None]
    assert [e.map_status(s) for s in ("open", "arrived", "submitted", "completed", "failed", "expired", "cancelled")] == \
        ["in_flight"] * 3 + ["delivered"] + ["failed"] * 3
    assert [g.map_status(s) for s in ("open", "claimed", "payout_pending", "contested", "completed", "resolved", "canceled")] == \
        ["in_flight"] * 4 + ["delivered", "failed", "failed"]
    assert g.map_status("suspended") is None
    assert [m.map_status(s) for s in ("open", "screening", "rejected", "completed", "assigned")] == \
        ["in_flight", "in_flight", "failed", "delivered", None]


def test_human_for_ai_status_and_deliverable_with_receipt(net):
    net.routes[("GET", "https://humanforai.dev/api/v1/tasks/HFAI-2026-97BBAB04C15D2E63")] = fx("human-for-ai", "task_delivered.json")
    a = get("human-for-ai")
    r = a.status({"task_id": "HFAI-2026-97BBAB04C15D2E63", "status": "submitted"})
    assert a.map_status(a.provider_status(r)) == "delivered"
    [o] = a.deliverables(r)
    assert o["kind"] == "report" and o["value"].startswith("Product on shelf") and o["phenomenon_time"] is None
    assert o["result_time"] == "2026-10-28T15:30:00Z" and o["receipt"].count(".") == 2 and len(o["deliverable_sha256"]) == 64
    assert "not an irlpls receipt" in o["receipt_kind"]
    assert a.deliverables(fx("human-for-ai", "task_submitted.json")) == []
    net.routes[("GET", "https://humanforai.dev/api/v1/tasks/nope")] = err(404, '{"error":"not_found"}')
    with pytest.raises(http.HTTPError):
        a.status({"task_id": "nope"})


def test_errand_status_fetches_the_result_once_completed(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    net.routes[("GET", "https://errand.be/api/v1/dispatches/d_123")] = fx("errand", "dispatch_completed.json")
    net.routes[("GET", "https://errand.be/api/v1/dispatches/d_123/result")] = fx("errand", "result.json")
    a = get("errand")
    r = a.status(fx("errand", "dispatch_created.json"))
    assert a.map_status(a.provider_status(r)) == "delivered"
    photo, answer = a.deliverables(r)
    assert photo["kind"] == "photo" and photo["phenomenon_time"] == "2026-09-08T14:12:33+09:00"
    assert (photo["lat"], photo["lon"]) == (37.498, 127.0275) and photo["checks"]["exif_gps_present"] is True
    assert photo["verdict"]["passed"] is True and "expires in 1 h" in photo["label"]
    assert answer["label"] == "wait_count" and answer["value"] == 12 and answer["url"] == "https://errand.be/r/abc"


def test_errand_status_errors_map_401_to_missing_credentials_and_keep_429(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    a = get("errand")
    net.routes[("GET", "https://errand.be/api/v1/dispatches/d_1")] = err(401, '{"error":"UNAUTHORIZED"}')
    with pytest.raises(MissingCredentials) as e:
        a.status({"dispatch_id": "d_1"})
    assert SECRET not in str(e.value) and "ERRAND_API_KEY" in str(e.value)
    net.routes[("GET", "https://errand.be/api/v1/dispatches/d_1")] = err(429, "", {"Retry-After": "60"})
    with pytest.raises(http.HTTPError) as e:
        a.status({"dispatch_id": "d_1"})
    assert e.value.status == 429 and e.value.headers["Retry-After"] == "60"
    monkeypatch.delenv("ERRAND_API_KEY")
    with pytest.raises(MissingCredentials):
        a.status({"dispatch_id": "d_1"})


def test_cancel_calls_hit_the_documented_paths(monkeypatch, net):
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    monkeypatch.setenv("MUNDANE_API_KEY", SECRET)
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://errand.be/api/v1/dispatches/d_123/cancel")] = {"status": "cancelled"}
    net.routes[("POST", "https://api.mundane.market/v1/tasks/t_42/cancel")] = None
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    net.routes[("POST", "https://getterdone.ai/api/tasks/sbx_task_xyz789/cancel")] = {"success": True, "data": {"status": "canceled"}}
    assert get("errand").cancel({"dispatch_id": "d_123"})["status"] == "cancelled"
    get("mundane").cancel({"task_id": "t_42"})
    assert get("getterdone").cancel(fx("getterdone", "task_created.json"))["data"]["status"] == "canceled"
    assert [c["url"].rsplit("/", 1)[-1] for c in net.calls if c["url"].endswith("cancel")] == ["cancel"] * 3
    with pytest.raises(NotSupported):
        get("human-for-ai").cancel({"task_id": "x"})


def test_getterdone_status_and_proof(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    net.routes[("GET", "https://getterdone.ai/api/tasks/sbx_task_xyz789")] = fx("getterdone", "task_completed.json")
    a = get("getterdone")
    r = a.status(fx("getterdone", "task_created.json"))
    assert a.map_status(a.provider_status(r)) == "delivered"
    photo, text = a.deliverables(r)
    assert photo["kind"] == "photo" and photo["url"].endswith("/proof-media/images/0") and photo["phenomenon_time"] is None
    assert photo["sandbox"] is True and photo["criteria_check"] == {"passed": True} and (photo["lat"], photo["lon"]) == (33.749, -84.388)
    assert text["kind"] == "report" and "Joe's Pizza" in text["value"]
    # the token is fetched once and cached
    a.status({"id": "sbx_task_xyz789"})
    assert sum(1 for c in net.calls if c["url"].endswith("/token")) == 1


def test_mundane_status_attaches_proof_links_when_completed(monkeypatch, net):
    monkeypatch.setenv("MUNDANE_API_KEY", SECRET)
    net.routes[("GET", "https://api.mundane.market/v1/tasks/t_42")] = fx("mundane", "task_completed.json")
    net.routes[("GET", "https://api.mundane.market/v1/tasks/t_42/proof-media")] = {"items": [{"url": "https://media.mundane.market/s/p1.jpg"}]}
    a = get("mundane")
    r = a.status({"task_id": "t_42", "task": {"task_id": "t_42"}})
    assert a.map_status(a.provider_status(r)) == "delivered"
    [p] = a.deliverables(r)
    assert p["url"].endswith("p1.jpg") and p["phenomenon_time"] is None and p["task_id"] == "t_42"
    net.routes[("GET", "https://api.mundane.market/v1/tasks/t_42/proof-media")] = err(429, "", {"Retry-After": "10"})
    r = a.status({"task_id": "t_42"})
    assert r["warnings"][0]["code"] == "rate_limited" and a.deliverables(r) == []
    net.routes[("GET", "https://api.mundane.market/v1/tasks/t_42")] = err(401)
    with pytest.raises(MissingCredentials):
        a.status({"task_id": "t_42"})


# ------------------------------------------------------------------------------------------------ protocol shim

def test_shim_quote_uses_the_adapters_spec_hook(monkeypatch, net):
    monkeypatch.setenv("GETTERDONE_API_KEY", GD_SECRET)
    net.routes[("POST", "https://getterdone.ai/api/auth/agent/token")] = fx("getterdone", "token.json")
    net.routes[("POST", "https://getterdone.ai/api/tasks/quote")] = fx("getterdone", "quote_sandbox.json")
    s = AdapterShim("getterdone")
    q = s.quote({"request": {**REQ, "mode": "commission"}}).body
    offer = q["offers"][0]
    assert offer["firm"] is True and offer["total"]["amount"] in ("12", "12.00")
    assert "REALSECRET" not in json.dumps(q)
    # Errand without a reward: the shim asks for it instead of guessing
    monkeypatch.setenv("ERRAND_API_KEY", SECRET)
    net.routes[("GET", "https://errand.be/api/public/coverage")] = fx("errand", "public_coverage.json")
    with pytest.raises(Problem) as e:
        AdapterShim("errand").quote({"request": {**SEOUL, "mode": "commission"}})
    assert e.value.code == "needs_input"


def test_shim_quote_for_human_for_ai_is_indicative_and_offline(net):
    q = AdapterShim("human-for-ai").quote({"request": {**REQ, "mode": "commission"}}).body
    assert q["offers"][0]["firm"] is False and "total" not in q["offers"][0] and net.calls == []
