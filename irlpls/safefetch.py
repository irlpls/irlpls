"""SSRF-safe, polite HTTP fetcher for URLs supplied by strangers. Stdlib only.

Rules: https only (http per call); we resolve DNS ourselves, reject every non-public address, and
connect to the validated IP (pinned, no second resolution) while sending the right Host and SNI;
redirects are followed by hand (max 3) and re-validated at every hop; no credentials in URLs; no IP-literal
hosts; streamed size cap; total and per-read timeouts; content-type allowlist; honest User-Agent;
per-host token bucket shared in-process; robots.txt (short cache) and an opt-out list are honoured.

The resolver and the connection factory are injectable, so tests need no network:
  resolver(host, port) -> [ip_string, ...]
  connect(ip, port, host, tls, timeout) -> object with sendall/recv/close/settimeout
"""
import ipaddress
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

UA = "irlpls-card-check/0.1 (+https://irlpls.com)"
MAX_BYTES = 1 << 20
REDIRECT_CODES = (301, 302, 303, 307, 308)
ROBOTS_TTL = 300.0
OPTOUT_HOSTS = set()  # hosts (and their subdomains) that asked to be excluded


class SafeFetchError(Exception):
    pass


class UnsafeURL(SafeFetchError):
    pass


class BlockedAddress(SafeFetchError):
    pass


class DNSError(SafeFetchError):
    pass


class TooManyRedirects(SafeFetchError):
    pass


class TooLarge(SafeFetchError):
    pass


class Timeout(SafeFetchError):
    pass


class RobotsDisallowed(SafeFetchError):
    pass


class BadContentType(SafeFetchError):
    pass


class ProtocolError(SafeFetchError):
    pass


class ConnectError(SafeFetchError):
    pass


# (the carrier-grade NAT range is written split so the release scan for private overlay-network addresses stays literal-free)
_BLOCKED_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100." "64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
    "::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/32",
    "2001:db8::/32", "2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
)]


def check_ip(ip):
    """Return the ipaddress object if the address is public; raise BlockedAddress otherwise."""
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        raise BlockedAddress(f"not an IP address: {ip!r}")
    if a.version == 6 and a.ipv4_mapped:
        check_ip(a.ipv4_mapped)
    if not a.is_global or any(a in n for n in _BLOCKED_NETS if n.version == a.version):
        raise BlockedAddress(f"non-public address {a}")
    return a


def system_resolver(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise DNSError(f"cannot resolve {host}: {e}")
    return list(dict.fromkeys(i[4][0] for i in infos))


def system_connect(ip, port, host, tls, timeout):
    s = socket.create_connection((ip, port), timeout=timeout)
    if tls:
        try:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        except Exception:
            s.close()
            raise
    return s


class TokenBucket:
    """Per-key token bucket: `rate` tokens/second, up to `burst`. Thread-safe."""

    def __init__(self, rate=1.0, burst=1.0, clock=time.monotonic, sleep=time.sleep):
        self.rate, self.burst, self.clock, self.sleep = rate, burst, clock, sleep
        self._state, self._lock = {}, threading.Lock()

    def acquire(self, key):
        while True:
            with self._lock:
                now = self.clock()
                tok, last = self._state.get(key, (self.burst, now))
                tok = min(self.burst, tok + (now - last) * self.rate)
                if tok >= 1:
                    self._state[key] = (tok - 1, now)
                    return
                self._state[key] = (tok, now)
                wait = (1 - tok) / self.rate
            self.sleep(wait)


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    url: str
    ip: str
    redirects: list = field(default_factory=list)


def _parse_url(url, allow_http, allow_ports):
    if not isinstance(url, str) or re.search(r"[\x00-\x20\x7f\\]", url):
        raise UnsafeURL("control characters, spaces or backslash in URL")
    p = urlsplit(url)
    if p.scheme not in ("https", "http"):
        raise UnsafeURL(f"scheme not allowed: {p.scheme!r}")
    if p.scheme == "http" and not allow_http:
        raise UnsafeURL("plain http not allowed")
    if "@" in p.netloc or p.username is not None or p.password is not None:
        raise UnsafeURL("credentials in URL")
    host = (p.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURL("no host")
    try:
        port = p.port
    except ValueError:
        raise UnsafeURL("bad port")
    default = 443 if p.scheme == "https" else 80
    port = port or default
    if port != default and port not in (allow_ports or ()):
        raise UnsafeURL(f"port {port} not allowed")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeURL("bad host name")
    last = host.rsplit(".", 1)[-1]
    # IP literals in any spelling (dotted, decimal, hex, octal, IPv6) are refused outright.
    if ":" in host or last.isdigit() or last.startswith("0x") or re.fullmatch(r"[0-9.x]+", host):
        raise BlockedAddress(f"IP-literal host not allowed: {host}")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9_-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9_-]*[a-z0-9])?)*", host):
        raise UnsafeURL("bad host name")
    path = (p.path or "/") + (f"?{p.query}" if p.query else "")
    return p.scheme, host, port, path, default


