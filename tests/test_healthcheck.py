"""Daily read check (tools/healthcheck.py, irlpls/health.py): fake adapters, no network."""
import importlib.util
import json
import pathlib
import socket

import pytest

import irlpls
from irlpls import catalog, health, http
from irlpls.adapters.base import Adapter, Results, warning

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("healthcheck", ROOT / "tools" / "healthcheck.py")
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


class Fake(Adapter):
    can_live = True

    def __init__(self, card_id, fn, env=None):
        self.card_id, self.fn, self.env = card_id, fn, env
        self.calls = []

    def live(self, lat, lon, radius_km=10, limit=5, **kw):
        self.calls.append((lat, lon, limit))
        return self.fn()


class OrderOnly(Adapter):
    card_id = "order-only"
    can_commission = True

    def commission(self, spec, confirm=False):
        raise AssertionError("the health check must never order")


def boom(exc):
    def f():
        raise exc
    return f


@pytest.fixture(autouse=True)
def points(monkeypatch):
    monkeypatch.setattr(hc, "POINTS", {k: (1.0, 2.0) for k in ("good", "empty", "e404", "warn404", "slow", "keyed", "partial")})
    monkeypatch.setattr(hc, "EXTRA", {})
    monkeypatch.setattr(hc, "NEVER", {"paid": "costs money"})


def run(adapters, **kw):
    return hc.check(adapters, **{"timeout_s": 1, "budget_s": 2, **kw})


