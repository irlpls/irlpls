"""Security review 2026-11 (MEDIUM): operator ceiling, confirm gate, idempotency, state files, wallet flag. No network."""
import asyncio
import json
import os
import stat
import threading
import time
from decimal import Decimal

import pytest

import irlpls
from irlpls import broker, mcp_server
from irlpls.adapters import _a7
from irlpls.adapters.beemaps import BeeMaps


class Fake(BeeMaps):
    def __init__(self, total=30):
        self.confirmed, self.total = [], total

    def configured(self):
        return True

    def commission(self, spec, confirm=False):
        if confirm:
            self.confirmed.append(spec)
            return {"id": f"req-{len(self.confirmed)}", "quote": {"total": self.total}}
        return {"dry_run": True, "quote": {"total": self.total}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(broker, "HOME", tmp_path / "home")
    monkeypatch.setattr(broker, "_last_prune", [0.0])
    a = Fake()
    monkeypatch.setitem(irlpls.adapters.ADAPTERS, "hivemapper", a)
    return a


def operator(monkeypatch, allow=True, ceiling=None):
    monkeypatch.setattr(broker, "OPERATOR_ALLOW_SUBMIT", allow)
    monkeypatch.setattr(broker, "OPERATOR_MAX_ORDER_USD", None if ceiling is None else Decimal(str(ceiling)))


def job(policy=None, strict=False):
    j = irlpls.request("street video within 3 days", geocode=False, lat=30.27, lon=-97.74, country_code="us", policy=policy).choose("hivemapper")
    j.operator_strict = strict
    return j


# -- (1) operator ceiling ---------------------------------------------------------------------------------------------
def test_ceiling_is_a_cap_policy_cannot_loosen(fake, monkeypatch):
    operator(monkeypatch, ceiling=20)
    j = job({"max_order_usd": 500}).submit(confirm=True, idempotency_key="k1")   # quote $30, operator ceiling $20
    assert j.state == "quoted" and not fake.confirmed and j.quote["blocked_by_policy"]
    j = job(None).submit(confirm=True, idempotency_key="k2")                      # no policy at all: ceiling still binds
    assert j.state == "quoted" and not fake.confirmed


def test_ceiling_applies_to_auto_submit_and_policy_can_be_stricter(fake, monkeypatch):
    operator(monkeypatch, ceiling=100)
    j = job({"auto_submit": True, "max_order_usd": 10}).submit()
    assert j.state == "quoted" and not fake.confirmed                              # policy stricter than operator: honoured
    operator(monkeypatch, ceiling=20)
    j = job({"auto_submit": True, "max_order_usd": 10_000}).submit()
    assert j.state == "quoted" and not fake.confirmed                              # auto_submit cannot lift the operator ceiling
    operator(monkeypatch, ceiling=100)
    assert job({"auto_submit": True, "max_order_usd": 50}).submit().state == "submitted" and len(fake.confirmed) == 1


def test_ceiling_zero_means_no_orders(fake, monkeypatch):
    operator(monkeypatch, ceiling=0)
    j = job({"max_order_usd": 500, "auto_submit": True})
    j2 = j.submit()
    assert j2.state == "quoted" and not fake.confirmed
    with pytest.raises(broker.JobError) as e:
        job({"max_order_usd": 500}).submit(confirm=True, idempotency_key="z")
    assert e.value.code == "submit_disabled" and not fake.confirmed


def test_bad_env_ceiling_is_zero():
    for raw in ("abc", "-5", "nan", "inf"):
        assert broker._env_ceiling(raw) == 0
    assert broker._env_ceiling(None) is None and broker._env_ceiling("12.5") == Decimal("12.5")


def test_mcp_mode_defaults_to_no_orders(fake, monkeypatch):
    operator(monkeypatch, allow=False)
    with pytest.raises(broker.JobError) as e:
        job({"max_order_usd": 500}, strict=True).submit(confirm=True, idempotency_key="m1")
    assert e.value.code == "submit_disabled" and "IRLPLS_ALLOW_SUBMIT" in e.value.message and not fake.confirmed
    j = job({"max_order_usd": 500, "auto_submit": True}, strict=True).submit()    # a standing mandate is not enough either
    assert j.state == "quoted" and not fake.confirmed
    operator(monkeypatch, allow=True)                                              # allowed but no ceiling: strict default is 0
    assert job({"max_order_usd": 500}, strict=True).operator_strict
    with pytest.raises(broker.JobError):
        job({"max_order_usd": 500}, strict=True).submit(confirm=True, idempotency_key="m2")
    operator(monkeypatch, allow=True, ceiling=100)
    assert job({"max_order_usd": 500}, strict=True).submit(confirm=True, idempotency_key="m3").state == "submitted"


def call(srv, name, args):
    r = asyncio.run(srv.call_tool(name, args))
    return bool(r.is_error), json.loads(r.content[0].text)


def test_mcp_tools_refuse_and_say_who_approves(fake, monkeypatch):
    pytest.importorskip("mcp")
    operator(monkeypatch, allow=False)
    srv = mcp_server.build(read_only=False)
    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    for n in ("irlpls_submit", "irlpls_approve"):
        assert "person" in tools[n].description and "IRLPLS_ALLOW_SUBMIT" in tools[n].description
    j = job({"max_order_usd": 500})
    broker._JOBS[j.id] = j
    err, body = call(srv, "irlpls_submit", {"job_id": j.id, "confirm": True, "idempotency_key": "x1"})
    assert err and body["code"] == "submit_disabled" and not fake.confirmed
    err, body = call(srv, "irlpls_order", {"order_request": {"quote_id": "q"}, "meta": {"idempotency_key": "x2"}})
    assert err and body["code"] == "submit_disabled"
    err, body = call(srv, "irlpls_cancel", {"order_id": "nope", "meta": {"idempotency_key": "x3"}})
    assert err and body["code"] in ("not_found", "submit_disabled")
    err, body = call(srv, "irlpls_submit", {"job_id": j.id})                       # a quote is still fine
    assert not err and body["state"] == "quoted"
    operator(monkeypatch, allow=True, ceiling=100)
    err, body = call(srv, "irlpls_submit", {"job_id": j.id, "confirm": True, "idempotency_key": "x4"})
    assert not err and body["state"] == "submitted" and "person" in body["approval_notice"]


# -- (3) idempotency -----------------------------------------------------------------------------------------------------
def test_replay_same_key_different_spec_is_rejected(fake, monkeypatch):
    operator(monkeypatch)
    j = job({"max_order_usd": 500}).submit(spec={"title": "A"}, confirm=True, idempotency_key="r1")
    assert j.state == "submitted" and len(fake.confirmed) == 1
    j.submit(spec={"title": "A"}, confirm=True, idempotency_key="r1")              # same spec: a quiet replay
    assert len(fake.confirmed) == 1
    with pytest.raises(broker.JobError) as e:
        j.submit(spec={"title": "B"}, confirm=True, idempotency_key="r1")
    assert e.value.code == "idempotency_key_reused" and len(fake.confirmed) == 1


def test_concurrent_same_key_orders_once(fake, monkeypatch):
    operator(monkeypatch)
    orig = fake.commission
    def slow(spec, confirm=False):
        if confirm:
            time.sleep(0.15)
        return orig(spec, confirm)
    fake.commission = slow
    j = job({"max_order_usd": 500})
    out = []
    def go():
        try:
            out.append(j.submit(confirm=True, idempotency_key="race"))
        except Exception as e:  # pragma: no cover
            out.append(e)
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(fake.confirmed) == 1


def test_replay_across_a_reloaded_job_is_still_one_order(fake, monkeypatch):
    operator(monkeypatch)
    j = job({"max_order_usd": 500}).submit(confirm=True, idempotency_key="p1")
    other = broker.Job.from_dict(j.to_dict())
    other.state = "quoted"
    other.submit(confirm=True, idempotency_key="p1")
    assert len(fake.confirmed) == 1


# -- (4) the confirmed total is re-checked ---------------------------------------------------------------------------------
def test_price_rise_after_the_quote_is_refused(fake, monkeypatch):
    operator(monkeypatch)
    j = job({"max_order_usd": 500}).submit()
    assert j.state == "quoted"
    fake.total = 45                                                                # the provider's price moved up before confirm
    j.submit(confirm=True, idempotency_key="up")
    assert not fake.confirmed and j.state == "quoted" and "price rose" in json.dumps(j.quote["blocked_by_policy"])


def test_confirmed_total_over_ceiling_is_flagged(fake, monkeypatch):
    operator(monkeypatch, ceiling=100)
    orig = fake.commission
    fake.commission = lambda spec, confirm=False: ({"id": "r", "quote": {"total": 300}} if confirm else orig(spec, confirm))
    j = job({"max_order_usd": 50}).submit(confirm=True, idempotency_key="over")    # dry run said $30; the confirm says $300
    assert any(w["code"] == "over_ceiling" for w in j.warnings)


# -- (5) state files -----------------------------------------------------------------------------------------------------
def test_state_files_are_private_atomic_and_bounded(fake, monkeypatch):
    operator(monkeypatch)
    monkeypatch.setenv("BEEMAPS_API_KEY", "sekret-credential-123")
    j = job({"max_order_usd": 500}).submit(confirm=True, idempotency_key="s1")
    jobs = broker.HOME / "jobs"
    assert stat.S_IMODE(os.stat(jobs).st_mode) == 0o700 and stat.S_IMODE(os.stat(broker.HOME).st_mode) == 0o700
    files = list(jobs.iterdir())
    assert files and all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in files)
    assert not [f for f in files if f.name.endswith(".tmp")]
    assert all("sekret-credential-123" not in f.read_text() for f in files if f.suffix == ".json")
    big = j.to_dict()
    big["log"] = [{"t": "x", "msg": "y" * 2000}] * 2000
    assert len(broker._bounded(big)) <= broker.STATE_MAX_BYTES * 1.05


