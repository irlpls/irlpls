"""A7 adapters: Vangrid, Execution Market, Defined.ai, Truepic Vision. Offline: irlpls.http.request is replaced by a
router over recorded or documented-shape fixtures (tests/fixtures/adapters/<card>/SOURCE says which)."""
import base64
import json
import uuid
from pathlib import Path

import pytest

import irlpls
import irlpls.adapters
from irlpls import http
from irlpls.adapters import _a7
from irlpls.adapters.base import MissingCredentials, NotSupported
from irlpls.adapters.defined_ai import DefinedAI
from irlpls.adapters.execution_market import FIELDS, ExecutionMarket
from irlpls.adapters.truepic_vision import TruepicVision
from irlpls.adapters.vangrid import PAY_TO, Vangrid

FX = Path(__file__).parent / "fixtures" / "adapters"
SECRET = "SEKRET-do-not-leak-9f3a"
CARDS = ("vangrid", "execution-market", "defined-ai", "truepic-vision")
REQ = {"need": "Film the loading dock at 1 Main St, Austin, TX, both directions, in daylight", "place": "1 Main St, Austin, TX",
       "lat": 30.27, "lon": -97.74, "max_price_usd": 200, "quantity": 1}


def fx(card, name):
    p = FX / card / name
    return p.read_text() if name.endswith(".b64") else json.loads(p.read_text())


class Router:
    """Fake irlpls.http.request: routes (method, url prefix) to a response or an HTTPError, and records every call."""
    def __init__(self, routes=()):
        self.routes = list(routes)
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, body=None, form=None, **kw):
        if params:
            url = url + ("&" if "?" in url else "?") + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body, "form": form})
        for m, prefix, resp in self.routes:
            if m == method and url.startswith(prefix):
                r = resp(self.calls[-1]) if callable(resp) else resp
                if isinstance(r, http.HTTPError):
                    raise r
                return r
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def net(monkeypatch):
    r = Router()
    monkeypatch.setattr(http, "request", r)
    return r


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("DEFINED_AI_CLIENT_ID", "DEFINED_AI_CLIENT_SECRET", "DEFINED_AI_SOURCE_PROJECT_ID", "TRUEPIC_CLIENT_ID",
              "TRUEPIC_CLIENT_SECRET", "TRUEPIC_REGION", "TRUEPIC_TEAM_ID", "TRUEPIC_INSPECTION_TYPE_ID"):
        monkeypatch.delenv(v, raising=False)
    DefinedAI._tokens.clear()
    TruepicVision._tokens.clear()
    yield


def err(status, body="", headers=None):
    return http.HTTPError(status, "https://x.invalid/", body, headers=headers)


# ---------------------------------------------------------------------------------------------------- registry + shape

def test_registered_commission_only_and_cards_agree():
    for cid in CARDS:
        a = irlpls.adapters.get(cid)
        assert a and a.can_commission and not a.can_live, cid
        assert irlpls.card(cid)["delivery"]["modes"] == ["commission"]
    assert irlpls.adapters.get("vangrid").can_cancel and irlpls.adapters.get("truepic-vision").can_cancel
    assert not irlpls.adapters.get("execution-market").can_cancel   # cancelling needs the wallet's signature


@pytest.mark.parametrize("a", [Vangrid(), ExecutionMarket(), DefinedAI(), TruepicVision()], ids=lambda a: a.card_id)
def test_prepare_is_offline_and_well_formed(a, net, monkeypatch):
    assert a.prepare(REQ, "live") is None
    pc = a.prepare(REQ, "commission")
    assert pc["operation"] == "order" and pc["method"] == "POST" and pc["url"].startswith("https://")
    assert pc["safe_to_run_without_approval"] is False and pc["docs_url"].startswith("https://")
    ops = a.prepared_calls(REQ)
    assert ops["order"] == pc
    for op, c in ops.items():
        assert c["operation"] == op
        assert c["safe_to_run_without_approval"] is (op in ("read", "status"))
    assert net.calls == []                                  # nothing was sent


