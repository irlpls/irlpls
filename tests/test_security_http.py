"""Outbound-HTTP security: credentials stay on their host, total deadline, SSRF address/port filter, pinned Rekor key.
Local 127.0.0.1 servers play 'attacker.example'; no external network."""
import http.server as hs
import threading
import time
import urllib.request

import pytest

from irlpls import evidence, http
from irlpls.adapters import usgs_water


def serve(handler_fn):
    class H(hs.BaseHTTPRequestHandler):
        def do_GET(self):
            handler_fn(self)

        def log_message(self, *a):
            pass
    srv = hs.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- 1. credentials never go to another host
def test_usgs_next_link_to_other_host_does_not_get_the_key(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, **kw):
        calls.append((url, dict(headers or {})))
        return {"features": [], "links": [{"rel": "next", "href": "https://attacker.example/steal"}]}
    monkeypatch.setattr(usgs_water.http, "get", fake_get)
    usgs_water.USGSWater()._ogc_pages((0, 0, 1, 1), {"X-Api-Key": "SECRET-KEY-123"})
    assert calls and all("attacker.example" not in u for u, _ in calls)


def test_usgs_stats_next_link_to_other_host_is_not_followed(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, **kw):
        calls.append(url)
        return {"features": [], "next": "https://attacker.example/x"}
    monkeypatch.setattr(usgs_water.http, "get", fake_get)
    usgs_water.daily_stats(["01"], headers={"X-Api-Key": "K"})
    assert calls == [usgs_water.STATS]


def test_usgs_same_host_next_link_is_followed(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, **kw):
        calls.append(url)
        nxt = [] if len(calls) > 1 else [{"rel": "next", "href": usgs_water.BASE + "/collections/p2"}]
        return {"features": [], "links": nxt}
    monkeypatch.setattr(usgs_water.http, "get", fake_get)
    usgs_water.USGSWater()._ogc_pages((0, 0, 1, 1), {"X-Api-Key": "K"})
    assert usgs_water.BASE + "/collections/p2" in calls


def test_same_origin_helper():
    base = "https://api.waterdata.usgs.gov/ogcapi/v1"
    assert http.same_origin_or_raise(base, base + "/x?y=1")
    for bad in ("https://attacker.example/x", "http://api.waterdata.usgs.gov/x", "https://api.waterdata.usgs.gov:8443/x",
                "https://u:p@api.waterdata.usgs.gov/x"):
        with pytest.raises(http.UnsafeURL):
            http.same_origin_or_raise(base, bad)


def test_redirect_to_other_origin_strips_key_header():
    seen = {}

    def target(h):
        seen["key"] = h.headers.get("X-Api-Key")
        h.send_response(200); h.send_header("Content-Type", "application/json"); h.end_headers(); h.wfile.write(b"{}")
    t = serve(target)

    def origin(h):
        h.send_response(302); h.send_header("Location", f"http://localhost:{t.server_address[1]}/"); h.end_headers()
    o = serve(origin)
    http.get(f"http://127.0.0.1:{o.server_address[1]}/", headers={"X-Api-Key": "SECRET"}, allow_private=True)
    assert "key" in seen and seen["key"] is None


# ---- 2. total deadline
def test_slow_drip_body_hits_total_deadline(monkeypatch):
    monkeypatch.setattr(http, "TOTAL_TIMEOUT", 1.0)

    def drip(h):
        h.send_response(200); h.send_header("Content-Length", "1000"); h.end_headers()
        try:
            for _ in range(100):
                h.wfile.write(b"x"); h.wfile.flush(); time.sleep(0.3)
        except OSError:
            pass
    s = serve(drip)
    t0 = time.monotonic()
    with pytest.raises(http.DeadlineExceeded):
        http.get(f"http://127.0.0.1:{s.server_address[1]}/", timeout=2, allow_private=True, raw=True)
    assert time.monotonic() - t0 < 5


def test_oversize_body_still_capped():
    s = serve(lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b"x" * 5000)))
    with pytest.raises(http.TooLarge):
        http.get(f"http://127.0.0.1:{s.server_address[1]}/", allow_private=True, raw=True, max_bytes=1000)


# ---- 3. SSRF filter
@pytest.mark.parametrize("addr", ["64:ff9b::7f00:1", "64:ff9b::a00:1", "64:ff9b::a9fe:a9fe", "64:ff9b:1::1", "::ffff:127.0.0.1",
                                  "::ffff:169.254.169.254", "169.254.169.254", "127.0.0.1", "10.0.0.1", "fe80::1", "::1",
                                  "fd00:ec2::254", "2002:7f00:1::", "2002:a9fe:a9fe::1", "::7f00:1",
                                  "2001:0:4136:e378:8000:63bf:3fff:fdd2"])
def test_non_public_forms_refused(addr):
    assert not http.is_public_ip(addr)


def test_public_addresses_still_pass():
    assert http.is_public_ip("93.184.216.34") and http.is_public_ip("2606:2800:220:1:248:1893:25c8:1946")
    assert http.is_public_ip("64:ff9b::5db8:d822")  # NAT64 of a public IPv4


@pytest.mark.parametrize("url", ["http://[64:ff9b::7f00:1]/", "http://[64:ff9b::a00:1]/", "http://169.254.169.254/latest/meta-data/",
                                 "https://93.184.216.34:6379/", "http://93.184.216.34:8080/", "file:///etc/passwd"])
def test_check_url_refuses(url, monkeypatch):
    monkeypatch.delenv("IRLPLS_ALLOW_PRIVATE_HOSTS", raising=False)
    with pytest.raises(http.UnsafeURL):
        http.check_url(url)


def test_ports_80_443_allowed_and_override_is_off_by_default(monkeypatch):
    monkeypatch.delenv("IRLPLS_ALLOW_PRIVATE_HOSTS", raising=False)
    http.check_url("https://93.184.216.34/"); http.check_url("http://93.184.216.34:80/"); http.check_url("https://93.184.216.34:443/")
    with pytest.raises(http.UnsafeURL):
        http.check_url("http://127.0.0.1:8080/")
    assert http.check_url("http://127.0.0.1:8080/", allow_private=True)


def test_redirect_to_odd_port_refused(monkeypatch):
    monkeypatch.delenv("IRLPLS_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(http, "_resolve", lambda host: ["93.184.216.34"])
    r = http._Redirects(allow_private=False)
    req = urllib.request.Request("https://provider.example/a")
    with pytest.raises(http.UnsafeURL):
        r.redirect_request(req, None, 302, "Found", {}, "https://provider.example:6379/")


# ---- 4. Rekor: a key supplied by the pack being verified is not trusted
def _rekor(opts, anchor="log.pem"):
    from tests import test_evidence as te
    url, extra = te.rekor(key=anchor)
    return te.run(te.card("verification.time", url), extra, **opts)


def test_rekor_without_pinned_key_fails_closed_even_with_pack_key():
    r = _rekor({})
    assert r["verdict"] != evidence.VERIFIED
    assert "pinned" in " ".join(r["reasons"]).lower()


def test_rekor_with_pinned_key_verifies_and_ignores_pack_anchor():
    from tests import test_evidence as te
    r = _rekor({"rekor_keys": [te._LOG_PEM()]}, anchor="../detached/publickey.pem")
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]


def test_rekor_pinned_wrong_key_fails_even_if_pack_offers_right_one():
    from tests import test_evidence as te
    r = _rekor({"rekor_keys": [te._DETACHED_PEM()]}, anchor="log.pem")
    assert r["verdict"] == evidence.FAILED
