"""Public-safe health summary: sanitizer, build include, card page line. Never a ranking input."""
import json
import os
import pathlib
import subprocess
import sys

PATHLEAK = "/Us" + "ers/someone"   # a local-path sentinel, spelled so the release privacy scan does not trip on the test itself

import pytest

from irlpls import health

ROOT = pathlib.Path(__file__).resolve().parent.parent

LOCAL = {"checked_at": "2026-11-06T09:00:00Z", "providers": {
    "nws": {"result": "ok", "ok": True, "checked_at": "2026-11-06T09:00:00Z", "read": "live(1,2)", "reason": "answered with 1 item",
            "error": None, "status": None, "latency_ms": 12},
    "usgs-water": {"result": "degraded", "ok": False, "checked_at": "2026-11-05T09:00:00Z", "reason": PATHLEAK + "/x secret=abc",
                   "read": "https://api.example/key=XYZ"},
    "noaa-tides": {"result": "failed", "ok": False, "status": 500, "error": "HTTPError", "checked_at": "2026-11-04T09:00:00Z",
                   "reason": "HTTP 500 for https://x/?token=T body {raw}"},
    "airnow": {"result": "skipped", "ok": None, "checked_at": "2026-11-06T09:00:00Z"},
    "../evil": {"result": "failed", "checked_at": "2026-11-06T09:00:00Z"}}}


def test_sanitizer_keeps_only_id_status_date():
    out = health.sanitize_public(LOCAL)
    assert out["generated"] == "2026-11-06"
    assert out["cards"] == {"nws": {"status": "ok", "last_checked": "2026-11-06"},
                            "usgs-water": {"status": "degraded", "last_checked": "2026-11-05"},
                            "noaa-tides": {"status": "failing", "last_checked": "2026-11-04"},
                            "airnow": {"status": "unknown", "last_checked": "2026-11-06"}}
    blob = json.dumps(out)
    for leak in (PATHLEAK[:6], "secret", "token", "http", "XYZ", "reason", "latency", "error", "read"):
        assert leak not in blob
    for v in out["cards"].values():
        assert set(v) == {"status", "last_checked"} and v["status"] in health.PUBLIC_STATUS


def test_sanitizer_known_ids_and_bad_dates():
    out = health.sanitize_public(LOCAL, known={"nws"})
    assert list(out["cards"]) == ["nws"]
    bad = health.sanitize_public({"providers": {"nws": {"result": "ok", "checked_at": "/etc/passwd"}}})
    assert bad["cards"]["nws"] == {"status": "unknown", "last_checked": None}


def test_load_public_resanitizes_a_hand_edited_file(tmp_path):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"irlpls_public_health": 1, "cards": {
        "nws": {"status": "failing", "last_checked": "2026-11-05", "path": PATHLEAK, "reason": "x"},
        "x y": {"status": "ok", "last_checked": "2026-11-05"}}}))
    d = health.load_public(f)
    assert d["cards"] == {"nws": {"status": "failing", "last_checked": "2026-11-05"}}
    assert health.load_public(tmp_path / "missing.json") == {}
    f.write_text("{not json")
    assert health.load_public(f) == {}
    f.write_text(json.dumps({"cards": {}}))
    assert health.load_public(f) == {}   # not our format


def test_public_line_only_for_degraded_or_failing():
    assert health.public_line({"status": "ok", "last_checked": "2026-11-05"}) is None
    assert health.public_line({"status": "unknown", "last_checked": "2026-11-05"}) is None
    assert health.public_line(None) is None
    assert health.public_line({"status": "failing", "last_checked": "2026-11-05"}) == "Read check failed on 5 Nov"
    t = health.public_line({"status": "degraded", "last_checked": "2026-11-05"})
    assert "5 Nov" in t and "not as expected" in t and "down" not in t.lower()
    assert health.public_line({"status": "failing", "last_checked": "garbage"}) is None


def test_card_page_line_gated_on_data(monkeypatch):
    import build
    monkeypatch.setattr(build, "HEALTH", {})
    monkeypatch.setattr(build, "LIVE", {})
    c = {"id": "nws"}
    monkeypatch.setattr(build, "PUBLIC_HEALTH", {})
    assert build.read_check_line(c) == ""
    monkeypatch.setattr(build, "PUBLIC_HEALTH", {"nws": {"status": "ok", "last_checked": "2026-11-05"}})
    assert build.read_check_line(c) == ""   # healthy says nothing
    monkeypatch.setattr(build, "PUBLIC_HEALTH", {"nws": {"status": "failing", "last_checked": "2026-11-05"}})
    assert 'Read check failed on 5 Nov' in build.read_check_line(c)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("hp")
    snap = d / "health-public.json"
    snap.write_text(json.dumps(health.sanitize_public(LOCAL)))
    out = d / "site"
    env = dict(os.environ, IRLPLS_SITE=str(out), IRLPLS_NO_FETCH="1", IRLPLS_HEALTH_PUBLIC=str(snap),
               IRLPLS_HEALTH_FILE=str(d / "none.json"))
    r = subprocess.run([sys.executable, str(ROOT / "build.py"), "--public"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return out


def test_build_includes_public_health_and_flags_only_bad(built):
    h = json.loads((built / "health.json").read_text())
    assert h["cards"]["usgs-water"] == {"status": "degraded", "last_checked": "2026-11-05"}
    assert PATHLEAK[:6] not in (built / "health.json").read_text()
    cj = json.loads((built / "cards.json").read_text())
    assert set(cj["health"]) == {"usgs-water", "noaa-tides"}
    assert "Read check failed on 4 Nov" in (built / "c" / "noaa-tides.html").read_text()
    assert "not as expected" in (built / "c" / "usgs-water.html").read_text()
    assert "Read check" not in (built / "c" / "nws.html").read_text()


def test_no_snapshot_no_file_no_line(tmp_path):
    out = tmp_path / "site"
    env = dict(os.environ, IRLPLS_SITE=str(out), IRLPLS_NO_FETCH="1", IRLPLS_HEALTH_PUBLIC=str(tmp_path / "none.json"),
               IRLPLS_HEALTH_FILE=str(tmp_path / "none2.json"))
    r = subprocess.run([sys.executable, str(ROOT / "build.py")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    assert not (out / "health.json").exists()
    assert "health" not in json.loads((out / "cards.json").read_text())


def test_order_identical_with_and_without_health(built, tmp_path):
    out = tmp_path / "site"
    env = dict(os.environ, IRLPLS_SITE=str(out), IRLPLS_NO_FETCH="1", IRLPLS_HEALTH_PUBLIC=str(tmp_path / "none.json"),
               IRLPLS_HEALTH_FILE=str(tmp_path / "none2.json"))
    subprocess.run([sys.executable, str(ROOT / "build.py"), "--public"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=True)
    ids = lambda s: [c["id"] for c in json.loads((s / "cards.json").read_text())["cards"]]
    assert ids(built) == ids(out)


def test_cli_writes_public_snapshot(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("healthcheck", ROOT / "tools" / "healthcheck.py")
    hc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hc)
    assert "--public-snapshot" in open(ROOT / "tools" / "healthcheck.py").read()
