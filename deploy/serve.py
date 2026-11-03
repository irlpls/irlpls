"""Hosted entrypoint for the read-only irlpls MCP server. Same CLI as `irlpls-mcp`; it only adds deploy hardening.

    python deploy/serve.py --read-only --allow-remote --transport streamable-http --host 0.0.0.0 --port 8765
    python deploy/serve.py --read-only --http            # local: 127.0.0.1:8765/mcp

What it changes, and why (the hosted promise is "logs nothing"):
- Access log off: uvicorn is forced to access_log=False, so no line per request (no client IP, path or status).
- Log level WARNING for uvicorn and the MCP SDK: no session-id or "processing request" lines. Nothing in this stack
  logs request or response bodies at any level we run; tool arguments never reach a log.
- No outbound calls: the geocoder (OpenStreetMap Nominatim) is replaced by the bundled offline gazetteer
  (irlpls/places.py: GeoNames cities of 50,000+ and capitals) unless IRLPLS_DEPLOY_GEOCODE=1. A place written in a
  need is never relayed to a third party. Smaller places match on words and coverage text; agents can pass lat/lon.
- Rate limits, in memory only (never logged, never written): per client 60 requests/min with a burst of 20, and
  600/min for the whole server. Over the limit: HTTP 429 with Retry-After. The client key is the address the platform
  proxy reports in IRLPLS_CLIENT_IP_HEADER (fly.toml sets fly-client-ip), else the TCP peer. Tune with
  IRLPLS_RATE_PER_MIN, IRLPLS_RATE_BURST, IRLPLS_RATE_GLOBAL_PER_MIN.
- GET /healthz answers 200 "ok" (platform health checks), without touching the MCP app.
- Body cap 64 KB and at most 500 live sessions (mcp >= 2).
- Refuses to start without --read-only: the hosted server must never register irlpls_submit or the protocol tools.
"""
import inspect
import json
import logging
import os
import sys
import threading
import time

MAX_BODY = 64 * 1024
MAX_SESSIONS = 500
MAX_CLIENTS = 10_000  # remembered rate-limit buckets; the fullest-refilled are dropped first


def _env_num(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


class RateLimit:
    """Token buckets: one per client key and one for the server. In memory; keys are never logged or stored."""

    def __init__(self, per_min=60, burst=20, global_per_min=600, clock=time.monotonic):
        self.rate, self.burst = per_min / 60.0, float(burst)
        self.g_rate, self.g_burst = global_per_min / 60.0, float(max(global_per_min / 6, burst))
        self.clock, self.lock = clock, threading.Lock()
        self.buckets = {}
        self.g = [self.g_burst, clock()]

    @staticmethod
    def _take(b, rate, cap, now):
        b[0] = min(cap, b[0] + (now - b[1]) * rate)
        b[1] = now
        if b[0] >= 1:
            b[0] -= 1
            return 0.0
        return (1 - b[0]) / rate

    def check(self, key):
        """0 when allowed, else seconds until the next request would be."""
        now = self.clock()
        with self.lock:
            b = self.buckets.get(key)
            if b is None:
                if len(self.buckets) >= MAX_CLIENTS:
                    self._prune(now)
                b = self.buckets[key] = [self.burst, now]
            wait = self._take(b, self.rate, self.burst, now)
            if wait:
                return wait
            gw = self._take(self.g, self.g_rate, self.g_burst, now)
            if gw:
                b[0] += 1  # not served: give the client its token back
            return gw

    def _prune(self, now):
        full = [k for k, b in self.buckets.items() if b[0] + (now - b[1]) * self.rate >= self.burst]
        for k in full or list(self.buckets)[: MAX_CLIENTS // 10]:
            del self.buckets[k]


def client_key(scope, header=None):
    header = (header if header is not None else os.environ.get("IRLPLS_CLIENT_IP_HEADER", "")).strip().lower()
    if header:
        for k, v in scope.get("headers") or ():
            if k.decode("latin-1").lower() == header:
                return v.decode("latin-1").split(",")[0].strip()
    c = scope.get("client")
    return c[0] if c else "-"


class Guard:
    """ASGI wrapper: /healthz, then the rate limit, then the MCP app."""

    def __init__(self, app, limit=None, header=None):
        self.app, self.header = app, header
        self.limit = limit or RateLimit(_env_num("IRLPLS_RATE_PER_MIN", 60), _env_num("IRLPLS_RATE_BURST", 20),
                                        _env_num("IRLPLS_RATE_GLOBAL_PER_MIN", 600))

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/healthz" and scope.get("method") in ("GET", "HEAD"):
            return await _reply(send, 200, b"ok\n", "text/plain")
        wait = self.limit.check(client_key(scope, self.header))
        if wait:
            secs = max(1, int(wait + 0.999))
            body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32000, "message": f"rate limited: retry after {secs} s"}}).encode()
            return await _reply(send, 429, body, "application/json", [(b"retry-after", str(secs).encode())])
        return await self.app(scope, receive, send)


async def _reply(send, status, body, ctype, extra=()):
    headers = [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode()), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def harden():
    import uvicorn
    orig_init = uvicorn.Config.__init__

    def init(self, app, *a, **kw):
        kw["access_log"] = False
        kw["log_level"] = "warning"
        orig_init(self, Guard(app), *a, **kw)
    uvicorn.Config.__init__ = init

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "mcp", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True

    if os.environ.get("IRLPLS_DEPLOY_GEOCODE", "").strip() not in ("1", "true", "yes"):
        from irlpls import geo, places
        geo.geocode = places.lookup

    try:  # mcp >= 2: inject the body cap and session cap into the streamable-http runner
        from mcp.server.mcpserver.server import MCPServer
        run = MCPServer.run_streamable_http_async
        params = inspect.signature(run).parameters

        async def capped(self, *a, **kw):
            if "max_request_body_size" in params:
                kw.setdefault("max_request_body_size", MAX_BODY)
            if "max_sessions" in params:
                kw.setdefault("max_sessions", MAX_SESSIONS)
            return await run(self, *a, **kw)
        MCPServer.run_streamable_http_async = capped
    except ImportError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--read-only" not in argv:
        raise SystemExit("deploy/serve.py: refusing to start without --read-only")
    os.environ["IRLPLS_MCP_READ_ONLY"] = "1"
    harden()
    from irlpls import mcp_server
    mcp_server.main(argv)


if __name__ == "__main__":
    main()
