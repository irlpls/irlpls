"""Outbound-request hygiene (irlpls/http.py) and evidence_url rules (irlpls/validate.py). No network."""
import gzip
import io
import urllib.request

import pytest

from irlpls import http, validate


def test_only_http_schemes():
    for u in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "javascript:alert(1)"):
        with pytest.raises(http.UnsafeURL):
            http.check_url(u)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "172.16.0.1", "::1", "::ffff:127.0.0.1", "fe80::1", "0.0.0.0"])
def test_private_ip_literals_refused(ip):
    host = f"[{ip}]" if ":" in ip else ip
    with pytest.raises(http.UnsafeURL):
        http.check_url(f"http://{host}/x")


def test_hostname_resolving_to_private_refused(monkeypatch):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(http.UnsafeURL):
        http.check_url("https://evil.example/x")
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    assert http.check_url("https://ok.example/x").hostname == "ok.example"


def test_opt_out(monkeypatch):
    assert http.check_url("http://127.0.0.1:8000/x", allow_private=True)
    monkeypatch.setenv("IRLPLS_ALLOW_PRIVATE_HOSTS", "1")
    assert http.check_url("http://localhost/x")


def test_require_https_option():
    with pytest.raises(http.UnsafeURL):
        http.check_url("http://example.com", allow_private=True, allow_http=False)


def test_redirect_downgrade_and_private_refused(monkeypatch):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    h = http._Redirects()
    req = urllib.request.Request("https://a.example/x")
    with pytest.raises(http.UnsafeURL):
        h.redirect_request(req, None, 302, "Found", {}, "http://a.example/y")
    with pytest.raises(http.UnsafeURL):
        h.redirect_request(req, None, 302, "Found", {}, "https://169.254.169.254/latest")
    with pytest.raises(http.UnsafeURL):
        h.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://b.example/z").full_url == "https://b.example/z"
    assert h.max_redirections == http.MAX_REDIRECTS <= 5


class _Resp:
    def __init__(self, data, headers=None):
        self._b, self.headers = io.BytesIO(data), headers or {}

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _serve(monkeypatch, data, headers=None):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(http, "_open", lambda req, timeout, allow_private=False: _Resp(data, headers))


def test_normal_json_and_gzip(monkeypatch):
    _serve(monkeypatch, b'{"a": 1}')
    assert http.get("https://x.example/") == {"a": 1}
    _serve(monkeypatch, gzip.compress(b'{"a": 2}'), {"Content-Encoding": "gzip"})
    assert http.get("https://x.example/") == {"a": 2}


def test_size_cap(monkeypatch):
    _serve(monkeypatch, b"x" * 100)
    with pytest.raises(http.TooLarge):
        http.get("https://x.example/", raw=True, max_bytes=50)


def test_declared_length_cap(monkeypatch):
    _serve(monkeypatch, b"x", {"Content-Length": str(http.MAX_BYTES + 1)})
    with pytest.raises(http.TooLarge):
        http.get("https://x.example/", raw=True)


def test_gzip_bomb(monkeypatch):
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(bomb) < 100_000
    _serve(monkeypatch, bomb, {"Content-Encoding": "gzip"})
    with pytest.raises(http.TooLarge):
        http.get("https://x.example/", raw=True, max_bytes=1_000_000)


def test_request_refuses_private_before_connecting(monkeypatch):
    called = []
    monkeypatch.setattr(http, "_open", lambda *a, **k: called.append(1))
    with pytest.raises(http.UnsafeURL):
        http.get("http://169.254.169.254/latest/meta-data")
    assert not called


def test_default_limits():
    assert http.MAX_BYTES == 10 * 1024 * 1024


# evidence_url

def _card(url, level="evidenced"):
    g = {"level": level, "unknown": "x", "method": "m", "evidence_url": url}
    return {"spec_version": validate.VERSION, "capability": "com.irlpls.capture.card",
            "verification": {k: dict(g) for k in validate.GUARANTEES}}


def _errs(card):
    return [m for sev, w, m in validate.problems(card) if sev == "error" and w.startswith("verification.")]


def test_evidence_url_https_ok():
    assert not any("evidence_url" in m for m in _errs(_card("https://proof.example.com/report")))


@pytest.mark.parametrize("url", ["http://proof.example.com/r", "ftp://proof.example.com/r", "proof.example.com/r", "file:///etc/passwd"])
def test_evidence_url_must_be_https(url):
    assert any("https" in m for m in _errs(_card(url)))


@pytest.mark.parametrize("url", ["https://localhost/p", "https://127.0.0.1/p", "https://10.1.2.3/p", "https://169.254.169.254/p",
                                 "https://[::1]/p", "https://printer.local/p", "https://db.internal/p"])
def test_evidence_url_private_host_flagged(url):
    assert any("private" in m for m in _errs(_card(url)))


def test_claimed_needs_no_evidence_url():
    assert not _errs(_card("", level="claimed"))


def test_private_host_helper():
    assert validate.private_host("https://192.168.0.1/x") and not validate.private_host("https://example.com/x")
