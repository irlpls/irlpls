"""irlpls/safefetch.py: SSRF suite, redirects, caps, timeouts, robots, rate. No network."""
import socket

import pytest

from irlpls import safefetch as sf

PUB = "93.184.216.34"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Conn:
    def __init__(self, chunks, clock=None, step=0.0, exc=None):
        self.chunks, self.clock, self.step, self.exc, self.sent = list(chunks), clock, step, exc, b""

    def settimeout(self, t):
        pass

    def sendall(self, b):
        self.sent += b

    def recv(self, n):
        if self.clock and self.step:
            self.clock.t += self.step
        if self.exc and not self.chunks:
            raise self.exc
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        pass


def resp(status=200, body=b"ok", extra="", ctype="application/json"):
    h = f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n{extra}\r\n"
    return h.encode() + body


class Env:
    """Fetcher wired to fakes. routes: {(host, path): bytes | callable -> Conn}."""

    def __init__(self, routes=None, dns=None):
        self.clock = Clock()
        self.routes, self.dns, self.calls, self.dns_calls = routes or {}, dns or {}, [], []
        self.f = sf.Fetcher(self.resolve, self.connect, sf.TokenBucket(1000, 1000, self.clock, self.clock.sleep),
                            self.clock, self.clock.sleep, optout=set())

    def resolve(self, host, port):
        self.dns_calls.append(host)
        v = self.dns.get(host, [PUB])
        return v(len(self.dns_calls)) if callable(v) else v

    def connect(self, ip, port, host, tls, timeout):
        self.calls.append((ip, port, host, tls))
        self._last = (host, ip)
        return _Pending(self)

    def get(self, url, **kw):
        kw.setdefault("robots", False)
        return self.f.fetch(url, **kw)


class _Pending:
    """Conn that picks its response once the request line arrives."""

    def __init__(self, env):
        self.env, self.inner, self.sent = env, None, b""

    def settimeout(self, t):
        pass

    def sendall(self, b):
        self.sent += b
        path = b.split(b" ")[1].decode()
        host = [l for l in b.decode().split("\r\n") if l.startswith("Host:")][0][6:]
        r = self.env.routes[(host, path)]
        self.inner = r(self.env) if callable(r) else Conn([r])

    def recv(self, n):
        return self.inner.recv(n)

    def close(self):
        pass


def test_happy_path_pins_ip_sets_host_ua():
    e = Env({("a.example", "/x?q=1"): resp(body=b'{"a":1}')})
    r = e.get("https://a.example/x?q=1", content_types=["application/json"])
    assert r.status == 200 and r.body == b'{"a":1}'
    assert e.calls == [(PUB, 443, "a.example", True)] and e.dns_calls == ["a.example"]


def test_request_headers():
    e = Env({("a.example", "/"): resp()})
    sent = []
    orig = _Pending.sendall
    _Pending.sendall = lambda self, b: (sent.append(b.decode()), orig(self, b))[1]
    try:
        e.get("https://a.example/")
    finally:
        _Pending.sendall = orig
    assert "User-Agent: irlpls-card-check/0.1 (+https://irlpls.com)" in sent[0] and "Host: a.example\r\n" in sent[0]
    assert "Cookie" not in sent[0] and "Authorization" not in sent[0]


def _sent_requests(e, url, **kw):
    sent = []
    orig = _Pending.sendall
    _Pending.sendall = lambda self, b: (sent.append(b.decode()), orig(self, b))[1]
    try:
        e.get(url, **kw)
    finally:
        _Pending.sendall = orig
    return sent


def test_extra_headers_sent_but_cannot_split_request_or_override_host():
    e = Env({("a.example", "/"): resp()})
    sent = _sent_requests(e, "https://a.example/", headers={
        "If-None-Match": 'W/"v1"\r\nX-Evil: 1', "Host": "evil.example", "User-Agent": "x"})
    assert 'If-None-Match: W/"v1"X-Evil: 1\r\n' in sent[0] and "\r\nX-Evil" not in sent[0]
    assert "Host: a.example\r\n" in sent[0] and "evil.example" not in sent[0]