def test_prepared_auth_kinds():
    v = Vangrid().prepared_calls(REQ, sha256="ab" * 32, bounty_id="b1")
    assert v["order"]["auth"]["kind"] == "wallet" and v["order"]["headers"]["PAYMENT-SIGNATURE"] == "{X402_PAYMENT_SIGNATURE}"
    assert v["order"]["body"]["amount_usdc"] == 200 and v["quote"]["auth"] == {"kind": "none"}
    assert "PAYMENT-SIGNATURE" not in v["quote"]["headers"]
    assert v["read"]["url"].endswith("/provenance/" + "ab" * 32) and v["read"]["auth"] == {"kind": "none"}
    assert v["status"]["headers"]["Authorization"] == "Bearer {VANGRID_AGENT_TOKEN}"
    e = ExecutionMarket().prepared_calls(REQ, task_id="438661c3-e768-4750-89b5-af34275bb79a")
    assert e["order"]["auth"]["kind"] == "wallet" and set(e["order"]["body"]) <= set(FIELDS)
    assert e["order"]["body"]["location_lat"] == 30.27 and e["order"]["body"]["evidence_required"] == ["photo_geo"]
    assert e["read"]["url"].startswith("https://api.execution.market/api/v1/tasks/available?") and "lat=30.27" in e["read"]["url"]
    d = DefinedAI().prepared_calls(REQ)
    assert d["order"]["auth"]["kind"] == "oauth2" and "{DEFINED_AI_SOURCE_PROJECT_ID}" in d["order"]["url"]
    assert "connect/token" in d["order"]["notes"] and d["order"]["placeholders"][0]["name"] == "DEFINED_AI_SOURCE_PROJECT_ID"
    t = TruepicVision().prepared_calls(REQ)
    names = {p["name"] for p in t["order"]["placeholders"]}
    assert {"TRUEPIC_TEAM_ID", "TRUEPIC_INSPECTION_TYPE_ID", "CUSTOMER_FIRST_NAME", "CUSTOMER_PHONE_NUMBER"} <= names
    assert t["order"]["body"]["send_methods"] == {"text": True, "email": False}
    assert "vision-auth.truepic.com/oauth/token" in t["order"]["notes"]


def test_plan_carries_a_prepared_call_for_these_adapters():
    from irlpls import broker
    job = irlpls.request("photo of the storefront at 123 Main St, Springfield, IL this week", geocode=False,
                         lat=39.80, lon=-89.65, country_code="us")
    for cid in ("vangrid", "execution-market"):
        pc = broker._prepared(irlpls.adapters.get(cid), job.request, "commission")
        assert pc and pc["card_id"] == cid and pc["auth"]["kind"] == "wallet"


