# Rate limits and abuse notes

This file is about the hosted read-only MCP server only. Provider-side limits (USGS, NWS, OpenAQ and the rest, which
agents and the landing page's browser call directly) are on each card's `access.rate_limit` (docs/STANDARD.md Section
2.5) and enforced client-side by irlpls/ratelimit.py.

Updated 2026-10-28 against irlpls/mcp_server.py (14 tools) and mcp 2.3.0.

## What the hosted server is
- `deploy/serve.py --read-only --allow-remote`: 7 planning tools (`irlpls_find_providers`, `irlpls_get_card`,
  `irlpls_list_capabilities`, `irlpls_request`, `irlpls_answer`, `irlpls_choose_provider`, `irlpls_get_job`).
- Not registered: `irlpls_submit` and all six protocol tools (`irlpls_discover/quote/order/status/deliver/cancel`).
  Calling one returns an error (tested in smoke.sh). `irlpls_get_job` never refreshes from a provider in read-only mode.
- No outbound calls: serve.py disables the Nominatim geocoder. No credentials in the image. No money can move.
- State: jobs in memory, capped at MAX_JOBS = 500 (oldest dropped); lost on restart. No auth, no per-client limiter.

So the risks are availability and scraping, not spend.

## Limits
In the app (serve.py, tested to start with them on mcp 2.3.0):
- Body cap 64 KB (SDK default is 4 MB).
- At most 500 live sessions (SDK default 10,000); SDK idle timeout 30 min.

At the edge (the app has no per-IP limiter):
- Per IP: 60 req/min on POST /mcp, burst 20 (Cloudflare WAF rate rule). On Fly there is no per-IP rule; use the
  fly.toml concurrency limits (soft 25 / hard 50) and accept that.
- Per-IP limiting needs the edge to see the IP. That is the edge's own state, not an app log; on Cloudflare, keep
  logging and Logpush off (see RUNBOOK.md).
- One machine, 256 MB, restart on OOM. Idle-stop is fine: sessions are lost and clients re-initialize.
- No CORS headers: non-browser clients only.
- Logs: none from the app (RUNBOOK.md, "How the config enforces it"). Platform request logs off where the platform allows.
- Kill switch: `fly scale count 0`.

## Threats (see docs/STANDARD.md Section 12)
- Person targeting: the filter is an English regex speed bump; paraphrase bypasses it. A read-only plan
  creates no capture, but job text is attacker-controlled: it is never logged, and is returned to whoever holds
  the job id. Treat plan text as untrusted.
- Abuse line: commissioned captures can stalk or harass. Read-only hosting is the mitigation: ordering happens at the
  provider, under its own checks. Do NOT host `irlpls_submit` or `irlpls_order` without auth, per-order requester
  records and limits on repeat orders to one address. That would also end the "logs nothing" promise.
- Job ids are the only access control on `irlpls_get_job` / `irlpls_answer`. They are random; a guessed id would
  reveal another session's need text. Acceptable for read-only plans; revisit if jobs ever hold more.

## Known gap
The SDK's DNS-rebinding guard answers 421 for a non-localhost Host when bound to 127.0.0.1. The container binds
0.0.0.0 (with --allow-remote), so Host: irlpls.com is accepted. If it is ever bound to localhost behind a proxy,
allowed_hosts must be set in mcp_server.py.
