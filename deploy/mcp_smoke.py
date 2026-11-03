"""MCP client smoke test, localhost only (no Docker, no network beyond 127.0.0.1).

    python deploy/mcp_smoke.py                    # hosted: deploy/serve.py over Streamable HTTP
    python deploy/mcp_smoke.py --stdio irlpls-mcp # stdio: an installed irlpls-mcp (or any command that starts it)
    python deploy/mcp_smoke.py --url https://mcp.irlpls.com/mcp   # after deploy: the live server (no log checks)

Hosted mode starts `deploy/serve.py --read-only --http` on 127.0.0.1 with the interpreter running this script, then
with the official MCP client: initialize, tools/list is exactly the 7 planning tools, irlpls_request answers
(river level at Comfort, TX -> usgs-water), irlpls_find_providers answers, GET /healthz is 200, a burst beyond the rate
limit gets 429, and the server's whole log contains no request text and no access lines. Exit 0 on success.
Needs irlpls and mcp >= 2 importable (`pip install irlpls-mcp`, or `uv run --with "mcp>=2" ...` in a checkout).
"""
import argparse
import asyncio
import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SEVEN = {"irlpls_answer", "irlpls_choose_provider", "irlpls_find_providers", "irlpls_get_card", "irlpls_get_job",
         "irlpls_list_capabilities", "irlpls_request"}
MARK = f"smokemarker{os.getpid()}"
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def _struct(r):
    if getattr(r, "structured_content", None):
        return r.structured_content
    sc = getattr(r, "structuredContent", None)
    if sc:
        return sc
    return json.loads(r.content[0].text)


async def exercise(session, live=True):
    await session.initialize()
    names = {t.name for t in (await session.list_tools()).tools}
    print("tools:", " ".join(sorted(names)))
    check(names == SEVEN, f"tools/list is exactly the 7 planning tools ({len(names)})")
    check("irlpls_submit" not in names and "irlpls_order" not in names, "no irlpls_submit / ordering tools")
    r = await session.call_tool("irlpls_request", {"need": f"river level at Comfort, TX now {MARK}",
                                                  "lat": 29.97, "lon": -98.9, "policy": {"max_order_usd": 0}})
    job = _struct(r)
    plan = job.get("plan") or []
    best = (plan[0] if plan else {}).get("card_id") or (job.get("best") or {}).get("card_id")
    check(not getattr(r, "is_error", getattr(r, "isError", False)) and best == "usgs-water",
          f"irlpls_request answers: best = {best}")
    r = await session.call_tool("irlpls_find_providers", {"need": "air quality in Munich now", "limit": 3})
    check(not getattr(r, "is_error", getattr(r, "isError", False)), "irlpls_find_providers answers")


async def run_http(url):
    from mcp.client.session import ClientSession
    try:
        from mcp.client.streamable_http import streamable_http_client as client
    except ImportError:  # older SDK name
        from mcp.client.streamable_http import streamablehttp_client as client
    async with client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await exercise(s)


async def run_stdio(cmd):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    argv = shlex.split(cmd)
    params = StdioServerParameters(command=argv[0], args=argv[1:] + ["--read-only"], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await exercise(s)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post_status(url):
    req = urllib.request.Request(url, data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}', method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def hosted():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    env = dict(os.environ, IRLPLS_RATE_BURST="30", IRLPLS_RATE_PER_MIN="60")
    proc = subprocess.Popen([sys.executable, str(HERE / "serve.py"), "--read-only", "--http", "--host", "127.0.0.1",
                             "--port", str(port)], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(120):
            try:
                if get(base + "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.25)
        check(get(base + "/healthz") == (200, b"ok\n"), "GET /healthz is 200")
        asyncio.run(run_http(base + "/mcp"))
        codes = [post_status(base + "/mcp") for _ in range(40)]
        check(429 in codes, f"burst of 40 POSTs is rate limited (first 429 at #{codes.index(429) + 1 if 429 in codes else '-'})")
    finally:
        proc.terminate()
        proc.wait(10)
    text = pathlib.Path(log.name).read_text()
    os.unlink(log.name)
    check(MARK not in text, "request text not in the server log")
    check("POST /mcp" not in text and "127.0.0.1:" not in text, "no access-log lines (no client IP, path or status)")
    print(f"server log, complete ({len(text.splitlines())} lines):")
    for line in text.splitlines():
        print("  | " + line)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stdio", metavar="CMD", help="test a stdio server started by CMD (it gets --read-only)")
    ap.add_argument("--url", help="test an already running Streamable HTTP endpoint")
    a = ap.parse_args(argv)
    if a.stdio:
        asyncio.run(run_stdio(a.stdio))
    elif a.url:
        asyncio.run(run_http(a.url))
    else:
        hosted()
    ok = all(results) and results
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
