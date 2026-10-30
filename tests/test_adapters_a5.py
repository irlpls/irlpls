"""Batch A5, crowd platforms: Toloka, clickworker (LXT), Hire a Human. No network: http.get/post are replaced with
fixtures recorded from each provider's documented shapes (tests/fixtures/adapters/<card>/, marked documented-shape).
Freelancer has no adapter on purpose (the owner, 2026-10-27: listed, never routed to)."""
import base64
import json
from pathlib import Path

import pytest

import irlpls
import irlpls.adapters
from irlpls import http
from irlpls.adapters.base import MissingCredentials
from irlpls.adapters.hire_a_human import HireAHuman
from irlpls.adapters.lxt_clickworker import LXTClickworker
from irlpls.adapters.toloka import Toloka

FIX = Path(__file__).parent / "fixtures" / "adapters"
SECRET = "sk-test-SECRET-9f8e7d"
PID = "pp.0192f0a1-1111-7aaa-8bbb-000000000001"


def fx(card, name):
    d = json.loads((FIX / card / name).read_text())
    assert d["_fixture"] == "documented-shape" and d["_source"].startswith("https://")
    return d["response"]


class Net:
    """Records every call; answers from a {(METHOD, url-substring): response | Exception} table."""
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, method):
        def f(url, body=None, headers=None, params=None, **kw):
            self.calls.append({"method": method, "url": url, "body": body, "headers": dict(headers or {}), "params": params})
            for (m, frag), resp in self.table.items():
                if m == method and frag in url:
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            raise AssertionError(f"unexpected {method} {url}")
        return f


@pytest.fixture
def net(monkeypatch):
    def install(table):
        n = Net(table)
        monkeypatch.setattr(http, "get", lambda url, **kw: n("GET")(url, **kw))
        monkeypatch.setattr(http, "post", lambda url, body=None, **kw: n("POST")(url, body=body, **kw))
        return n
    return install