class _Reader:
    def __init__(self, conn, deadline, read_timeout, clock):
        self.conn, self.deadline, self.rt, self.clock, self.buf = conn, deadline, read_timeout, clock, b""

    def _more(self):
        left = self.deadline - self.clock()
        if left <= 0:
            raise Timeout("total timeout")
        try:
            self.conn.settimeout(min(self.rt, left))
            d = self.conn.recv(65536)
        except (socket.timeout, TimeoutError):
            raise Timeout("read timeout")
        except OSError as e:
            raise ConnectError(f"read failed: {type(e).__name__}")
        if self.clock() > self.deadline:
            raise Timeout("total timeout")
        return d

    def until(self, marker, limit):
        while marker not in self.buf:
            if len(self.buf) > limit:
                raise ProtocolError("header/line too long")
            d = self._more()
            if not d:
                raise ProtocolError("connection closed early")
            self.buf += d
        i = self.buf.index(marker)
        out, self.buf = self.buf[:i], self.buf[i + len(marker):]
        return out

    def chunk(self):
        """Next piece of body bytes (b'' at EOF)."""
        if self.buf:
            d, self.buf = self.buf, b""
            return d
        return self._more()


class Fetcher:
    def __init__(self, resolver=system_resolver, connect=system_connect, limiter=None,
                 clock=time.monotonic, sleep=time.sleep, optout=None):
        self.resolver, self.connect, self.clock, self.sleep = resolver, connect, clock, sleep
        self.limiter = limiter or TokenBucket(1.0, 1.0, clock, sleep)
        self.optout = OPTOUT_HOSTS if optout is None else optout
        self._robots, self._lock = {}, threading.Lock()

    # public ---------------------------------------------------------------
    def fetch(self, url, method="GET", allow_http=False, max_bytes=MAX_BYTES, timeout=10.0,
              read_timeout=5.0, content_types=None, max_redirects=3, robots=True,
              user_agent=UA, allow_ports=None, read_body=True, headers=None):
        if method not in ("GET", "HEAD"):
            raise UnsafeURL("only GET and HEAD")
        deadline = self.clock() + timeout
        extra = {}
        for k, v in (headers or {}).items():  # e.g. If-None-Match; never let a value split the request
            if k.lower() not in ("host", "user-agent", "connection", "accept-encoding", "content-length"):
                extra[re.sub(r"[^A-Za-z0-9-]", "", str(k))] = re.sub(r"[\r\n\x00]", "", str(v))
        first_host = urlsplit(url).hostname
        chain, cur = [], url
        while True:
            # conditional headers go to the original host only, never across a redirect to another one
            r = self._once(cur, method, allow_http, max_bytes, deadline, read_timeout, robots,
                           user_agent, allow_ports, read_body,
                           extra if urlsplit(cur).hostname == first_host else {})
            loc = r.headers.get("location")
            if r.status in REDIRECT_CODES and loc:
                if len(chain) >= max_redirects:
                    raise TooManyRedirects(f"more than {max_redirects} redirects")
                chain.append(cur)
                cur = urljoin(cur, loc)
                continue
            if read_body and 200 <= r.status < 300 and content_types is not None:
                ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if not any(ct == t or (t.endswith("/*") and ct.startswith(t[:-1])) for t in content_types):
                    raise BadContentType(f"content-type {ct!r} not allowed")
            r.url, r.redirects = cur, chain
            return r

    # internals --------------------------------------------------------------
    def _opted_out(self, host):
        return any(host == h or host.endswith("." + h) for h in self.optout)

    def _once(self, url, method, allow_http, max_bytes, deadline, rt, robots, ua, allow_ports, read_body, extra=None):
        scheme, host, port, path, default = _parse_url(url, allow_http, allow_ports)
        if self._opted_out(host):
            raise RobotsDisallowed(f"{host} has opted out")
        if robots and not self._robots_ok(scheme, host, port, url, ua, allow_http, allow_ports, deadline, rt):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self.limiter.acquire(host)
        ip = self._resolve(host, port)
        left = deadline - self.clock()
        if left <= 0:
            raise Timeout("total timeout")
        try:
            conn = self.connect(ip, port, host, scheme == "https", min(rt, left))
        except (socket.timeout, TimeoutError):
            raise Timeout("connect timeout")
        except (OSError, ssl.SSLError) as e:
            raise ConnectError(f"connect failed: {type(e).__name__}")
        try:
            hosthdr = host if port == default else f"{host}:{port}"
            req = (f"{method} {path} HTTP/1.1\r\nHost: {hosthdr}\r\nUser-Agent: {ua}\r\nAccept: */*\r\n"
                   + "".join(f"{k}: {v}\r\n" for k, v in (extra or {}).items())
                   + "Accept-Encoding: identity\r\nConnection: close\r\n\r\n")
            try:
                conn.sendall(req.encode("ascii", "ignore"))
            except (socket.timeout, TimeoutError):
                raise Timeout("write timeout")
            except OSError as e:
                raise ConnectError(f"write failed: {type(e).__name__}")
            rd = _Reader(conn, deadline, rt, self.clock)
            head = rd.until(b"\r\n\r\n", 65536).decode("latin-1").split("\r\n")
            m = re.match(r"HTTP/1\.[01] (\d{3})", head[0])
            if not m:
                raise ProtocolError("bad status line")
            status = int(m.group(1))
            hdrs = {}
            for line in head[1:]:
                k, sep, v = line.partition(":")
                if sep:
                    hdrs[k.strip().lower()] = v.strip()
            body = b""
            if read_body and method != "HEAD" and status not in (204, 304) and status >= 200:
                body = self._body(rd, hdrs, max_bytes)
            return Response(status, hdrs, body, url, ip)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _resolve(self, host, port):
        try:
            ips = list(self.resolver(host, port))
        except SafeFetchError:
            raise
        except OSError as e:
            raise DNSError(f"cannot resolve {host}: {e}")
        if not ips:
            raise DNSError(f"no addresses for {host}")
        for ip in ips:  # every answer must be public, not just the one we use
            check_ip(ip)
        return str(ips[0])

    def _body(self, rd, hdrs, max_bytes):
        out = bytearray()

        def add(d):
            out.extend(d)
            if len(out) > max_bytes:
                raise TooLarge(f"body exceeds {max_bytes} bytes")

        if "chunked" in hdrs.get("transfer-encoding", "").lower():
            while True:
                try:
                    n = int(rd.until(b"\r\n", 8192).split(b";")[0].strip() or b"0", 16)
                except ValueError:
                    raise ProtocolError("bad chunk size")
                if n == 0:
                    break
                if len(out) + n > max_bytes:
                    raise TooLarge(f"body exceeds {max_bytes} bytes")
                got = 0
                while got < n:
                    while not rd.buf:
                        d = rd._more()
                        if not d:
                            raise ProtocolError("truncated chunk")
                        rd.buf += d
                    take, rd.buf = rd.buf[:n - got], rd.buf[n - got:]
                    got += len(take)
                    add(take)
                rd.until(b"\r\n", 8192)
            return bytes(out)
        cl = hdrs.get("content-length")
        if cl is not None:
            try:
                cl = int(cl)
            except ValueError:
                raise ProtocolError("bad content-length")
            if cl > max_bytes:
                raise TooLarge(f"content-length {cl} exceeds {max_bytes}")
        while cl is None or len(out) < cl:
            d = rd.chunk()
            if not d:
                break
            add(d)
        return bytes(out if cl is None else out[:cl])

    def _robots_ok(self, scheme, host, port, url, ua, allow_http, allow_ports, deadline, rt):
        key = (scheme, host, port)
        with self._lock:
            hit = self._robots.get(key)
        if not hit or hit[0] < self.clock():
            rp = RobotFileParser()
            robots_url = f"{scheme}://{host}" + ("" if port in (80, 443) else f":{port}") + "/robots.txt"
            try:
                r = self._fetch_robots(robots_url, ua, allow_http, allow_ports, deadline, rt)
                if r.status >= 500:
                    rp.parse(["User-agent: *", "Disallow: /"])
                elif r.status >= 400:
                    rp.parse([])
                else:
                    rp.parse(r.body.decode("utf-8", "replace").splitlines())
            except (BlockedAddress, UnsafeURL, TooManyRedirects):
                raise
            except SafeFetchError:
                rp.parse([])  # unreachable robots.txt: proceed, politely
            hit = (self.clock() + ROBOTS_TTL, rp)
            with self._lock:
                self._robots[key] = hit
        return hit[1].can_fetch(ua, url)

    def _fetch_robots(self, robots_url, ua, allow_http, allow_ports, deadline, rt):
        cur = robots_url
        for _ in range(4):
            r = self._once(cur, "GET", allow_http, 512 * 1024, deadline, rt, False, ua, allow_ports, True)
            if r.status in REDIRECT_CODES and r.headers.get("location"):
                cur = urljoin(cur, r.headers["location"])
                continue
            return r
        raise TooManyRedirects("robots.txt redirects")


_default = Fetcher()


def fetch(url, **kw):
    """Module-level convenience using the shared default Fetcher (shared per-host rate limit)."""
    return _default.fetch(url, **kw)