def test_classifies_ok_failed_and_skipped(monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    ads = {
        "good": Fake("good", lambda: Results([{"v": 1}])),
        "empty": Fake("empty", lambda: Results([])),
        "e404": Fake("e404", boom(http.HTTPError(404, "https://x", "gone"))),
        "warn404": Fake("warn404", lambda: Results([], [warning("warn404", "not_found", "HTTP 404 on read", status=404)])),
        "partial": Fake("partial", lambda: Results([{"v": 1}], [warning("partial", "provider_error", "stats down", status=500)])),
        "slow": Fake("slow", boom(socket.timeout("timed out"))),
        "keyed": Fake("keyed", lambda: Results([{"v": 1}]), env="FAKE_KEY"),
        "nopoint": Fake("nopoint", lambda: Results([{"v": 1}])),
        "paid": Fake("paid", boom(AssertionError("paid read must never run"))),
        "order-only": OrderOnly(),
    }
    d = run(ads)
    p = d["providers"]
    assert p["good"]["result"] == "ok" and p["good"]["items"] == 1 and p["good"]["status"] is None
    assert p["empty"]["result"] == "ok" and "empty result" in p["empty"]["reason"]
    assert (p["e404"]["result"], p["e404"]["status"], p["e404"]["error"]) == ("failed", 404, "HTTPError")
    assert (p["warn404"]["result"], p["warn404"]["status"]) == ("failed", 404)
    assert p["partial"]["result"] == "ok" and "partial" in p["partial"]["reason"]
    assert (p["slow"]["result"], p["slow"]["error"]) == ("failed", "Timeout")
    assert p["nopoint"]["result"] == "skipped"
    assert "keyed" not in p and "paid" not in p and "order-only" not in p
    assert ads["good"].calls == [(1.0, 2.0, 1)]   # one minimal read
    for r in p.values():
        assert {"result", "ok", "status", "error", "latency_ms", "checked_at", "reason", "history"} <= set(r)
    assert d["summary"] == {"checked": 6, "ok": 3, "failed": 3, "skipped": 1}


def test_keyed_read_runs_only_with_key(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")
    a = Fake("keyed", lambda: Results([{"v": 1}]), env="FAKE_KEY")
    assert run({"keyed": a})["providers"]["keyed"]["result"] == "ok" and a.calls


def test_extra_keyless_listing(monkeypatch):
    class Board(Adapter):
        card_id = "board"

        def board(self, limit=20):
            return [{"id": 1}]
    monkeypatch.setattr(hc, "EXTRA", {"board": ("board(limit=1)", lambda a: a.board(limit=1))})
    r = run({"board": Board()})["providers"]["board"]
    assert r["result"] == "ok" and r["read"] == "board(limit=1)"


def test_requests_are_capped_short(monkeypatch):
    seen = []
    monkeypatch.setattr(http.urllib.request.OpenerDirector, "open", lambda self, req, timeout=None: seen.append(timeout) or (_ for _ in ()).throw(socket.timeout("t")))
    monkeypatch.setattr(http, "check_url", lambda *a, **k: None)
    a = Fake("good", lambda: http.get("https://example.com/x", timeout=30))
    r = run({"good": a}, timeout_s=3)["providers"]["good"]
    assert seen == [3] and r["error"] == "Timeout"


def test_history_streak_and_board_dedupe(tmp_path):
    hist = []
    for day in ("2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"):
        hist = health.add_history(hist, day, day == "2026-10-25")
    hist = health.add_history(hist, "2026-10-28", False)   # same day again replaces
    assert len(hist) == 4 and health.failed_streak(hist) == 3
    assert health.failed_streak([{"date": "2026-10-26", "ok": False}, {"date": "2026-10-28", "ok": False}]) == 1  # a gap breaks it
    board = tmp_path / "BOARD.json"
    board.write_text(json.dumps({"updated": "x", "updates": [{"id": "u-old", "kind": "done", "text": "t"}]}, indent=1))
    data = {"providers": {
        "bad": {"result": "failed", "ok": False, "status": 404, "reason": "gone", "history": hist},
        "once": {"result": "failed", "ok": False, "status": 500, "reason": "x", "history": [{"date": "2026-10-28", "ok": False}]},
        "fine": {"result": "ok", "ok": True, "history": [{"date": "2026-10-28", "ok": True}]}}}
    assert hc.board_alerts(data, {"bad": "Bad Feed"}, board_path=board) == ["u-health-bad-2026-10-26"]
    assert hc.board_alerts(data, board_path=board) == []   # dedupe
    b = json.loads(board.read_text())
    assert [u["id"] for u in b["updates"]] == ["u-health-bad-2026-10-26", "u-old"]
    assert b["updates"][0]["kind"] == "info" and "Bad Feed" in b["updates"][0]["text"] and "404" in b["updates"][0]["text"]


def test_history_carries_over_runs():
    a = Fake("e404", boom(http.HTTPError(404, "https://x", "")))
    prev = {"providers": {"e404": {"history": [{"date": "2000-01-01", "ok": True}]}, "other": {"result": "ok", "history": []}}}
    d = run({"e404": a}, previous=prev, only={"e404"})
    assert [h["ok"] for h in d["providers"]["e404"]["history"]] == [True, False]
    assert "other" in d["providers"]   # a partial run keeps the rest


def _write(tmp_path, monkeypatch, providers):
    f = tmp_path / "health.json"
    f.write_text(json.dumps({"irlpls_health": 1, "providers": providers}))
    monkeypatch.setenv("IRLPLS_HEALTH_FILE", str(f))
    return f


def test_card_line_and_catalog(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "nws": {"result": "ok", "ok": True, "checked_at": "2026-10-28T12:00:00Z", "reason": "answered"},
        "usgs-water": {"result": "failed", "ok": False, "status": 404, "checked_at": "2026-10-28T12:00:00Z", "reason": "gone"},
        "noaa-tides": {"result": "failed", "ok": False, "status": None, "error": "Timeout", "checked_at": "2026-10-28T12:00:00Z"},
        "skyfi": {"result": "skipped", "ok": None}})
    assert health.line("nws") == "Read check: worked 28 Oct"
    assert health.line("usgs-water") == "Read check: failed 28 Oct (404)"
    assert health.line("noaa-tides") == "Read check: failed 28 Oct (Timeout)"
    assert health.line("skyfi") is None and health.line("nope") is None
    cat, _ = catalog.build(irlpls.registry().cards)
    assert cat["providers"]["nws"]["health"]["ok"] is True
    assert cat["providers"]["usgs-water"]["health"]["status"] == 404
    assert cat["providers"]["skyfi"]["health"] is None
    assert cat["stats"]["read_check"] == {"ok": 1, "failed": 2}


def test_card_page_shows_read_check(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"usgs-water": {"result": "failed", "ok": False, "status": 404, "checked_at": "2026-10-28T12:00:00Z"}})
    spec = importlib.util.spec_from_file_location("build_mod", ROOT / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    card = json.loads((ROOT / "cards" / "usgs-water.json").read_text())
    page = b.card_page(card)
    assert "Read check: failed 28 Oct (404)" in page
    assert page.index("Read check") > page.index('<h2 class="sec">API</h2>')
    assert "Read check" not in b.card_page(json.loads((ROOT / "cards" / "nws.json").read_text()))


def test_broker_warns_when_pick_failed(tmp_path, monkeypatch):
    def plan():
        return irlpls.request("river level now", geocode=False, lat=29.9677, lon=-98.905, country_code="us", state="TX")
    _write(tmp_path, monkeypatch, {})
    best = plan().best()
    assert best and best["card_id"]
    j = plan()
    assert not any(w.get("code") == "read_check_failed" for w in j.warnings)
    _write(tmp_path, monkeypatch, {best["card_id"]: {"result": "failed", "ok": False, "status": 503,
                                                     "checked_at": "2026-10-28T12:00:00Z", "reason": "down"}})
    j = plan()
    b = j.best()
    assert b["card_id"] == best["card_id"]   # informational: never reranks
    assert b["read_check"]["ok"] is False and "503" in b["warning"]
    w = [w for w in j.warnings if w["code"] == "read_check_failed"]
    assert len(w) == 1 and w[0]["card_id"] == best["card_id"]
    j.choose(best["card_id"])
    assert len([w for w in j.warnings if w["code"] == "read_check_failed"]) == 1   # deduped
    others = [o for o in j.plan if o["card_id"] != best["card_id"]]
    assert all("warning" not in o for o in others)


def test_every_keyless_live_adapter_has_a_test_point():
    from irlpls import adapters
    _real = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_real)
    missing = [cid for cid, a in adapters.ADAPTERS.items()
               if a.can_live and not a.env and cid not in _real.POINTS and cid not in _real.NEVER]
    assert not missing, f"add a test point to tools/healthcheck.py POINTS for {missing}"


