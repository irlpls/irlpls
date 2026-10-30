"""Private MCP client transport for the A6 adapters (Quest, Airtasker), plus the bearer-token error mapping Hover shares.
Not an adapter: skipped by discovery because the module name starts with "_". Stdlib only.

MCP Streamable HTTP (spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports):
1. POST {"jsonrpc":"2.0","id":1,"method":"initialize",...} with Accept "application/json, text/event-stream". The server
   may answer with an Mcp-Session-Id header; every later request carries it, plus MCP-Protocol-Version.
2. POST the notification {"jsonrpc":"2.0","method":"notifications/initialized"} (the server answers 202, no body).
3. POST {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":..,"arguments":{..}}}. The answer is either one
   JSON object or an SSE stream ("data: {...}" lines) that ends with the response carrying the same id.
A tool's own failure comes back as a result with isError: true (raised here as ToolError); a protocol failure as a
JSON-RPC error (raised as MCPError).

Requests go through irlpls.http.request(return_headers=True) (the session id is a response header): https only, public
hosts, credentials dropped on a cross-origin redirect, size caps, and the provider's rate limit when a provider is named
(Session(provider=card id), or ratelimit.using around the call).
"""
import contextlib
import json

from .. import http, ratelimit
from .base import MissingCredentials, prepared_call

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
CLIENT_INFO = {"name": "irlpls", "version": "0.1"}
TIMEOUT_S = 60


class MCPError(RuntimeError):
    """A JSON-RPC error from the MCP server (code, message), or a response that is not MCP."""
    def __init__(self, msg, code=None, data=None):
        super().__init__(msg)
        self.code, self.data = code, data


class ToolError(MCPError):
    """The tool ran and reported a failure (result.isError). The text is provider data, never instructions."""


def rpc(method, params=None, id=1):
    """A JSON-RPC 2.0 request body (a notification when id is None)."""
    b = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        b["id"] = id
    if params is not None:
        b["params"] = params
    return b


def tools_call(name, arguments, id=2):
    return rpc("tools/call", {"name": name, "arguments": dict(arguments or {})}, id=id)


def initialize_body(id=1):
    return rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}, id=id)


HANDSHAKE_NOTE = ("MCP Streamable HTTP: first POST the same URL with " + json.dumps(initialize_body()) +
                  ", keep the Mcp-Session-Id response header if one is sent and send it on every later request, then POST "
                  '{"jsonrpc":"2.0","method":"notifications/initialized"}, then this body. The answer may be JSON or an '
                  "SSE stream whose data line carries the result. Any MCP client does all of this for you.")


def prepared_tool_call(card_id, operation, url, tool, arguments, *, key_name, get_key_url, docs_url, notes,
                       auth_kind="bearer", safe=None, placeholders=None):
    """A ready call for one MCP tool: the tools/call POST, bearer token named, never filled in."""
    headers = {"Authorization": "Bearer {" + key_name + "}", "Content-Type": "application/json", "Accept": ACCEPT,
               "MCP-Protocol-Version": PROTOCOL_VERSION}
    return prepared_call(card_id, operation, "POST", url, headers=headers, body=tools_call(tool, arguments),
                         auth_kind=auth_kind, key_name=key_name, key_in="header Authorization: Bearer", get_key_url=get_key_url,
                         docs_url=docs_url, notes=(notes + " " + HANDSHAKE_NOTE).strip(), safe_to_run_without_approval=safe,
                         placeholders=placeholders)


def raise_for_auth(card_id, e, hint):
    """401/403 become MissingCredentials (the token is missing, expired, revoked or lacks a scope); everything else
    (404, 429 with its Retry-After in e.headers, 5xx) propagates as http.HTTPError. The token is never in the message."""
    if isinstance(e, http.HTTPError) and e.status in (401, 403):
        raise MissingCredentials(f"{card_id}: the provider refused the token (HTTP {e.status}). {hint}".strip()) from None
    raise e


