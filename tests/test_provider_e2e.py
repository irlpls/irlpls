"""Provider story end to end, no external network.

A local HTTP server plays the provider's domain. The card is the file `irlpls card-file --out` writes. conformance
is HTTPS-only and builds https://<domain>/... itself, so its fetcher is the real conformance.fetch pointed at the
local socket (the provider domain is mapped to 127.0.0.1:port); `irlpls validate` fetches the local URL directly
with the documented IRLPLS_ALLOW_PRIVATE_HOSTS=1 opt-out, set only inside these tests.
"""
import json
import subprocess
import sys
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from irlpls import conformance

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()
DOMAIN = "skyfi.com"
GOOD_HEADERS = {"Content-Type": "application/json", "Cache-Control": "public, max-age=300", "ETag": '"e2e"'}


def cli(*args, env_extra=None):
    import os
    env = dict(os.environ, **(env_extra or {}))
    return subprocess.run([sys.executable, "-m", "irlpls.cli", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture(scope="module")
def card_path(tmp_path_factory):
    out = tmp_path_factory.mktemp("provider")
    r = cli("card-file", "skyfi", "--out", str(out))
    assert r.returncode == 0, r.stderr
    p = out / ".well-known" / "irlpls"
    assert p.exists()
    return p


@pytest.fixture
def base_card(card_path):
    """What the provider serves after settling the checklist: claim the domain and set fresh dates."""
    c = json.loads(card_path.read_text())
    c.update(status="provider-maintained", provider_url=f"https://{DOMAIN}",
             updated=TODAY.isoformat(), expires=(TODAY + timedelta(days=300)).isoformat())
    return c


@pytest.fixture
def server():
    state = {"body": b"{}", "headers": dict(GOOD_HEADERS), "status": 200, "paths": {}}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/.well-known/irlpls":
                self.send_response(404); self.end_headers(); return
            self.send_response(state["status"])
            for k, v in state["headers"].items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["port"] = srv.server_address[1]
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}/.well-known/irlpls"
    yield state
    srv.shutdown()


def serve(state, card, **kw):
    state["body"] = json.dumps(card).encode()
    state["headers"] = dict(kw.pop("headers", GOOD_HEADERS))
    state["status"] = kw.pop("status", 200)


def conform(state, against=None):
    def f(url):
        assert url.startswith(f"https://{DOMAIN}/")
        return conformance.fetch(url.replace(f"https://{DOMAIN}", f"http://127.0.0.1:{state['port']}"),
                                 allow_private=True, allow_http=True)
    return conformance.check(DOMAIN, fetcher=f, against=against)


def names(res, status):
    return {n for s, n, _ in res if s == status}


def validate_cli(state, *extra):
    return cli("validate", *extra, state["url"], env_extra={"IRLPLS_ALLOW_PRIVATE_HOSTS": "1"})


def audited():
    return json.loads((ROOT / "cards" / "skyfi.json").read_text())


def test_card_file_serves_and_passes(server, base_card):
    serve(server, base_card)
    res = conform(server, against=audited())
    assert not names(res, "fail"), res
    assert "provider_url" in names(res, "pass") and "overclaims" in names(res, "pass")
    f = ROOT / "tests" / "__e2e_card.json"
    f.write_text(json.dumps(base_card))
    try:
        v = cli("validate", "--against", "skyfi", str(f))
    finally:
        f.unlink()
    assert v.returncode == 0, v.stdout + v.stderr
    assert ": valid" in v.stdout
    # served from 127.0.0.1 instead of the claimed domain, validate refuses the provider-maintained claim
    v = validate_cli(server)
    assert v.returncode == 1 and "provider's own domain" in v.stdout, v.stdout


def test_raw_card_file_unedited_is_flagged_unclaimed(server, card_path):
    """The file as generated is not yet the provider's: it must not earn provider-maintained."""
    serve(server, json.loads(card_path.read_text()))
    res = conform(server)
    assert "provider_url" not in names(res, "fail")  # unclaimed stays unclaimed, not a failure


def test_redirect(server, base_card):
    serve(server, base_card, status=301, headers={**GOOD_HEADERS, "Location": "https://www.skyfi.com/x"})
    res = conform(server)
    assert "no redirects" in names(res, "fail")


def test_wrong_content_type(server, base_card):
    serve(server, base_card, headers={**GOOD_HEADERS, "Content-Type": "text/html"})
    assert "content type" in names(conform(server), "fail")


def test_missing_cache_control(server, base_card):
    h = {k: v for k, v in GOOD_HEADERS.items() if k != "Cache-Control"}
    serve(server, base_card, headers=h)
    assert "cache-control" in names(conform(server), "fail")


def test_short_cache_control(server, base_card):
    serve(server, base_card, headers={**GOOD_HEADERS, "Cache-Control": "public, max-age=10"})
    assert "cache-control" in names(conform(server), "fail")


def test_expired(server, base_card):
    base_card.update(updated=(TODAY - timedelta(days=400)).isoformat(), expires=(TODAY - timedelta(days=35)).isoformat())
    serve(server, base_card)
    assert "expires" in names(conform(server), "fail")
    v = validate_cli(server)
    assert "expire" in v.stdout.lower(), v.stdout


def test_wrong_domain(server, base_card):
    base_card["provider_url"] = "https://someone-else.example"
    serve(server, base_card)
    assert "provider_url" in names(conform(server), "fail")
    v = validate_cli(server)
    assert v.returncode == 1 and "invalid" in v.stdout, v.stdout


def test_overclaim_against_audited(server, base_card):
    base_card["verification"]["location"] = {**base_card["verification"].get("location", {}), "level": "evidenced"}
    base_card["verification"]["content"] = {**base_card["verification"].get("content", {}), "level": "evidenced"}
    serve(server, base_card)
    res = conform(server, against=audited())
    over = {n for s, n, _ in res if n.startswith("overclaim")}
    assert {"overclaim verification.location", "overclaim verification.content"} <= over
    v = validate_cli(server, "--against", "skyfi")
    assert "verification.location" in v.stdout and "verification.content" in v.stdout, v.stdout


@pytest.mark.xfail(strict=True, reason="overclaims are WARN in conformance; spec says a provider may not exceed the audited card (exit code stays 0)")
def test_overclaim_fails_conformance(server, base_card):
    base_card["verification"]["location"] = {**base_card["verification"].get("location", {}), "level": "evidenced"}
    serve(server, base_card)
    assert "overclaim verification.location" in names(conform(server, against=audited()), "fail")