def test_secrets_never_appear_in_outputs(net, monkeypatch):
    monkeypatch.setenv("DEFINED_AI_CLIENT_ID", "cid")
    monkeypatch.setenv("DEFINED_AI_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("TRUEPIC_CLIENT_ID", "cid")
    monkeypatch.setenv("TRUEPIC_CLIENT_SECRET", SECRET)
    spec = {**REQ, "amount_usdc": 200, "bounty_usd": 5, "source_project_id": str(uuid.uuid4()), "team_id": 1,
            "inspection_type_id": 2, "customer_first_name": "the owner", "customer_phone_number": "+15550100"}
    out = []
    for a in (Vangrid(), ExecutionMarket(), DefinedAI(), TruepicVision()):
        out += [a.prepare(REQ, "commission"), a.prepared_calls(REQ), a.commission(spec)]
    blob = json.dumps(out)
    assert SECRET not in blob and net.calls == []
    # a refused token request names neither the secret nor the URL's query
    net.routes = [("POST", "https://vision-auth.truepic.com/oauth/token", err(401, f'{{"error":"bad {SECRET}"}}'))]
    with pytest.raises(MissingCredentials) as e:
        TruepicVision().inspections()
    assert SECRET not in str(e.value)
    # the bounty token is sent, but never returned or echoed in an error
    net.routes = [("GET", "https://data.vangrid.io/api/v1/bounties/", err(401, f"token {SECRET} does not match"))]
    with pytest.raises(MissingCredentials) as e:
        Vangrid().status({"bounty_id": "b1", "agent_token": SECRET})
    assert SECRET not in str(e.value) and net.calls[-1]["headers"]["Authorization"] == "Bearer " + SECRET


# ------------------------------------------------------------------------------------------------------------- Vangrid

def test_vangrid_provenance_read_recorded(net):
    net.routes = [("GET", "https://data.vangrid.io/api/v1/provenance/", fx("vangrid", "provenance.json"))]
    o = Vangrid().provenance("sha256:CF9642DAE244A213BC9C0E6D325A84BE44FE8FD1F57D9B362C64493769B74A48")
    assert net.calls[0]["url"].endswith("/provenance/cf9642dae244a213bc9c0e6d325a84be44fe8fd1f57d9b362c64493769b74a48")
    assert o["value"] is True and o["phenomenon_time"] == "2026-09-24T06:31:34.000Z" and o["geohash"] == "xn76e4"
    assert abs(o["lat"] - 35.6094) < 0.001 and abs(o["lon"] - 139.7076) < 0.001 and o["location_precision_km"] < 1
    assert o["anchor"]["chain_id"] == 8453 and o["url"].startswith("https://base.easscan.org/")
    with pytest.raises(ValueError):
        Vangrid().provenance("not-a-hash")


def test_vangrid_dry_run_quotes_the_buyers_own_amount(net):
    v = Vangrid()
    dry = v.commission({"brief": REQ["need"], "amount_usdc": 200, "within_minutes": 3 * 1440})
    assert dry["dry_run"] and dry["quote"] == {**dry["quote"], "total": 200, "currency": "USDC", "firm": True}
    assert dry["would_send"]["body"] == {"title": REQ["need"], "brief": REQ["need"], "amount_usdc": 200, "deadline_days": 3}
    short = v.commission({"brief": "too short", "amount_usdc": 10})
    assert "quote" not in short and len(short["missing"]) == 2
    # USDC is not USD: the amount is shown in USDC, never a firm USD total (coordinator decision, 2026-10-29)
    assert irlpls.broker._quoted_total(dry) is None
    assert irlpls.broker._price_shown(dry) == {"amount": "200", "currency": "USDC", "firm": False}
    with pytest.raises(NotSupported, match="prepare-only"):
        v.commission({"brief": REQ["need"], "amount_usdc": 200}, confirm=True)
    assert net.calls == []


def test_vangrid_signed_payment_flow_checks_the_price_before_signing(net, monkeypatch):
    monkeypatch.setattr(_a7, "WALLET_SIGNING", True)
    b64 = fx("vangrid", "payment_required.b64")
    seen = []

    def signer(req):
        seen.append(req)
        return {"PAYMENT-SIGNATURE": "signed-payload", "X-Other": "dropped"}

    def bounties(call):
        if "PAYMENT-SIGNATURE" in call["headers"]:
            return {"bounty_id": "b1", "status": "paid", "amount_usdc": 200, "agent_token": "vgb_x"}
        return err(402, '{"error":"payment_required"}', {"PAYMENT-REQUIRED": b64})
    net.routes = [("POST", "https://data.vangrid.io/api/v1/bounties", bounties)]
    with pytest.raises(NotSupported, match="signer"):
        Vangrid().commission({"brief": REQ["need"], "amount_usdc": 200}, confirm=True)   # no signer, nothing sent
    assert net.calls == []
    r = Vangrid(signer=signer).commission({"brief": REQ["need"], "amount_usdc": 200}, confirm=True)
    assert r["bounty_id"] == "b1" and Vangrid().order_total(r) == 200
    assert seen[0]["scheme"] == "x402" and seen[0]["requirement"]["network"] == "eip155:8453"
    assert seen[0]["requirement"]["payTo"] == PAY_TO
    assert net.calls[-1]["headers"] == {"PAYMENT-SIGNATURE": "signed-payload"}
    # a 402 that asks for a different amount is never signed
    seen.clear()
    with pytest.raises(NotSupported, match="no offered payment"):
        Vangrid(signer=signer).commission({"brief": REQ["need"], "amount_usdc": 300}, confirm=True)
    assert seen == []


def test_vangrid_status_mapping_deliverables_and_cancel(net):
    net.routes = [("GET", "https://data.vangrid.io/api/v1/bounties/cmufahx1e00002egi0d1um822", fx("vangrid", "bounty_accepted.json")),
                  ("POST", "https://data.vangrid.io/api/v1/bounties/cmufahx1e00002egi0d1um822/cancel", {"status": "cancelling"})]
    v = Vangrid()
    order = {"bounty_id": "cmufahx1e00002egi0d1um822", "agent_token": "vgb_secret"}
    resp = v.status(order)
    assert v.map_status(v.provider_status(resp)) == "delivered"
    items = v.deliverables(resp)
    assert [i["label"] for i in items] == ["full capture", "3D model (GLB)"]
    assert all(i["phenomenon_time"] is None and i["result_time"] == "2026-09-27T10:00:00.000Z" for i in items)
    for s, want in (("open", "in_flight"), ("expired_refunded", "failed"), ("cancelled", "failed"), ("mystery", None)):
        assert v.map_status(s) == want
    assert v.cancel(order) == {"status": "cancelling"}
    assert net.calls[-1]["headers"]["Authorization"] == "Bearer vgb_secret"
    with pytest.raises(MissingCredentials):
        v.status({"bounty_id": "b1"})


# ---------------------------------------------------------------------------------------------------- Execution Market

def test_em_board_read_recorded(net):
    net.routes = [("GET", "https://api.execution.market/api/v1/tasks/available", fx("execution-market", "tasks_available.json"))]
    tasks = ExecutionMarket().board(lat=30.27, lon=-97.74, radius_km=20, limit=5)
    assert "lat=30.27" in net.calls[0]["url"] and "radius_km=20" in net.calls[0]["url"] and "limit=5" in net.calls[0]["url"]
    t = tasks[0]
    assert t["id"] == "438661c3-e768-4750-89b5-af34275bb79a" and t["bounty_usd"] == 1.0 and t["status"] == "published"
    assert t["evidence_required"] == ["text_response"] and t["url"] == "https://execution.market/task/" + t["id"]
    assert "Authorization" not in net.calls[0]["headers"]          # API-key headers are refused (403) by EM


def test_em_status_mapping_and_errors(net):
    em = ExecutionMarket()
    tid = "438661c3-e768-4750-89b5-af34275bb79a"
    net.routes = [("GET", f"https://api.execution.market/api/v1/tasks/{tid}", fx("execution-market", "task_completed.json"))]
    resp = em.status({"id": tid})
    assert em.map_status(em.provider_status(resp)) == "delivered"
    assert em.deliverables(resp)[0]["url"].endswith(tid)
    for s, want in (("published", "in_flight"), ("verifying", "in_flight"), ("expired", "failed"), ("cancelled", "failed")):
        assert em.map_status(s) == want
    with pytest.raises(ValueError):
        em.status({"id": "../admin"})
    for status, headers, check in ((410, None, lambda e: e.status == 410),
                                   (404, None, lambda e: e.status == 404),
                                   (429, {"Retry-After": "30"}, lambda e: e.retry_after_s == 30 and e.code == "rate_limited")):
        net.routes = [("GET", "https://api.execution.market/", err(status, json.dumps(fx("execution-market", "error_429.json")), headers))]
        with pytest.raises(_a7.ProviderError) as e:
            em.status({"id": tid})
        assert check(e.value), status
    net.routes = [("GET", "https://api.execution.market/", err(403))]
    with pytest.raises(MissingCredentials):
        em.status({"id": tid})


def test_em_publish_is_prepare_only_and_the_signed_path_signs_the_exact_bytes(net, monkeypatch):
    em = ExecutionMarket()
    spec = {"instructions": REQ["need"], "bounty_usd": 8, "lat": 30.27, "lon": -97.74, "within_minutes": 240,
            "idempotency_key": "k-1", "unknown_field": "dropped"}
    dry = em.commission(spec)
    body = dry["would_send"]["body"]
    assert set(body) <= set(FIELDS) and body["deadline_hours"] == 4 and body["target_executor"] == "human"
    assert dry["quote"]["firm"] is False and irlpls.broker._quoted_total(dry) is None
    assert "missing" in em.commission({"instructions": "short"})
    with pytest.raises(NotSupported, match="prepare-only"):
        em.commission(spec, confirm=True)
    assert net.calls == []
    monkeypatch.setattr(_a7, "WALLET_SIGNING", True)
    seen = []

    def signer(req):
        seen.append(req)
        return {"Signature": "eth=:sig:", "Signature-Input": "eth=(...)", "Content-Digest": "sha-256=:d:"}
    net.routes = [("GET", "https://api.execution.market/api/v1/auth/erc8128/nonce", {"nonce": "n-123"}),
                  ("POST", "https://api.execution.market/api/v1/tasks", {"id": "438661c3-e768-4750-89b5-af34275bb79a", "status": "published"})]
    r = ExecutionMarket(signer=signer).commission(spec, confirm=True)
    assert r["status"] == "published"
    assert seen[0]["scheme"] == "erc8128" and seen[0]["nonce"] == "n-123" and seen[0]["body"] == json.dumps(body).encode()
    sent = net.calls[-1]
    assert sent["body"] == body and sent["headers"]["Signature"] == "eth=:sig:" and sent["headers"]["X-Idempotency-Key"] == "k-1"


# ---------------------------------------------------------------------------------------------------------- Defined.ai

def _defined_env(monkeypatch):
    monkeypatch.setenv("DEFINED_AI_CLIENT_ID", "cid")
    monkeypatch.setenv("DEFINED_AI_CLIENT_SECRET", SECRET)


def test_defined_reads_with_a_client_credentials_token(net, monkeypatch):
    d = DefinedAI()
    assert not d.configured()
    with pytest.raises(MissingCredentials):
        d.projects()
    _defined_env(monkeypatch)
    assert d.configured()
    base = "https://api.production-na01.definedcrowd.com/v2.0/public"
    net.routes = [("POST", "https://auth.definedcrowd.com/connect/token", fx("defined-ai", "token.json")),
                  ("GET", base + "/projects", fx("defined-ai", "projects.json"))]
    p = d.projects()
    assert p[0]["name"] == "Swahili read speech"
    tok = net.calls[0]
    assert tok["form"]["grant_type"] == "client_credentials" and tok["form"]["scope"] == "PublicAPIv2"
    assert net.calls[1]["headers"]["Authorization"] == "Bearer eyJ.defined.token"
    d.projects()
    assert sum(c["url"].endswith("/connect/token") for c in net.calls) == 1        # token reused


def test_defined_order_status_and_deliverables(net, monkeypatch):
    _defined_env(monkeypatch)
    d = DefinedAI()
    src = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    dry = d.commission({"project_name": "Swahili read speech"})
    assert dry["missing"] and dry["would_send"]["url"].endswith("/{DEFINED_AI_SOURCE_PROJECT_ID}/clone")
    with pytest.raises(ValueError):
        d.commission({"project_name": "x"}, confirm=True)
    assert net.calls == []
    base = "https://api.production-na01.definedcrowd.com/v2.0/public"
    pid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    net.routes = [("POST", "https://auth.definedcrowd.com/connect/token", fx("defined-ai", "token.json")),
                  ("POST", f"{base}/projects/{src}/clone", fx("defined-ai", "clone.json")),
                  ("GET", f"{base}/projects/{pid}/status", fx("defined-ai", "status.json")),
                  ("GET", f"{base}/projects/{pid}/deliverables", fx("defined-ai", "deliverables.json"))]
    spec = {"project_name": "Swahili read speech", "source_project_id": src, "idempotency_key": "order-1"}
    order = d.commission(spec, confirm=True)
    sent = net.calls[1]["body"]
    assert sent["projectName"] == "Swahili read speech" and sent["targetProjectId"] == str(uuid.uuid5(_ns(), "order-1"))
    assert d.commission(spec)["would_send"]["body"]["targetProjectId"] == sent["targetProjectId"]   # stable on retry
    assert order["id"] == pid
    resp = d.status(order)
    assert d.provider_status(resp) == "Running" and d.map_status("Running") is None   # undocumented: stays in_flight
    items = d.deliverables(resp)
    assert items[0]["label"] == "batch-001.zip" and items[0]["phenomenon_time"] is None and items[0]["partial"] is True
    assert items[0]["url"].endswith(f"/projects/{pid}/deliverables/9b2d1c3e-1111-4222-8333-444455556666/download")


def _ns():
    from irlpls.adapters.defined_ai import ID_NS
    return ID_NS


def test_defined_error_mapping(net, monkeypatch):
    _defined_env(monkeypatch)
    base = "https://api.production-na01.definedcrowd.com/v2.0/public"
    for status, exc in ((401, MissingCredentials), (403, MissingCredentials), (404, _a7.ProviderError), (429, _a7.ProviderError)):
        DefinedAI._tokens.clear()
        net.routes = [("POST", "https://auth.definedcrowd.com/connect/token", fx("defined-ai", "token.json")),
                      ("GET", base, err(status, "", {"Retry-After": "12"}))]
        with pytest.raises(exc) as e:
            DefinedAI().projects()
        if status == 429:
            assert e.value.retry_after_s == 12
        assert SECRET not in str(e.value)


# ------------------------------------------------------------------------------------------------------ Truepic Vision

def _truepic_env(monkeypatch):
    monkeypatch.setenv("TRUEPIC_CLIENT_ID", "cid")
    monkeypatch.setenv("TRUEPIC_CLIENT_SECRET", SECRET)


SPEC_TP = {"team_id": 7, "inspection_type_id": 12, "customer_first_name": "the owner", "customer_email_address": "mo@example.com",
           "place": "1 Main St, Austin, TX", "notes": "Photograph the loading dock", "lat": 30.27, "lon": -97.74}


def test_truepic_order_is_a_dry_run_until_confirmed(net, monkeypatch):
    t = TruepicVision()
    dry = t.commission({"notes": "photo"})
    assert {m.split(" ")[0] for m in dry["missing"]} >= {"team_id", "inspection_type_id"}
    ok = t.commission(SPEC_TP)
    assert "missing" not in ok and ok["would_send"]["body"]["send_methods"] == {"text": False, "email": True}
    assert ok["would_send"]["body"]["customer_address_lat"] == "30.270000" and "quote" not in ok
    assert net.calls == []
    _truepic_env(monkeypatch)
    net.routes = [("POST", "https://vision-auth.truepic.com/oauth/token", fx("truepic-vision", "token.json")),
                  ("POST", "https://vision-api.truepic.com/v3/inspections", fx("truepic-vision", "inspection_created.json"))]
    order = t.commission(SPEC_TP, confirm=True)
    tok = net.calls[0]
    assert tok["body"] == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": SECRET,
                           "audience": "https://vision-api.truepic.com"} and tok["form"] is None     # JSON, as documented
    assert order["id"] == 1843 and t.map_status(t.provider_status(order)) == "in_flight"


