"""Contributor round 1: plain build/gate failures, validate warnings, the starter card, the localhost rehearsal."""
import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls import validate  # noqa: E402

TEMPLATE = ROOT / "examples" / "card-template.json"


def card():
    return json.loads(TEMPLATE.read_text())


def msgs(c, sev=None, **kw):
    return [(w, m) for s, w, m in validate.problems(c, **kw) if sev in (None, s)]


def test_template_is_clean_and_passes_gate():
    assert not msgs(card(), "error") and not msgs(card(), "warn")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_cards.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert (ROOT / "examples" / "card-template.md").exists()
    assert "Example Weather Co" in TEMPLATE.read_text()


def test_unknown_key_typo_warns_with_suggestion():
    c = card()
    c["licencing"] = {}
    w = [m for _, m in msgs(c, "warn")]
    assert any("did you mean 'licensing'" in m for m in w)
    assert not msgs(c, "error")


def test_bad_date_empty_unknown_and_local_provider_maintained_warn():
    c = card()
    c["updated"] = "06/11/2026"
    c["verification"]["human"]["unknown"] = ""
    c["status"] = "provider-maintained"
    ws = msgs(c, "warn")
    assert any(w == "updated" and "YYYY-MM-DD" in m for w, m in ws)
    assert any(w == "verification.human.unknown" for w, m in ws)
    assert any(w == "status" and "own domain" in m for w, m in ws)
    # the same card served from its own domain does not warn about status
    assert not any(w == "status" for w, m in msgs(c, "warn", fetched_from="https://example.com/.well-known/irlpls"))


@pytest.mark.parametrize("key", ["short", "access", "api"])
def test_hidden_requirements_are_reported(key):
    c = card()
    del c[key]
    assert any(w == key and "needed to be listed" in m for w, m in msgs(c, "warn"))


def test_errors_are_short_and_never_echo_regex_or_huge_values():
    c = card()
    c["id"] = "Bad_Id"
    c["provider_url"] = "http://x.example/" + "a" * 5000
    c["summary"] = "s" * 9000
    del c["pricing"]
    errs = msgs(c, "error")
    assert errs
    for w, m in errs:
        assert len(m) < 260 and "^" not in m and "\\" not in m, (w, m[:120])
    assert any(w == "pricing" and "from_usd" in m for w, m in errs)
    assert any(w == "id" and "lowercase" in m for w, m in errs)
    assert len({(w, m) for w, m in errs}) == len(errs)


def _tree(tmp_path, ids):
    d = tmp_path / "cards"
    d.mkdir()
    for name, cid in ids:
        c = card()
        c["id"] = cid
        (d / f"{name}.json").write_text(json.dumps(c))
    return d


def test_repo_problems_family_duplicate_and_filename(tmp_path):
    d = _tree(tmp_path, [("example-weather-co", "example-weather-co"), ("other", "example-weather-co")])
    out = "\n".join(validate.repo_problems(d))
    assert "example-weather-co: not in any family" in out and 'FAMILIES in irlpls/catalog.py' in out
    assert "other: id 'example-weather-co' must equal the file name" in out
    assert "duplicate id" in out


def test_current_catalog_has_no_repo_problems():
    assert validate.repo_problems(ROOT / "cards") == []


def test_build_fails_plainly_without_traceback(tmp_path):
    shutil.copy(ROOT / "cards" / "airnow.json", tmp_path / "x.json")
    c = json.loads((ROOT / "cards" / "airnow.json").read_text())
    c["id"] = "example-weather-co"
    f = ROOT / "cards" / "example-weather-co.json"
    f.write_text(json.dumps(c))
    try:
        r = subprocess.run([sys.executable, str(ROOT / "build.py")], capture_output=True, text=True, cwd=ROOT,
                           env={**__import__("os").environ, "IRLPLS_NO_FETCH": "1"})
    finally:
        f.unlink()
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "example-weather-co: not in any family" in r.stderr and "FAMILIES in irlpls/catalog.py" in r.stderr


def test_check_cards_says_the_same(tmp_path):
    f = ROOT / "cards" / "example-weather-co.json"
    c = json.loads((ROOT / "cards" / "airnow.json").read_text())
    c["id"] = "example-weather-co"
    f.write_text(json.dumps(c))
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_cards.py")], capture_output=True, text=True)
    finally:
        f.unlink()
    assert r.returncode == 1 and "example-weather-co: not in any family" in r.stdout and "FAMILIES in irlpls/catalog.py" in r.stdout


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/.well-known/irlpls"
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(TEMPLATE.read_bytes() if ok else b"{}")

    def log_message(self, *a):
        pass


def test_localhost_rehearsal_only_with_the_env_var(monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/.well-known/irlpls"
    try:
        monkeypatch.delenv("IRLPLS_ALLOW_PRIVATE_HOSTS", raising=False)
        with pytest.raises(ValueError, match="HTTPS only"):
            validate.load(url)
        monkeypatch.setenv("IRLPLS_ALLOW_PRIVATE_HOSTS", "1")
        c, u = validate.load(url)
        assert c["id"] == "example-weather-co" and u == url
    finally:
        srv.shutdown()


def test_docs_exist_and_are_neutral():
    add = (ROOT / "docs" / "ADD-A-CARD.md").read_text()
    for needle in ("Claim, correct or remove your card", "hello@irlpls.com", "We read every message", "/.well-known/irlpls"):
        assert needle in add
    for f in ("docs/ADD-A-CARD.md", "docs/PROVIDERS.md"):
        t = (ROOT / f).read_text()
        assert "github.com" not in t.lower()
    assert "add-a-card.html" in (ROOT / "docs" / "PROVIDERS.md").read_text()
    assert "ADD-A-CARD" in (ROOT / "CONTRIBUTING.md").read_text()
    ops = ROOT / "docs" / "OPS.md"   # private: absent from the public repo
    if ops.exists():
        assert "claim steps" not in ops.read_text()
