"""Hosted entrypoint for the read-only irlpls MCP server. Same CLI as `irlpls-mcp`; it only adds deploy hardening.

    python deploy/serve.py --read-only --allow-remote --transport streamable-http --host 0.0.0.0 --port 8765

What it changes, and why (the hosted promise is "logs nothing"):
- Access log off: uvicorn is forced to access_log=False, so no line per request (no client IP, path or status).
- Log level WARNING for uvicorn and the MCP SDK: no session-id or "processing request" lines. Nothing in this stack
  logs request or response bodies at any level we run; tool arguments never reach a log.
- No outbound calls: the geocoder (OpenStreetMap Nominatim) is replaced by "unknown place" unless
  IRLPLS_DEPLOY_GEOCODE=1. Without this, a place written in a need would be relayed from the server to a third party.
  Matching still works from the place words and coverage text; agents can pass lat/lon to irlpls_request.
- Body cap 64 KB and at most 500 live sessions, when the SDK accepts those settings (mcp >= 2).
- Refuses to start without --read-only: the hosted server must never register irlpls_submit or the protocol tools.
"""
import inspect
import logging
import os
import sys

MAX_BODY = 64 * 1024
MAX_SESSIONS = 500


def harden():
    import uvicorn
    orig_init = uvicorn.Config.__init__

    def init(self, *a, **kw):
        kw["access_log"] = False
        kw["log_level"] = "warning"
        orig_init(self, *a, **kw)
    uvicorn.Config.__init__ = init

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "mcp", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True

    if os.environ.get("IRLPLS_DEPLOY_GEOCODE", "").strip() not in ("1", "true", "yes"):
        from irlpls import geo
        geo.geocode = lambda place: None

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