def test_truepic_status_deliverables_cancel_and_region(net, monkeypatch):
    _truepic_env(monkeypatch)
    t = TruepicVision()
    net.routes = [("POST", "https://vision-auth.truepic.com/oauth/token", fx("truepic-vision", "token.json")),
                  ("GET", "https://vision-api.truepic.com/v3/inspections/1843", fx("truepic-vision", "inspection_ready.json")),
                  ("PUT", "https://vision-api.truepic.com/v3/inspections/1843", {"result": {"status": {"id": 6}}})]
    resp = t.status({"id": 1843})
    assert t.provider_status(resp) == "STATUS_READY_FOR_REVIEW" and t.map_status("STATUS_READY_FOR_REVIEW") == "delivered"
    for s, want in (("STATUS_INSPECTION_CLOSED", "delivered"), ("STATUS_CANCELED", "failed"), ("STATUS_PROCESSING", "in_flight")):
        assert t.map_status(s) == want
    items = t.deliverables(resp)
    assert len(items) == 2                                   # the deleted photo is left out
    p, v = items
    assert p["kind"] == "photo" and p["phenomenon_time"] == "2026-10-28T11:05:00.000Z" and p["lat"] == 30.2672
    assert p["verification"] == "pass" and p["gps_accuracy_m"] == 5.0 and p["url"].startswith("https://vision-media.truepic.com/o/501")
    assert v["kind"] == "video" and v.get("lat") is None and v["is_existing_image"] is True
    assert t.provider_status({"result": {"status": {"id": 6}}}) == "STATUS_CANCELED"
    t.cancel({"id": 1843})
    assert net.calls[-1]["method"] == "PUT" and net.calls[-1]["body"] == {"inspection_status_id": 6}
    with pytest.raises(ValueError):
        t.status({"id": "1843/../x"})
    monkeypatch.setenv("TRUEPIC_REGION", "uk")
    assert TruepicVision().prepare(REQ, "commission")["url"] == "https://vision-api.truepic.uk/v3/inspections"