def test_one_freshness_line_merges_both_checks(tmp_path, monkeypatch):
    """The daily read check and the landing's 30-minute check make one line: the newest wins, 'failed' only when it failed."""
    daily = {"providers": {"nws": {"result": "failed", "ok": False, "status": 503, "checked_at": "2026-10-28T12:00:00Z"},
                           "usgs-water": {"result": "ok", "ok": True, "checked_at": "2026-10-28T12:00:00Z"}}}
    live = {"checked": "2026-10-29T12:00:00Z", "results": {"nws": {"ok": True}, "usgs-water": {"ok": False, "error": "Timeout"}}}
    assert health.line("nws", daily, live) == "Read check: worked 29 Oct"
    assert health.line("usgs-water", daily, live) == "Read check: failed 29 Oct (Timeout)"
    old = dict(live, checked="2026-10-27T12:00:00Z")
    assert health.line("nws", daily, old) == "Read check: failed 28 Oct (503)"
    assert health.line("usgs-water", daily, old) == "Read check: worked 28 Oct"
    assert health.line("noaa-tides", {}, live) is None and health.latest("noaa-tides", {}, {}) is None
    tie = {"checked": "2026-10-28T12:00:00Z", "results": {"nws": {"ok": True}}}
    assert health.latest("nws", daily, tie)["ok"] is True
    _write(tmp_path, monkeypatch, daily["providers"])
    spec = importlib.util.spec_from_file_location("build_mod2", ROOT / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.LIVE.update(live)
    page = b.card_page(json.loads((ROOT / "cards" / "nws.json").read_text()))
    assert page.count("Read check") == 1 and "Read check: worked 29 Oct" in page
    assert "Adapter answered" not in page and "Adapter failed" not in page
