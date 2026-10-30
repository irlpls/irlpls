"""A real, reproducible MCP run of the landing page's first example:
"is the Guadalupe at Comfort, TX good for tubing today".

Starts the irlpls MCP server over stdio (python -m irlpls.mcp_server) and drives it with the official `mcp` Python
client, calling the tools an agent would: request_data (need + why) -> submit_job -> get_job. Uses the live USGS
network. Every call and its exact JSON response is recorded with UTC timestamps.

    uv run --with mcp python tools/mcp_demo.py            # writes docs/examples/mcp-run-guadalupe.json

Trimming: long lists inside a response are cut to their first N items and replaced by a marker object
{"_trimmed": "...", "kept": N, "total": M}; nothing kept is changed. The full untrimmed response text is not stored,
but its byte length and sha256 are, so a reader knows how big the real response was.
"""
import asyncio
import hashlib
import json
import pathlib
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "examples" / "mcp-run-guadalupe.json"

NEED = "is the Guadalupe at Comfort, TX good for tubing today"
WHY = "user deciding whether to go tubing on the Guadalupe today"
POLICY = {"max_order_usd": 0}  # the landing page's call: free reads only

KEEP = {"plan": 8, "ruled_out": 60, "results": 12, "log": 20, "warnings": 10}  # list fields trimmed for size; everything else kept whole
DEFAULT_KEEP = 10


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def _a(obj, *names):
    """mcp 2.x uses snake_case attributes, 1.x camelCase."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def trim(v, key=None):
    """Cut long lists (marking what was cut); never edit a kept value."""
    if isinstance(v, dict):
        return {k: trim(x, k) for k, x in v.items()}
    if isinstance(v, list):
        n = KEEP.get(key, DEFAULT_KEEP)
        kept = [trim(x) for x in v[:n]]
        if len(v) > n:
            kept.append({"_trimmed": f"{len(v) - n} more items of '{key}' omitted", "kept": n, "total": len(v)})
        return kept
    return v


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "irlpls.mcp_server"], cwd=str(ROOT))
    calls = []
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        tools = (await s.list_tools()).tools

        async def call(name, args):
            t0 = now()
            res = await s.call_tool(name, args)
            t1 = now()
            text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            data = json.loads(text)
            calls.append({"tool": name, "arguments": args, "started_at": t0, "finished_at": t1,
                          "is_error": bool(_a(res, "is_error", "isError")), "response_bytes": len(text.encode()),
                          "response_sha256": hashlib.sha256(text.encode()).hexdigest(), "response": trim(data)})
            print(f"{t1} {name}: {len(text):,} bytes", file=sys.stderr)
            return data

        job = await call("request_data", {"need": NEED, "why": WHY, "policy": POLICY})
        if "error" in job:
            raise SystemExit(job["error"])
        await call("submit_job", {"job_id": job["id"]})
        await call("get_job", {"job_id": job["id"]})

    run = {"what": "irlpls MCP server, stdio, driven by the official mcp Python client; live USGS network",
           "command": "uv run --with mcp python tools/mcp_demo.py",
           "server": {"name": _a(init, "server_info", "serverInfo").name, "version": _a(init, "server_info", "serverInfo").version,
                      "protocol": _a(init, "protocol_version", "protocolVersion")},
           "client_python": sys.version.split()[0],
           "tools_listed": [{"name": t.name, "description_chars": len(t.description or "")} for t in tools],
           "trimming": "lists cut to their first N items (plan 8, ruled_out 60, results 12, log 20, other lists 10) with a _trimmed marker; "
                       "kept values unaltered. response_bytes/response_sha256 describe the full untrimmed response text.",
           "calls": calls}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
