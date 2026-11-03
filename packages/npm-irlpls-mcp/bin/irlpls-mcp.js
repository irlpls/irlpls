#!/usr/bin/env node
// npx irlpls-mcp [args]  ->  uvx irlpls-mcp==<version> [args]   (stdio passes straight through)
// The server is Python (PyPI: irlpls-mcp). This shim only starts it with uv's uvx, pinned to this package's version.
// IRLPLS_MCP_SPEC overrides what uvx runs (e.g. a local wheel path, for testing).
"use strict";
const { spawn } = require("child_process");
const version = require("../package.json").version;
const spec = process.env.IRLPLS_MCP_SPEC || `irlpls-mcp==${version}`;
const args = ["--from", spec, "irlpls-mcp", ...process.argv.slice(2)];
const child = spawn(process.env.UVX || "uvx", args, { stdio: "inherit" });
child.on("error", (e) => {
  if (e.code === "ENOENT") {
    process.stderr.write("irlpls-mcp: needs uv (https://docs.astral.sh/uv/). Install it, or run the server " +
      "directly: uvx irlpls-mcp, or pip install irlpls-mcp && irlpls-mcp\n");
    process.exit(127);
  }
  throw e;
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code, signal) => process.exit(signal ? 1 : code));
