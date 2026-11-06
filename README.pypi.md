# irlpls

Find and call providers that collect new real-world observations: people, drones, satellites, dashcams, sensors.
Each provider is described by a **Capture Card** (what it captures, where, how fast, at what price, under what
rights, and what it can prove). irlpls plans against the cards, enforces your policy and calls the provider's own
API with your credentials. It captures nothing, sells no data and stays neutral: nothing buys rank.

```bash
pip install irlpls
```

```python
import irlpls

job = irlpls.request("river level at Comfort, TX now",
                     why="deciding whether to go tubing",
                     policy={"max_order_usd": 0})   # free reads only
job.best()["card_id"]   # 'usgs-water'
job.plan                # ranked providers: route, fit, gaps, proof, policy violations
job.ruled_out           # everyone else, with a reason
job.submit()            # live routes run now; made-to-order stops at a quote unless confirm=True
job.results             # observations, each with the provider's proof levels
```

The command line does the same:

```bash
irlpls request "river level at Comfort, TX now"
irlpls find "storefront photo in Austin" --limit 5
irlpls show usgs-water
irlpls validate my-card.json
```

The library is stdlib only. Extras: `irlpls[protocol]` (signature checks, `cryptography`), `irlpls[check]`
(schema checks, `jsonschema`).

## For agents (MCP)

The MCP server is a separate package, so any MCP client can start it with `uvx`:

```bash
uvx irlpls-mcp --read-only     # plan only: never orders, never calls a provider
uvx irlpls-mcp                 # full: also runs live reads; orders only if you set IRLPLS_ALLOW_SUBMIT=1 and a ceiling, and then only after your explicit approval
```

Setup for Claude, Cursor and other clients: https://irlpls.com/agents

## Data in the package

A snapshot of the Capture Cards (`irlpls/data/cards.json`), the JSON Schemas, the Public Suffix List (MPL-2.0) and
an offline city gazetteer from GeoNames (CC BY 4.0). Set `IRLPLS_CARDS` to a path or URL to use a newer registry.
Card data is licensed as stated in `LICENSE-DATA.md`; code is Apache-2.0.

Standard, provider guide and agent manual: https://irlpls.com
