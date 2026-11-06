"""Shape drift must not read as healthy: a normally non-empty source that answers empty or key-less is 'degraded'."""
import importlib.util
import pathlib

import pytest

from irlpls import health
from irlpls.adapters.base import Adapter, Results, observation

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("healthcheck", ROOT / "tools" / "healthcheck.py")
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


class Fake(Adapter):
    can_live = True
    env = None

    def __init__(self, card_id, out):
        self.card_id, self.out = card_id, out

    def live(self, lat, lon, radius_km=10, limit=5, **kw):
        return self.out


GOOD = observation("feed", "reading", "2026-11-06T00:00:00Z", 1.0, 2.0, value=3, unit="m")


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(hc, "POINTS", {k: (1.0, 2.0) for k in ("feed", "quiet")})
    monkeypatch.setattr(hc, "EXTRA", {})
    monkeypatch.setattr(hc, "NEVER", {})
    monkeypatch.setattr(hc, "EXPECT", {"feed": 1})   # "quiet" is not expected to have data


def one(cid, out):
    return hc.check({cid: Fake(cid, out)}, timeout_s=1, budget_s=2)["providers"][cid]


def test_required_fields_come_from_the_adapters_own_parse():
    assert set(hc._required_fields()) == {"provider", "kind", "phenomenon_time", "retrieved_at"}
    assert all(k in GOOD for k in hc._required_fields())


def test_data_is_ok():
    r = one("feed", Results([GOOD]))
    assert (r["result"], r["ok"], r["shape"]) == ("ok", True, "data")


def test_empty_where_data_is_usual_is_degraded_not_ok():
    r = one("feed", Results([]))
    assert (r["result"], r["ok"], r["shape"]) == ("degraded", False, "empty")
    assert r["status"] is None and r["error"] is None
    assert "down" not in r["reason"].lower() and "cannot tell" in r["reason"]


def test_keyless_payload_is_degraded():
    r = one("feed", Results([{"v": 1}, {}]))   # items came back, but not through the parse's fields
    assert (r["result"], r["ok"], r["shape"]) == ("degraded", False, "unexpected")
    assert "shape" in r["reason"]


def test_partly_keyless_still_ok():
    assert one("feed", Results([{"v": 1}, GOOD]))["result"] == "ok"


def test_quiet_sources_stay_ok_when_empty():
    r = one("quiet", Results([]))
    assert (r["result"], r["ok"], r["shape"]) == ("ok", True, "empty")


def test_failures_stay_failed():
    from irlpls import http

    def boom():
        raise http.HTTPError(404, "https://x", "gone")
    class B(Fake):
        def live(self, *a, **k):
            boom()
    r = hc.check({"feed": B("feed", None)}, timeout_s=1, budget_s=2)["providers"]["feed"]
    assert r["result"] == "failed" and r["status"] == 404


def test_summary_counts_degraded_and_history_records_it():
    d = hc.check({"feed": Fake("feed", Results([]))}, timeout_s=1, budget_s=2)
    assert d["summary"]["degraded"] == 1 and d["summary"]["checked"] == 1 and d["summary"]["ok"] == 0
    assert d["providers"]["feed"]["history"][-1]["ok"] is False


def test_wording_is_honest():
    d = hc.check({"feed": Fake("feed", Results([]))}, timeout_s=1, budget_s=2)
    assert health.last("feed", d)["result"] == "degraded"
    t = health.line("feed", d)
    assert "answered but not as expected" in t and "failed" not in t and "down" not in t.lower()
    s = health.summary("feed", d)
    assert s["ok"] is False and s["result"] == "degraded"
    assert "answered, but not as expected" in health.why(d["providers"]["feed"])


def test_board_alert_after_two_days_says_not_as_expected(tmp_path):
    import json
    hist = [{"date": "2026-11-05", "ok": False}, {"date": "2026-11-06", "ok": False}]
    data = {"providers": {"feed": {"result": "degraded", "ok": False, "reason": "empty", "history": hist}}}
    b = tmp_path / "BOARD.json"
    b.write_text(json.dumps({"updates": []}))
    assert hc.board_alerts(data, board_path=b) == ["u-health-feed-2026-11-05"]
    txt = json.loads(b.read_text())["updates"][0]["text"]
    assert "not as expected" in txt and "failed" not in txt


def test_broker_warning_does_not_claim_failure_for_degraded():
    from irlpls import broker
    j = broker.Job.__new__(broker.Job)
    j.warnings, j.notes = [], []
    j._note = lambda m: None
    opt = {"name": "Feed", "card_id": "feed", "read_check": {"ok": False, "result": "degraded", "checked_at": "2026-11-06T01:00:00Z"}}
    j._read_check_warning(opt)
    assert "not as expected" in opt["warning"] and "failed" not in opt["warning"]


def test_real_expectations_name_real_adapters():
    from irlpls import adapters
    fresh = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(fresh)
    assert set(fresh.EXPECT) <= set(adapters.ADAPTERS)
    assert set(fresh.EXPECT) <= set(fresh.POINTS)
    assert all(adapters.ADAPTERS[c].can_live for c in fresh.EXPECT)