def test_old_and_excess_jobs_are_pruned(fake, monkeypatch):
    jobs = broker._private_dir(broker.HOME / "jobs")
    old = jobs / "aaaaaaaaaaaa.json"
    old.write_text("{}")
    t = time.time() - 40 * 86400
    os.utime(old, (t, t))
    for i in range(5):
        (jobs / f"{i:012x}.json").write_text("{}")
    monkeypatch.setattr(broker, "JOB_MAX_COUNT", 3)
    broker._prune_jobs(keep="000000000000")
    left = sorted(p.name for p in jobs.iterdir())
    assert "aaaaaaaaaaaa.json" not in left and len(left) <= 4 and "000000000000.json" in left


# -- (6) wallet signing ----------------------------------------------------------------------------------------------------
def test_wallet_signing_is_off_and_only_the_operator_env_turns_it_on(monkeypatch):
    import pathlib
    assert _a7.WALLET_SIGNING is False
    monkeypatch.setattr(_a7, "WALLET_SIGNING", False)
    monkeypatch.delenv("IRLPLS_ENABLE_WALLET_SIGNING", raising=False)
    monkeypatch.setattr(_a7, "WALLET_SIGNING", True)
    assert mcp_server.pin_wallet_signing() is False and _a7.WALLET_SIGNING is False   # process start resets it
    monkeypatch.setenv("IRLPLS_ENABLE_WALLET_SIGNING", "1")
    assert mcp_server.pin_wallet_signing() is True
    monkeypatch.setattr(_a7, "WALLET_SIGNING", False)
    # no tool, policy or job field names it, and nothing in the package assigns it besides _a7 and the pin
    src = pathlib.Path(irlpls.__file__).parent
    for f in src.rglob("*.py"):
        if f.name == "_a7.py" or f.name == "mcp_server.py":
            continue
        assert "WALLET_SIGNING =" not in f.read_text() and "WALLET_SIGNING=" not in f.read_text(), f
    assert "wallet" not in json.dumps(irlpls.Policy().to_dict()).lower()


def test_fingerprint_ignores_the_moving_deadline():
    a = broker._fingerprint("c", {"constraints": {"deliver_by": "2026-11-07T00:00:00Z", "max_total": "5"}, "n": 1})
    b = broker._fingerprint("c", {"constraints": {"deliver_by": "2026-11-07T00:00:01Z", "max_total": "5"}, "n": 1})
    c = broker._fingerprint("c", {"constraints": {"deliver_by": "2026-11-07T00:00:01Z", "max_total": "6"}, "n": 1})
    assert a == b and a != c


def test_reloaded_job_keeps_its_fixed_deadline():
    from irlpls.need import Request
    r = Request.from_dict({"need": "photo of a shop", "within_minutes": 60})
    j = broker.Job("photo of a shop", r, None, None)
    first = broker._req_dict(j.request)["deliver_by"]
    other = broker.Job.from_dict(json.loads(json.dumps(j.to_dict())))
    assert broker._req_dict(other.request)["deliver_by"] == first
