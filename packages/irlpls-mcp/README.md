# irlpls-mcp

The [irlpls](https://irlpls.com) MCP server. Your agent finds providers that collect new real-world observations
(people, drones, satellites, dashcams, sensors), reads their Capture Cards, and plans a request against your policy.
Inference-free: your agent's own model writes the need; irlpls matches it against the cards.

```bash
uvx irlpls-mcp --read-only
```

Claude Code:

```bash
claude mcp add irlpls -- uvx irlpls-mcp --read-only
```

Claude Desktop, Cursor and other clients (`mcpServers` in the client's JSON config):

```json
{"mcpServers": {"irlpls": {"command": "uvx", "args": ["irlpls-mcp", "--read-only"]}}}
```

## Modes

- `--read-only` (7 tools): `irlpls_request`, `irlpls_answer`, `irlpls_choose_provider`, `irlpls_get_job`,
  `irlpls_find_providers`, `irlpls_get_card`, `irlpls_list_capabilities`. Plans only: it never orders and makes no
  provider calls. Also `IRLPLS_MCP_READ_ONLY=1`.
- Default (15 tools): adds `irlpls_submit` and `irlpls_approve` (live reads now). Orders, approvals and cancellations stay refused until the operator sets `IRLPLS_ALLOW_SUBMIT=1` and an `IRLPLS_MAX_ORDER_USD` ceiling; even then a paid order needs the user's explicit approval and the six wire-protocol tools (`irlpls_discover`, `irlpls_quote`, `irlpls_order`,
  `irlpls_status`, `irlpls_deliver`, `irlpls_cancel`). Provider keys come from your environment; irlpls never sees
  them.

## Transports

stdio by default. `--http` (or `--transport streamable-http`) serves Streamable HTTP on `--host 127.0.0.1 --port 8765`,
path `/mcp`. It has no authentication, so a non-loopback `--host` also needs `--allow-remote`.

Part of [irlpls](https://github.com/irlpls/irlpls) (Apache-2.0). The library and CLI are `pip install irlpls`.

<!-- mcp-name: com.irlpls/irlpls -->
