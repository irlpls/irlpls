# Quickstart: try irlpls in ten minutes

No hosted service, no key, no install. Everything runs from a checkout of this repo with `uv run`. Output below is from real runs on 2026-10-27 (step 6 on 2026-10-28); live readings change. Steps marked **network** call a free public API (no key). Nothing here spends anything: step 6 orders only from a simulator on your own machine.

All commands run from the repo root.

## 1. Rank providers for a need (offline)

```bash
uv run irlpls find "river level at Comfort, TX now" --limit 5
```

```
live within 60 min · qty 1 · proof: time, location, content (default)

 FIT  PROVIDER                     DELIVERY             FROM                   PROOF   SDK
  73  USGS stream gauges           live · minutes       free                   ····◐◐◐ live
      ! no proof of time; self-reported location, content
  36  NOAA tide and current statio live · minutes       free                   ···◐◐◐· live

proof columns: human app camera time place content cross-check (● evidenced ◐ claimed · none). Fit is for this request only.
```

Fit is for this request only. The seven proof columns are separate guarantees (`●` evidenced, `◐` claimed, `·` none); there is no overall score. Add `--json` for machine output.

## 2. A policy that rules providers out, with reasons (offline)

`examples/quickstart_policy.json`:

```json
{"require": {"time": "evidenced"}, "need_ai_training": true, "deny": ["noaa-tides"]}
```

```bash
uv run irlpls find "river level at Comfort, TX now" --limit 4 --policy examples/quickstart_policy.json
```

```
  73  USGS stream gauges           live · minutes       free                   ····◐◐◐ live
      ! no proof of time; self-reported location, content
      ✕ policy: time must be evidenced, card says none; AI training rights not stated
  36  NOAA tide and current statio live · minutes       free                   ···◐◐◐· live
      ✕ policy: on the deny list; time must be evidenced, card says claimed; AI training rights not stated
```

An unstated right counts as a violation, never a pass. Add `--strict` to drop violators; here that leaves nothing (`[]` with `--json`), which is the honest answer. A violating order is never sent. Fields are in `irlpls/policy.py` and `docs/AGENTS.md` section 7.

## 3. A live keyless reading (network)

```bash
uv run irlpls request "river level at Comfort, TX now" --submit
```

```
job 653308adbf7e · delivered
 →  73 USGS stream gauges             live       callable                 time:none location:claimed content:claimed
    36 NOAA tide and current stations live       callable                 time:claimed location:claimed content:claimed
  2026-10-27T11:20:00.000-05:00  discharge: 76.3 ft3/s  (USGS-08167000, 0.8 km)
  2026-10-27T11:20:00.000-05:00  gage height: 3.26 ft  (USGS-08167000, 0.8 km)
  2026-10-27T11:20:00.000-05:00  gage height: 5.16 ft  (USGS-08166700, 8.2 km)
```

The adapter alone: `uv run irlpls live usgs-water --at "Comfort, TX"`. Which adapters need keys: `uv run irlpls adapters`.

## 4. Wire the MCP server into Claude Desktop or Claude Code

The MCP server is `irlpls-mcp` (stdio). Verified here with `uv run --with mcp irlpls-mcp --read-only`: it reports server `irlpls` and lists `irlpls_request`, `irlpls_answer`, `irlpls_choose_provider`, `irlpls_get_job`, `irlpls_find_providers`, `irlpls_get_card`. `--read-only` plans only and leaves out `irlpls_submit`; drop it to let the agent run live reads.

Use an absolute path to your checkout (`/path/to/irlpls`).

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "irlpls": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/irlpls", "--with", "mcp", "irlpls-mcp", "--read-only"]
    }
  }
}
```

Claude Code, in a project's `.mcp.json` (same shape), or:

```bash
claude mcp add irlpls -- uv run --project /path/to/irlpls --with mcp irlpls-mcp --read-only
```

Then ask: "Use irlpls to find who can report the river level at Comfort, TX now." (The Claude Code line was not run here; the stdio server was.)

## 5. A 40-line agent loop: ask, check policy, report proof as stated (network)

`examples/quickstart_agent.py` asks, applies a free-only policy, runs the live route and reports the proof levels the card states, never a claim that irlpls verified anything.

```bash
uv run python examples/quickstart_agent.py "river level at Comfort, TX now"
```

A real run (2026-10-29):

```
proposed: 2 option(s), 84 ruled out
  ruled out Hub'Eau Hydrométrie (France river levels and flow): outside_coverage
  ruled out PEGELONLINE (German Federal Waterways): outside_coverage
  ruled out Environment Agency real-time flood-monitoring API: outside_coverage