@pytest.fixture
def no_net(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(http, "get", boom)
    monkeypatch.setattr(http, "post", boom)
    monkeypatch.setattr(http, "request", boom)


def no_secret(obj, secret=SECRET):
    s = json.dumps(obj, default=str)
    assert secret not in s
    assert base64.b64encode(f"me@example.com:{secret}".encode()).decode() not in s


def err(status, headers=None):
    return http.HTTPError(status, "https://provider.example/x", "body", headers=headers)


REQ = {"need": "500 first-person clips of a hand opening a push-bar door", "quantity": 500, "lat": 52.52, "lon": 13.405,
       "place": "Berlin, Germany"}


# ------------------------------------------------------------------------------------------------------------ registry

def test_registered_with_cards_and_env():
    for cid, env in (("toloka", "TOLOKA_API_KEY"), ("lxt-clickworker", "CLICKWORKER_CREDENTIALS"),
                     ("hire-a-human", "HIREAHUMAN_API_KEY")):
        a = irlpls.adapters.get(cid)
        assert a and a.env == env and a.can_commission and not a.can_live
        card = json.loads((Path(__file__).parent.parent / "cards" / f"{cid}.json").read_text())
        assert card["delivery"]["modes"] == ["commission"]
        assert card["adapter"] == type(a).__module__.rsplit(".", 1)[1]
        assert env in [x.get("env_var") for x in card["api"]["auth"]]
    assert irlpls.adapters.get("freelancer") is None   # listed, never routed to


# --------------------------------------------------------------------------------------------------------------- toloka

def test_toloka_prepare_every_operation(no_net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    a = Toloka()
    q = a.prepare(REQ, "commission")
    assert q["operation"] == "quote" and q["method"] == "POST"
    assert q["url"] == "https://platform.toloka.ai/api/v2-beta/pipelines/{TOLOKA_PIPELINE_ID}/cost-estimate"
    assert q["headers"]["Authorization"] == "ApiKey {TOLOKA_API_KEY}" and q["auth"]["kind"] == "api_key_header"
    assert q["placeholders"][0]["name"] == "TOLOKA_PIPELINE_ID" and q["safe_to_run_without_approval"] is False
    o = a.prepare(REQ, "commission", operation="order", pipeline_id=PID)
    assert o["url"].endswith(f"/pipelines/{PID}/runs") and o["body"] == {} and "placeholders" not in o
    assert o["safe_to_run_without_approval"] is False
    r = a.prepare(REQ, "commission", operation="read")
    assert r["method"] == "GET" and r["url"].endswith("/api/v2-beta/projects?limit=20") and r["safe_to_run_without_approval"]
    s = a.prepare(REQ, "commission", operation="status", run_id="run-42")
    assert s["url"].endswith("/runs/run-42") and s["safe_to_run_without_approval"]
    assert a.prepare(REQ, "live") is None
    no_secret([q, o, r, s])


def test_toloka_read_parses_projects(net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    n = net({("GET", "/projects"): fx("toloka", "projects.json")})
    r = Toloka()._call("GET", "/projects", "projects")
    assert r["projects"][0]["id"] == PID
    assert n.calls[0]["headers"]["Authorization"] == f"ApiKey {SECRET}"


def test_toloka_dry_run_without_key_or_pipeline_sends_nothing(no_net, monkeypatch):
    monkeypatch.delenv("TOLOKA_API_KEY", raising=False)
    out = Toloka().commission({"need": "x"})
    assert out["dry_run"] and set(out["missing"]) == {"pipeline_id", "TOLOKA_API_KEY"}
    assert out["would_send"]["headers"]["Authorization"] == "***"


def test_toloka_dry_run_quotes_with_the_forecast_and_never_starts_a_run(net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    n = net({("POST", "/cost-estimate"): fx("toloka", "cost_estimate.json")})
    out = Toloka().commission({"pipeline_id": PID})
    assert out["dry_run"] and out["quote"]["total"]["expected"] == "412.50"
    assert [c["url"] for c in n.calls] == [f"https://platform.toloka.ai/api/v2-beta/pipelines/{PID}/cost-estimate"]
    from irlpls.broker import _quoted_total
    assert _quoted_total(out) is None   # strings without a currency: never a firm USD offer
    no_secret(out)


def test_toloka_order_status_items_cancel(net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    n = net({("POST", "/runs/run-42/cancel"): fx("toloka", "run_canceled.json"),
             ("POST", f"/pipelines/{PID}/runs"): fx("toloka", "run_started.json"),
             ("GET", "/runs/run-42/items"): fx("toloka", "run_items.json"),
             ("GET", "/runs/run-42"): fx("toloka", "run_succeeded.json")})
    a = Toloka()
    with pytest.raises(ValueError):
        a.commission({}, confirm=True)
    order = a.commission({"pipeline_id": PID}, confirm=True)
    assert order["id"] == "run-42" and n.calls[-1]["body"] == {}
    st = a.status(order)
    assert a.provider_status(st) == "succeeded" and a.map_status("succeeded") == "delivered"
    items = a.deliverables(st)
    assert len(items) == 1 and items[0]["phenomenon_time"] is None and items[0]["result_time"] == "2026-10-28T11:40:00Z"
    assert items[0]["output"] == {"revisionId": "out-0"}
    assert a.can_cancel and a.cancel(order)["error"] == "Run canceled"
    assert a.map_status("failed") == "failed" and a.map_status("pending") == "in_flight" and a.map_status("weird") is None
    no_secret([order, st, items])


def test_toloka_status_skips_items_until_succeeded(net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    n = net({("GET", "/runs/run-42"): fx("toloka", "run_started.json")})
    st = Toloka().status({"id": "run-42"})
    assert st["items"] is None and len(n.calls) == 1 and Toloka().deliverables(st) == []


# ------------------------------------------------------------------------------------------------------- lxt-clickworker

CW = "https://api-production.clickworker.com/api/marketplace/v2"


def test_clickworker_prepare_every_operation(no_net, monkeypatch):
    monkeypatch.setenv("CLICKWORKER_CREDENTIALS", f"me@example.com:{SECRET}")
    monkeypatch.delenv("CLICKWORKER_SANDBOX", raising=False)
    a = LXTClickworker()
    r = a.prepare(REQ, "commission")
    assert r["operation"] == "read" and r["safe_to_run_without_approval"]
    assert r["url"] == f"{CW}/clickworkers?api_method=count_in_range&latitude=52.5200&longitude=13.4050&radius=10"
    assert r["auth"]["kind"] == "basic" and r["headers"]["Authorization"] == "Basic {CLICKWORKER_CREDENTIALS}"
    p = a.prepare({"need": "x"}, "commission", operation="read")
    assert p["url"] == f"{CW}/products"
    o = a.prepare(REQ, "commission", operation="order")
    assert o["method"] == "POST" and o["url"] == f"{CW}/customer/tasks" and not o["safe_to_run_without_approval"]
    link = o["body"]["task"]["template"]["link"][0]
    assert link["href"] == f"{CW}/customer/task_templates/{{CLICKWORKER_TEMPLATE_CODE}}" and link["rel"] == "task_template"
    assert o["body"]["task"]["input"]["items"] == [{"code": "{CLICKWORKER_INPUT_CODE}", "content": REQ["need"]}]
    assert {x["name"] for x in o["placeholders"]} == {"CLICKWORKER_TEMPLATE_CODE", "CLICKWORKER_INPUT_CODE"}
    s = a.prepare(REQ, "commission", operation="status", task_id=12345)
    assert s["url"] == f"{CW}/customer/tasks/12345"
    assert a.prepare(REQ, "live") is None
    monkeypatch.setenv("CLICKWORKER_SANDBOX", "1")
    assert a.prepare(REQ, "commission")["url"].startswith("https://api-sandbox.clickworker.com/api/marketplace/v2/")
    no_secret([r, p, o, s])


def test_clickworker_dry_run_never_creates_a_task(net, monkeypatch):
    monkeypatch.setenv("CLICKWORKER_CREDENTIALS", f"me@example.com:{SECRET}")
    n = net({("GET", "/clickworkers"): fx("lxt-clickworker", "worker_count.json")})
    a = LXTClickworker()
    out = a.commission({"template_code": "photo_v1", "inputs": {"address": "1 Main St"}, "idempotency_key": "order-7f3a",
                        "lat": 52.52, "lon": 13.405})
    assert out["dry_run"] and out["workers_in_range"] == 1234 and "missing" not in out
    assert all(c["method"] == "GET" for c in n.calls)
    assert n.calls[0]["params"] == {"api_method": "count_in_range", "latitude": "52.5200", "longitude": "13.4050", "radius": 10}
    t = out["would_send"]["body"]["task"]
    assert t["customer_ref"] == "order-7f3a" and t["input"]["items"] == [{"code": "address", "content": "1 Main St"}]
    assert out["would_send"]["headers"]["Authorization"] == "***"
    no_secret(out)
    missing = a.commission({})
    assert set(missing["missing"]) == {"template_code", "inputs"}
    with pytest.raises(ValueError):
        a.commission({"template_code": "photo_v1"}, confirm=True)


def test_clickworker_order_status_mapping_and_result(net, monkeypatch):
    monkeypatch.setenv("CLICKWORKER_CREDENTIALS", f"me@example.com:{SECRET}")
    n = net({("POST", "/customer/tasks"): fx("lxt-clickworker", "task_created.json"),
             ("GET", "/customer/tasks/12345"): fx("lxt-clickworker", "task_finished.json")})
    a = LXTClickworker()
    order = a.commission({"template_code": "photo_v1", "inputs": {"address": "1 Main St"}}, confirm=True)
    sent = n.calls[0]
    assert sent["headers"]["Authorization"] == "Basic " + base64.b64encode(f"me@example.com:{SECRET}".encode()).decode()
    st = a.status(order)
    assert a.provider_status(st) == "finished" and a.map_status("finished") == "delivered"
    d = a.deliverables(st)
    assert len(d) == 1 and d[0]["kind"] == "task-result" and d[0]["phenomenon_time"] is None and d[0]["task_id"] == 12345
    fb = fx("lxt-clickworker", "task_feedback.json")
    assert a.map_status(a.provider_status(fb)) == "in_flight" and a.deliverables(fb) == []
    for raw, want in (("created", "in_flight"), ("queued", "in_flight"), ("running", "in_flight"), ("timedout", "failed"),
                      ("cancelled", "failed"), ("expired", "failed")):
        assert a.map_status(raw) == want
    no_secret([order, st, d])


# --------------------------------------------------------------------------------------------------------- hire-a-human

HAH = "https://cwktwvmcxstpscjcmqzi.supabase.co/functions/v1/api-tasks"


def test_hire_a_human_prepare_and_dry_run(no_net, monkeypatch):
    monkeypatch.setenv("HIREAHUMAN_API_KEY", SECRET)
    a = HireAHuman()
    o = a.prepare({"need": "photo of the storefront at 123 Main St, Springfield, IL", "place": "Springfield, IL"}, "commission")
    assert o["operation"] == "order" and o["method"] == "POST" and o["url"] == HAH and not o["safe_to_run_without_approval"]
    assert o["headers"]["x-api-key"] == "{HIREAHUMAN_API_KEY}" and o["body"]["target_type"] == "human"
    assert o["body"]["offered_rate"] == "{HIREAHUMAN_OFFERED_RATE}" and o["placeholders"][0]["name"] == "HIREAHUMAN_OFFERED_RATE"
    assert a.prepare(REQ, "commission", offered_rate=25)["body"]["offered_rate"] == 25
    r = a.prepare(REQ, "commission", operation="read")
    assert r["method"] == "GET" and r["url"] == HAH and r["safe_to_run_without_approval"]
    assert a.prepare(REQ, "commission", operation="status") is None   # no status endpoint is published
    assert a.prepare(REQ, "live") is None
    dry = a.commission({"need": "photo of the storefront"})
    assert dry["dry_run"] and dry["missing"] == ["offered_rate"] and dry["would_send"]["headers"]["x-api-key"] == "***"
    assert dry["would_send"]["body"] == {"title": "photo of the storefront", "description": "photo of the storefront",
                                         "target_type": "human"}
    with pytest.raises(ValueError):
        a.commission({"need": "x"}, confirm=True)
    no_secret([o, r, dry])
    assert not hasattr(HireAHuman, "status") or HireAHuman.status is irlpls.adapters.Adapter.status


def test_hire_a_human_order(net, monkeypatch):
    monkeypatch.setenv("HIREAHUMAN_API_KEY", SECRET)
    n = net({("POST", "/api-tasks"): fx("hire-a-human", "task_created.json")})
    out = HireAHuman().commission({"title": "Photograph storefront", "description": "10 photos of the exterior",
                                   "offered_rate": 25, "deadline": "2026-11-01"}, confirm=True)
    assert out["id"] == "task-1" and n.calls[0]["url"] == HAH and n.calls[0]["headers"]["x-api-key"] == SECRET
    assert n.calls[0]["body"]["offered_rate"] == 25 and n.calls[0]["body"]["deadline"] == "2026-11-01"
    s = HireAHuman().spec_from_request({"need": "photo of the gate"}, "ext-1", "2026-11-02T00:00:00Z")
    assert s == {"title": "photo of the gate", "description": "photo of the gate", "deadline": "2026-11-02"}


# ------------------------------------------------------------------------------------------------------- error mapping

@pytest.mark.parametrize("make,env,val,call", [
    (Toloka, "TOLOKA_API_KEY", SECRET, lambda a: a.status({"id": "run-42"})),
    (LXTClickworker, "CLICKWORKER_CREDENTIALS", f"me@example.com:{SECRET}", lambda a: a.status({"task": {"id": 1}})),
    (HireAHuman, "HIREAHUMAN_API_KEY", SECRET, lambda a: a.tasks()),
])
def test_errors_map_and_never_leak_the_key(make, env, val, call, monkeypatch):
    monkeypatch.setenv(env, val)
    for status, exc in ((401, MissingCredentials), (403, MissingCredentials), (404, LookupError)):
        def raise_(url, **kw):
            raise err(status)
        monkeypatch.setattr(http, "get", raise_)
        with pytest.raises(exc) as e:
            call(make())
        assert SECRET not in str(e.value) and "body" not in str(e.value)

    def limited(url, **kw):
        raise err(429, {"Retry-After": "30"})
    monkeypatch.setattr(http, "get", limited)
    with pytest.raises(http.HTTPError) as e:
        call(make())
    assert e.value.status == 429 and e.value.retry_after == 30.0


def test_missing_credentials_raise_before_any_call(no_net, monkeypatch):
    for env in ("TOLOKA_API_KEY", "CLICKWORKER_CREDENTIALS", "HIREAHUMAN_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(MissingCredentials):
        Toloka().status({"id": "r"})
    with pytest.raises(MissingCredentials):
        LXTClickworker().workers_near(52.5, 13.4)
    with pytest.raises(MissingCredentials):
        HireAHuman().tasks()


def test_broker_plan_carries_the_ready_call_offline(no_net, monkeypatch):
    monkeypatch.setenv("TOLOKA_API_KEY", SECRET)
    job = irlpls.request("500 first-person clips of a hand opening a push-bar door, within 7 days", geocode=False,
                         quantity=500)
    rows = {p["card_id"]: p for p in job.plan}
    if "toloka" in rows:
        pc = rows["toloka"].get("prepared_call")
        assert pc and pc["operation"] == "quote" and "{TOLOKA_PIPELINE_ID}" in pc["url"]
    no_secret([job.plan, job.ruled_out])