def test_extra_headers_not_sent_to_another_host_after_redirect():
    e = Env({("a.example", "/"): resp(302, b"", "Location: https://b.example/y\r\n"), ("b.example", "/y"): resp()},
            dns={"b.example": ["8.8.8.8"]})
    sent = _sent_requests(e, "https://a.example/", headers={"If-None-Match": '"abc"'})
    assert len(sent) == 2 and "If-None-Match" in sent[0] and "If-None-Match" not in sent[1]


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.8.8", "10.0.0.1", "172.16.5.5", "172.31.255.255", "192.168.1.1", "169.254.169.254",
    "100." "64.0.1", "100." "127.255.254", "0.0.0.0", "224.0.0.1", "255.255.255.255", "198.18.0.1",
    "::1", "::", "fe80::1", "fd00::1", "fd00:ec2::254", "fc00::1", "ff02::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "::ffff:169.254.169.254", "64:ff9b::7f00:1", "2002:7f00:1::1", "2001:db8::1",
])
def test_blocked_addresses(ip):
    with pytest.raises(sf.BlockedAddress):
        sf.check_ip(ip)
    e = Env(dns={"evil.example": [ip]})
    with pytest.raises(sf.BlockedAddress):
        e.get("https://evil.example/")
    assert e.calls == []


