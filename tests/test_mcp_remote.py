"""Remote-hosting readiness of the MCP server: read-only mode, transport flags, bounded job store."""
import asyncio
import sys
import types

import pytest

import irlpls
from irlpls import mcp_server

SIX = {"irlpls_request", "irlpls_answer", "irlpls_choose_provider", "irlpls_find_providers", "irlpls_get_card", "irlpls_get_job", "irlpls_list_capabilities"}


def _d(out):
    """A tool's result as a dict: structuredContent when the real mcp types were importable, else the dict itself."""
    return getattr(out, "structured_content", None) or out


def _stub(monkeypatch):
    reg = {}

    class Server:
        def __init__(self, name, instructions=None, version=None):
            self.instructions, self.version, self.ran = instructions, version, None

        def tool(self, name=None, annotations=None):
            return lambda fn: reg.setdefault(name, fn) and fn

        def run(self, *a, **kw):
            self.ran = (a, kw)

    class Field:
        def __init__(self, description=None):
            self.description = description
    for name, attrs in {"mcp": {}, "mcp.server": {}, "mcp.server.mcpserver": {"MCPServer": Server}, "pydantic": {"Field": Field}}.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    monkeypatch.delenv("IRLPLS_MCP_READ_ONLY", raising=False)
    return reg


def test_read_only_registers_exactly_seven_tools(monkeypatch):
    reg = _stub(monkeypatch)
    srv = mcp_server.build(read_only=True)
    assert set(reg) == SIX and "irlpls_submit" not in reg
    assert "This server can plan but not order; to order, use the provider's own MCP server or API" in srv.instructions
    assert "read-only" in srv.instructions


def test_env_var_enables_read_only_and_default_registers_submit(monkeypatch):
    reg = _stub(monkeypatch)
    monkeypatch.setenv("IRLPLS_MCP_READ_ONLY", "1")
    mcp_server.build()
    assert set(reg) == SIX
    reg.clear()
    monkeypatch.delenv("IRLPLS_MCP_READ_ONLY")
    srv = mcp_server.build()
    assert "irlpls_submit" in reg and "irlpls_approve" in reg and len(reg) == 9 + 6 and "read-only" not in srv.instructions  # + the six protocol tools


def test_read_only_get_job_ignores_refresh(monkeypatch):
    reg = _stub(monkeypatch)
    job = irlpls.request("river level at Comfort, TX now", geocode=False, lat=29.97, lon=-98.9, country_code="us", state="texas")
    calls = []
    monkeypatch.setattr(type(job), "refresh", lambda self: calls.append(1) or self)
    mcp_server.build(read_only=True)
    out = _d(reg["irlpls_get_job"](job.id, refresh=True))
    assert out["id"] == job.id and not calls
    reg.clear()
    mcp_server.build(read_only=False)
    reg["irlpls_get_job"](job.id, refresh=True)
    assert calls == [1]


def test_job_store_is_capped_oldest_dropped(monkeypatch):
    reg = _stub(monkeypatch)
    monkeypatch.setattr(mcp_server, "_created", type(mcp_server._created)())
    monkeypatch.setattr(mcp_server, "MAX_JOBS", 3)
    monkeypatch.setattr(irlpls.broker, "_JOBS", {})
    mcp_server.build(read_only=True)
    ids = [_d(reg["irlpls_request"]("river level at Comfort, TX now", lat=29.97, lon=-98.9))["id"] for _ in range(5)]
    assert len(set(ids)) == 5 and len(irlpls.broker._JOBS) == 3
    assert irlpls.get_job(ids[0]) is None and irlpls.get_job(ids[1]) is None and irlpls.get_job(ids[4])


def test_stdio_is_default_and_http_binds_localhost(monkeypatch):
    _stub(monkeypatch)
    srv = mcp_server.build()
    mcp_server.serve(srv)
    assert srv.ran == ((), {})                                    # plain mcp.run(): stdio, unchanged
    mcp_server.serve(srv, "streamable-http")
    assert srv.ran == ((), {"transport": "streamable-http", "host": "127.0.0.1", "port": 8765})
    seen = {}
    monkeypatch.setattr(mcp_server, "build", lambda read_only=None: types.SimpleNamespace(run=lambda *a, **k: seen.update(a=a, k=k)))
    mcp_server.main([])
    assert seen == {"a": (), "k": {}}
    with pytest.raises(SystemExit):
        mcp_server.serve(srv, "carrier-pigeon")


def test_read_only_through_the_real_sdk():
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401 (mcp >= 2)
    except ImportError:
        pytest.skip("needs mcp >= 2")
    srv = mcp_server.build(read_only=True)
    assert {t.name for t in asyncio.run(srv.list_tools())} == SIX
    r = asyncio.run(srv.call_tool("irlpls_get_job", {"job_id": "missing", "refresh": True}))
    assert r.is_error and "not_found" in r.content[0].text
