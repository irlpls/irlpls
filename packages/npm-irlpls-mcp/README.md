# irlpls-mcp (npx shim)

Starts the [irlpls](https://irlpls.com) MCP server, which is a Python package, through
[uv](https://docs.astral.sh/uv/)'s `uvx`. Same arguments as the server.

```bash
npx -y irlpls-mcp --read-only
```

Equivalent, without Node: `uvx irlpls-mcp --read-only`. Needs `uv` on PATH.

<!-- mcp-name: com.irlpls/irlpls -->