@pytest.mark.parametrize("ip", [PUB, "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ok(ip):
    sf.check_ip(ip)


@pytest.mark.parametrize("host", [
    "127.0.0.1", "2130706433", "0x7f000001", "0x7f.0.0.1", "0177.0.0.1", "017700000001", "127.1", "0",
    "[::1]", "[::ffff:127.0.0.1]", "[fd00::1]", "169.254.169.254", "10.1", "1.2.3.4",
])
def test_ip_literal_hosts_refused(host):
    e = Env()
    with pytest.raises(sf.SafeFetchError):
        e.get(f"https://{host}/")
    assert e.calls == [] and e.dns_calls == []


def test_mixed_answer_blocked():
    e = Env(dns={"m.example": [PUB, "10.0.0.5"]})
    with pytest.raises(sf.BlockedAddress):
        e.get("https://m.example/")


def test_rebinding_resolver_only_called_once_and_ip_pinned():
    e = Env({("r.example", "/"): resp()}, dns={"r.example": lambda n: [PUB] if n == 1 else ["127.0.0.1"]})
    r = e.get("https://r.example/")
    assert r.status == 200 and e.dns_calls == ["r.example"] and e.calls[0][0] == PUB


def test_rebinding_private_on_first_answer_blocked():
    e = Env(dns={"r.example": lambda n: ["127.0.0.1"] if n == 1 else [PUB]})
    with pytest.raises(sf.BlockedAddress):
        e.get("https://r.example/")


@pytest.mark.parametrize("url", [
    "https://user:pw@a.example/", "https://user@a.example/", "https://@a.example/", "http://a.example/",
    "ftp://a.example/", "file:///etc/passwd", "gopher://a.example/", "https://a.example:8443/",
    "https://a.example\\@evil.example/", "https://a.example/ x", "https:///x", "https://a.example:99999/",
])
def test_unsafe_urls(url):
    e = Env()
    with pytest.raises(sf.SafeFetchError):
        e.get(url)
    assert e.calls == []


def test_http_only_when_allowed():
    e = Env({("a.example", "/"): resp()})
    assert e.get("http://a.example/", allow_http=True).status == 200 and e.calls[0][1:] == (80, "a.example", False)


def test_redirect_followed_and_revalidated():
    e = Env({("a.example", "/"): resp(302, b"", "Location: https://b.example/y\r\n"), ("b.example", "/y"): resp()},
            dns={"b.example": ["8.8.8.8"]})
    r = e.get("https://a.example/")
    assert r.status == 200 and r.url == "https://b.example/y" and r.redirects == ["https://a.example/"]
    assert [c[0] for c in e.calls] == [PUB, "8.8.8.8"]


@pytest.mark.parametrize("loc", ["https://internal.example/", "https://127.0.0.1/", "https://[::1]/", "http://b.example/", "/\\@x"])
def test_redirect_to_private_blocked(loc):
    e = Env({("a.example", "/"): resp(302, b"", f"Location: {loc}\r\n")}, dns={"internal.example": ["10.0.0.9"]})
    with pytest.raises(sf.SafeFetchError):
        e.get("https://a.example/")
    assert len(e.calls) == 1


def test_redirect_loop_and_limit():
    e = Env({("a.example", "/"): resp(302, b"", "Location: /\r\n")})
    with pytest.raises(sf.TooManyRedirects):
        e.get("https://a.example/")
    assert len(e.calls) == 4  # original + 3 hops
    e2 = Env({("a.example", "/"): resp(302, b"", "Location: /1\r\n"), ("a.example", "/1"): resp()})
    assert e2.get("https://a.example/", max_redirects=1).status == 200


def test_oversize_content_length_and_streamed():
    e = Env({("a.example", "/"): resp(body=b"x" * 100)})
    with pytest.raises(sf.TooLarge):
        e.get("https://a.example/", max_bytes=50)
    assert len(e.get("https://a.example/", max_bytes=100).body) == 100
    head = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n"
    e = Env({("a.example", "/"): lambda env: Conn([head] + [b"y" * 1000] * 5000)})
    with pytest.raises(sf.TooLarge):
        e.get("https://a.example/", max_bytes=10_000)


def test_default_cap_is_1mib():
    head = b"HTTP/1.1 200 OK\r\n\r\n"
    e = Env({("a.example", "/"): lambda env: Conn([head] + [b"y" * 65536] * 20)})
    with pytest.raises(sf.TooLarge):
        e.get("https://a.example/")


def test_chunked_body_and_cap():
    body = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
    e = Env({("a.example", "/"): body})
    assert e.get("https://a.example/").body == b"hello world"
    with pytest.raises(sf.TooLarge):
        e.get("https://a.example/", max_bytes=8)


def test_slow_body_total_timeout():
    head = b"HTTP/1.1 200 OK\r\n\r\n"
    env = Env()
    env.routes[("a.example", "/")] = lambda e: Conn([head] + [b"z"] * 100, clock=e.clock, step=3.0)
    with pytest.raises(sf.Timeout):
        env.get("https://a.example/", timeout=10)


def test_read_timeout_socket():
    env = Env()
    env.routes[("a.example", "/")] = lambda e: Conn([], exc=socket.timeout())
    with pytest.raises(sf.Timeout):
        env.get("https://a.example/")


def test_content_type_allowlist():
    e = Env({("a.example", "/"): resp(ctype="application/zip")})
    with pytest.raises(sf.BadContentType):
        e.get("https://a.example/", content_types=["application/json", "text/*"])
    e = Env({("a.example", "/"): resp(ctype="text/html; charset=utf-8")})
    assert e.get("https://a.example/", content_types=["text/*"]).status == 200


def test_error_status_returned_not_raised():
    e = Env({("a.example", "/"): resp(404, b"nope", ctype="text/html")})
    assert e.get("https://a.example/", content_types=["application/json"]).status == 404


def test_head_has_no_body():
    e = Env({("a.example", "/"): b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n"})
    r = e.get("https://a.example/", method="HEAD")
    assert r.status == 200 and r.body == b""


def test_robots_disallow_and_cache():
    txt = b"User-agent: irlpls-card-check\nDisallow: /private\n"
    e = Env({("a.example", "/robots.txt"): resp(body=txt, ctype="text/plain"), ("a.example", "/ok"): resp(), ("a.example", "/private"): resp()})
    assert e.f.fetch("https://a.example/ok").status == 200
    with pytest.raises(sf.RobotsDisallowed):
        e.f.fetch("https://a.example/private")
    assert e.dns_calls.count("a.example") == 2  # one robots fetch + one page; second page hit the cache
    e.clock.t += sf.ROBOTS_TTL + 1
    e.f.fetch("https://a.example/ok")
    assert e.dns_calls.count("a.example") == 4


def test_robots_missing_allows_and_5xx_blocks():
    e = Env({("a.example", "/robots.txt"): resp(404, b""), ("a.example", "/"): resp()})
    assert e.f.fetch("https://a.example/").status == 200
    e = Env({("b.example", "/robots.txt"): resp(503, b""), ("b.example", "/"): resp()})
    with pytest.raises(sf.RobotsDisallowed):
        e.f.fetch("https://b.example/")


def test_optout_host():
    e = Env({("a.example", "/"): resp()})
    e.f.optout.add("a.example")
    with pytest.raises(sf.RobotsDisallowed):
        e.get("https://sub.a.example/")
    assert e.calls == []


def test_per_host_rate_limit():
    env = Env({("a.example", "/"): resp(), ("b.example", "/"): resp()})
    env.f.limiter = sf.TokenBucket(1.0, 1.0, env.clock, env.clock.sleep)
    env.get("https://a.example/")
    env.get("https://b.example/")
    assert env.clock.t == 0  # different hosts do not wait
    env.get("https://a.example/")
    assert env.clock.t == pytest.approx(1.0)
    env.get("https://a.example/")
    assert env.clock.t == pytest.approx(2.0)


def test_token_bucket_burst():
    c = Clock()
    b = sf.TokenBucket(1.0, 3.0, c, c.sleep)
    for _ in range(3):
        b.acquire("h")
    assert c.t == 0
    b.acquire("h")
    assert c.t == pytest.approx(1.0)


def test_no_addresses_is_dns_error():
    e = Env(dns={"n.example": []})
    with pytest.raises(sf.DNSError):
        e.get("https://n.example/")
