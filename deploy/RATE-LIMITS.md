# Rate limits and abuse notes

This file is about the hosted read-only MCP server only. Provider-side limits (USGS, NWS, OpenAQ and the rest, which
agents and the landing page's browser call directly) are on each card's `access.rate_limit` (docs/STANDARD.md Section
2.5) and enforced client-side by irlpls/ratelimit.py.

Updated 2026-11-02 against irlpls/mcp_server.py (15 tools full, 7 read-only) and mcp 2.3.0.

## What the hosted server is
- `deploy/serve.py --read-only --allow-remote`: 7 planning tools (`irlpls_find_providers`, `irlpls_get_card`,
  `irlpls_list_capabilities`, `irlpls_request`, `irlpls_answer`, `irlpls_choose_provider`, `irlpls_get_job`).
- Not registered: `irlpls_submit` and all six protocol tools (`irlpls_discover/quote/order/status/deliver/cancel`).
  Calling one returns an error (tested in smoke.sh). `irlpls_get_job` never refreshes from a provider in read-only mode.
- No outbound calls: serve.py swaps the Nominatim geocoder for the bundled offline gazetteer (irlpls/places.py). No credentials in the image. No money can move.
- State: jobs in memory, capped at MAX_JOBS = 500 (oldest dropped); lost on restart. No auth. Rate limited in the app (below).

So the risks are availability and scraping, not spend.

## Limits
In the app (deploy/serve.py, tested by deploy/mcp_smoke.py and tests/test_packaging.py):
- Per client: 60 requests/min, burst 20 (token bucket). Whole server: 600/min. Over the limit: HTTP 429 with a
  JSON-RPC error body and `Retry-After`. GET /healthz is exempt. Tunable: IRLPLS_RATE_PER_MIN, IRLPLS_RATE_BURST,
  IRLPLS_RATE_GLOBAL_PER_MIN.
- The client key is the `Fly-Client-IP` header (IRLPLS_CLIENT_IP_HEADER, set in fly.toml and the Dockerfile), else
  the TCP peer. Buckets live in memory (at most 10,000 keys), are never logged or written, and vanish on restart.
- Body cap 64 KB (SDK default is 4 MB). At most 500 live sessions (SDK default 10,000); SDK idle timeout 30 min.
- Fly proxy: concurrency soft 25 / hard 50 requests per machine (fly.toml).
- One machine, 256 MB, restart on OOM. Idle-stop is fine: sessions are lost and clients re-initialize.
- No CORS headers: non-browser clients only.
- Logs: none from the app (RUNBOOK.md). Kill switch: `fly scale count 0 -a irlpls-mcp`.

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
0.0.0.0 (with --allow-remote), so Host: mcp.irlpls.com is accepted. If it is ever bound to localhost behind a proxy,
allowed_hosts must be set in mcp_server.py.