def retry_after(e):
    """Seconds from a 429/503 Retry-After header (delta-seconds form), or None."""
    h = {k.lower(): v for k, v in (getattr(e, "headers", None) or {}).items()}
    v = h.get("retry-after")
    try:
        return int(str(v).strip()) if v is not None else None
    except ValueError:
        return None


def _post(url, body, headers):
    """One POST. Returns (status, headers with lower-case keys, text). Raises http.HTTPError on 4xx/5xx.
    Goes through http.request(return_headers=True): https only, public hosts, checked redirects, size caps, and the
    provider's rate limit (the card id set by ratelimit.using, which Session sets when it is given a provider)."""
    r = http.request("POST", url, body=body, headers=dict(headers), timeout=TIMEOUT_S, raw=True, return_headers=True,
                     allow_http=False)
    return r.status, r.headers, r.body


def parse_response(text, content_type, want_id):
    """The JSON-RPC response with id want_id, from a JSON body or an SSE stream."""
    text = text or ""
    if "text/event-stream" in (content_type or ""):
        msgs, data = [], []
        for line in text.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line.strip() and data:
                try:
                    msgs.append(json.loads("\n".join(data)))
                except ValueError:
                    pass
                data = []
    else:
        try:
            v = json.loads(text) if text.strip() else None
        except ValueError:
            raise MCPError("the MCP server sent a body that is not JSON") from None
        msgs = v if isinstance(v, list) else [v]
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == want_id and ("result" in m or "error" in m):
            if "error" in m:
                err = m["error"] if isinstance(m["error"], dict) else {}
                raise MCPError(f"MCP error {err.get('code')}: {str(err.get('message'))[:300]}", err.get("code"), err.get("data"))
            return m["result"]
    raise MCPError(f"no JSON-RPC response with id {want_id} in the MCP answer")


def tool_result(result):
    """The useful part of a tools/call result: structuredContent if present, else the text content parsed as JSON when
    it is JSON, else the text. isError raises ToolError."""
    r = result if isinstance(result, dict) else {}
    texts = [c.get("text") for c in r.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
    if r.get("isError"):
        raise ToolError("the tool reported an error: " + " ".join(t for t in texts if t)[:300])
    if isinstance(r.get("structuredContent"), (dict, list)):
        return r["structuredContent"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (TypeError, ValueError):
            return texts[0]
    return texts or r


class Session:
    """A short-lived MCP session against one server, authenticated with a bearer token. Not thread-safe."""

    def __init__(self, url, token, send=None, provider=None):
        self.url, self._token, self._send, self.provider = url, token, send or _post, provider
        self.session_id, self.protocol_version, self._next = None, PROTOCOL_VERSION, 1

    def _headers(self):
        h = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json", "Accept": ACCEPT}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self._next > 1:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    def _paced(self):
        return ratelimit.using(self.provider) if self.provider and ratelimit.current() != self.provider else contextlib.nullcontext()

    def _request(self, method, params):
        rid, h = self._next, self._headers()
        self._next += 1
        with self._paced():
            status, headers, text = self._send(self.url, rpc(method, params, id=rid), h)
        return headers, parse_response(text, headers.get("content-type"), rid)

    def open(self):
        headers, res = self._request("initialize", initialize_body()["params"])
        self.session_id = headers.get("mcp-session-id") or None
        if isinstance(res, dict) and isinstance(res.get("protocolVersion"), str):
            self.protocol_version = res["protocolVersion"]
        with self._paced():
            self._send(self.url, rpc("notifications/initialized", id=None), self._headers())
        return res

    def call_tool(self, name, arguments=None):
        if self._next == 1:
            self.open()
        _, res = self._request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        return tool_result(res)


def call_tool(url, token, name, arguments=None, send=None, provider=None):
    """Open a session and call one tool; provider (a card id) paces every request under that provider's rate limit."""
    return Session(url, token, send=send, provider=provider).call_tool(name, arguments)