def test_truepic_error_mapping(net, monkeypatch):
    _truepic_env(monkeypatch)
    for status, exc in ((401, MissingCredentials), (403, MissingCredentials), (404, _a7.ProviderError), (429, _a7.ProviderError)):
        TruepicVision._tokens.clear()
        net.routes = [("POST", "https://vision-auth.truepic.com/oauth/token", fx("truepic-vision", "token.json")),
                      ("GET", "https://vision-api.truepic.com/v3/inspections", err(status, "", {"Retry-After": "10"}))]
        with pytest.raises(exc) as e:
            TruepicVision().status({"id": 5})
        if status == 429:
            assert e.value.retry_after_s == 10 and "retry after 10 s" in str(e.value)
        assert SECRET not in str(e.value)


# ------------------------------------------------------------------------------------------------------------- helpers

def test_geohash_and_payment_required_helpers():
    assert _a7.geohash_center("ezs42")[:2] == pytest.approx((42.605, -5.603), abs=0.001)   # the reference example
    lat, lon, dh, dw = _a7.geohash_center("xn76e4")
    assert abs(lat - 35.6094) < 0.001 and abs(lon - 139.7076) < 0.001 and dh < 0.003
    assert _a7.geohash_center("aaai") is None and _a7.geohash_center("") is None
    pr = _a7.decode_payment_required(fx("vangrid", "payment_required.b64"))
    assert pr == fx("vangrid", "payment_required_decoded.json")
    assert _a7.decode_payment_required("%%%") is None
    bad = {"accepts": [dict(pr["accepts"][0], payTo="0x000000000000000000000000000000000000dEaD")]}
    with pytest.raises(NotSupported):
        _a7.pick_requirement("vangrid", bad, 200, PAY_TO, {"eip155:8453": pr["accepts"][0]["asset"]})
    wrong_asset = {"accepts": [dict(pr["accepts"][0], asset="0x0000000000000000000000000000000000000001")]}
    with pytest.raises(NotSupported):
        _a7.pick_requirement("vangrid", wrong_asset, 200, PAY_TO, {"eip155:8453": pr["accepts"][0]["asset"]})
    assert base64.b64decode(fx("vangrid", "payment_required.b64"))