choice: USGS stream gauges (live)

gage height: 3.29 ft at 2026-10-29T06:20:00Z from USGS stream gauges
proof as the provider states it: time none, location claimed, content claimed
irlpls verified nothing; any advice from this number is my own reading.
```

## 6. Run a provider locally and order from it (offline)

The reference provider speaks the wire protocol (`docs/STANDARD.md` Section 14) on your machine. `--sim` fakes the capture: it never sends anyone or charges anything, and delivers a generated photo after five seconds. In one terminal:

```bash
uv run --with cryptography --with jsonschema python -m irlpls.protocol.server --port 8811 --sim
```

```
irlpls reference provider on http://127.0.0.1:8811  (card: /.well-known/irlpls, endpoint http://127.0.0.1:8811/irlpls)
```

Its card has the `protocol` block that makes a provider orderable:

```bash
curl -s http://127.0.0.1:8811/.well-known/irlpls | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['protocol'], indent=1))"
```

```
{
 "versions": [
  "2026-10-26"
 ],
 "endpoint": "http://127.0.0.1:8811/irlpls",
 "quote": "optional",
 "public_quotes": false,
 "webhooks": true,
 "cancel": true,
 "jwks_uri": "http://127.0.0.1:8811/.well-known/irlpls-keys.json"
}
```

In a second terminal, save this as `order_local.py`. It discovers, quotes, orders with an idempotency key, retries with the same key, waits, reads the delivery and verifies the receipt against the provider's keys:

```python
from irlpls.protocol.client import Client, discover, money, new_key, new_nonce

local = dict(allow_http=True, allow_private=True)  # localhost only; real providers are https
card = discover("http://127.0.0.1:8811", **local)
c = Client.from_card(card, **local)

q = c.quote({"need": "photo of the storefront today", "place": "123 Main St, Springfield, IL"},
            {"max_total": money(40)})
offer = q["offers"][0]
print("offer:", offer["total"]["amount"], offer["total"]["currency"], "firm" if offer["firm"] else "indicative")

# Only after your person approves that total:
key, nonce = new_key(), new_nonce()
req = {"quote_id": q["id"], "offer_id": offer["id"], "nonce": nonce}
order = c.order(req, idempotency_key=key)
print("order:", order["id"], order["state"])
print("retry, same key:", c.order(req, idempotency_key=key)["id"])  # the same order, never a second one

order = c.wait(order["id"], timeout=30)
print("status:", order["state"])
d = c.delivery(order["id"])
for r in c.verify_delivery(d, nonce=nonce, request_sha256=q["request_sha256"]):
    print("receipt:", r["item_id"], r["verified"], "-", r["reason"])
```

```bash
uv run --with cryptography python order_local.py
```

```
offer: 12.00 USD firm
order: ord_KlDjtTP0qllc_hDd submitted
retry, same key: ord_KlDjtTP0qllc_hDd
status: delivered
receipt: itm_1 True - signature verifies with the provider's key; bindings match
```

`allow_http` and `allow_private` exist only for a local run; against a real provider the client refuses plain HTTP and private addresses. The MCP server exposes the same steps as `irlpls_discover`, `irlpls_quote`, `irlpls_order`, `irlpls_status`, `irlpls_deliver` and `irlpls_cancel` (not with `--read-only`).

## Next

- Commissions (a person, drone or satellite captures to order) stop at a quote unless you pass confirm and an idempotency key: `uv run python examples/commission.py` (dry run).
- Rules for agents: `docs/AGENTS.md`. Publish a card, and make it orderable: `docs/PROVIDERS.md`.
