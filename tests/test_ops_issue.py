"""Dedupe logic of the single autopilot issue, and the autopilot's pure parts (no network)."""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autopilot  # noqa: E402
import ops_issue as oi  # noqa: E402

F = [{"name": "site", "detail": "down"}]


def test_healthy_and_no_issue_does_nothing():
    assert oi.decide([], [])["action"] == "none"


def test_first_failure_creates():
    d = oi.decide(F, [])
    assert d["action"] == "create" and "site" in d["body"]


def test_same_failure_twice_does_not_renotify():
    body = oi.body_for(F)
    assert oi.decide(F, [{"number": 5, "title": oi.TITLE, "body": body}])["action"] == "none"


def test_changed_failure_set_updates_same_issue():
    open_ = [{"number": 5, "title": oi.TITLE, "body": oi.body_for(F)}]
    d = oi.decide(F + [{"name": "www", "detail": "x"}], open_)
    assert d["action"] == "update" and d["number"] == 5


def test_recovery_closes():
    d = oi.decide([], [{"number": 5, "title": oi.TITLE, "body": oi.body_for(F)}])
    assert d["action"] == "close" and d["number"] == 5


def test_duplicates_collapse_to_lowest():
    open_ = [{"number": 9, "title": "", "body": ""}, {"number": 5, "title": "", "body": oi.body_for(F)}]
    d = oi.decide(F, open_)
    assert d["number"] == 5 and d["dupes"] == [9]


def test_autopilot_exit_only_on_fail(monkeypatch):
    ok = lambda *a, **k: autopilot.R("x", "warn", "d")  # noqa: E731
    for n in ("check_site", "check_ci", "check_rebuild", "check_due"):
        monkeypatch.setattr(autopilot, n, ok)
    monkeypatch.setattr(autopilot, "check_www", lambda: autopilot.R("www", "ok", ""))
    monkeypatch.setattr(autopilot, "check_mcp", lambda: autopilot.R("mcp", "skip", ""))
    monkeypatch.setattr(autopilot, "check_pypi", lambda: autopilot.R("pypi", "skip", ""))
    assert autopilot.run(True, True)["needs_action"] is False
    monkeypatch.setattr(autopilot, "check_site", lambda t: autopilot.R("site", "fail", "down"))
    s = autopilot.run(True, True)
    assert s["needs_action"] and "NEEDS ACTION" in autopilot.to_markdown(s)


def test_site_staleness(monkeypatch):
    today = datetime.date(2026, 11, 10)
    body = b'{"generated": "2026-11-03"}'
    monkeypatch.setattr(autopilot, "_get", lambda *a, **k: (200, {}, body))
    assert autopilot.check_site(today)["status"] == "fail"
    assert autopilot.check_site(datetime.date(2026, 11, 4))["status"] == "ok"
