# irlpls

An open protocol and benchmark for new, verifiable observations of the physical world.

One function across providers (people, drones, satellites, dashcams, sensors). Each provider's rights and proof are stated on its **Capture Card**. You buy direct. irlpls captures nothing, sells no data and takes no cut.

Private preview. Standard version 2026-10-26.

## What is in here

| You want to | Look at |
|---|---|
| Read the standard | `docs/STANDARD.md` |
| See every provider | `cards/` (one JSON file each), validated by `schema/capture-card.schema.json` |
| Have an agent use it | `docs/AGENTS.md` (the agent manual) |
| Publish your own card | `docs/PROVIDERS.md` |
| Understand the pieces | `docs/ARCHITECTURE.md` |
| Change the code | `CONTRIBUTING.md`, `docs/DEVELOPING.md` |

## Try it (no keys)

From a checkout of the repo (`uv run` supplies the environment):

```bash
uv run python examples/live_answer.py     # "is the Guadalupe at Comfort, TX good for tubing today", free USGS reads
uv run python examples/commission.py      # 500 first-person door clips; dry run, orders nothing
uv run python examples/active_learning.py # a training loop orders its hard cases under a $50 mandate; dry run
```

Output from real runs is in `examples/README.md`.

## The idea in four objects

- **Capture Card**: what a provider captures, where, how fast, at what price, under what rights, how to call it, and what it can prove. Proof is shown as eight provenance stages (Real person, Captured locally, Genuine device, Sealed capture, Known timestamp, Tracked in transit, Independently verified, Delivered to you), each Evidenced, Claimed, Reported (a time only) or Not stated. They are read from the card's seven guarantee fields (human, application, camera, time, location, content, corroboration; `evidenced`, `claimed` or `none` in the JSON), each with a line on what is still uncertain. There is no overall score.
- **Request**: a need in plain language, with the place and time in it, and why.
- **Policy**: your rules, set once: required proof, price ceilings, AI-training rights, allow and deny lists, a standing budget. An unstated right or unpublished price counts as a violation. A violating order is never sent.
- **Job**: the answer. A ranked plan, every other provider with the reason it was ruled out, and results with each provider's proof attached.

```python
import irlpls

job = irlpls.request("river level at Comfort, TX now",
                     why="deciding whether to go tubing",
                     policy={"max_order_usd": 0})   # free reads only
job.plan       # ranked providers: route, fit, gaps, proof, policy violations
job.ruled_out  # everyone else, with a reason
job.submit()   # live routes run now; commissions stop at a quote unless confirm=True
job.results    # observations, each with the provider's proof levels
```

## Command line

```bash
uv run irlpls find "air quality near Fresno, CA right now"
uv run irlpls request "is the Guadalupe at Comfort, TX good for tubing right now" --submit
uv run irlpls show usgs-water
uv run irlpls validate cards/skyfi.json
uv run irlpls adapters
```

## MCP server

`uv run irlpls-mcp` serves nine broker tools: `irlpls_request`, `irlpls_answer`, `irlpls_choose_provider`, `irlpls_submit`, `irlpls_approve`, `irlpls_get_job`, `irlpls_find_providers`, `irlpls_get_card`, `irlpls_list_capabilities`, plus the six wire-protocol tools. `--read-only` leaves out every tool that orders or calls a provider (`irlpls_submit`, `irlpls_approve`, the protocol tools), so nothing is ever ordered. Needs the `mcp` package (version 2 for the tests).

## Providers with an adapter

61 of the 86 listed providers have an adapter: 22 need no key, 37 take your key or account, and 2 are paid from your own wallet (`vangrid`, `execution-market`: the SDK prepares the call, your wallet signs it). The other 25 are directory-only. All 61 also return ready calls (`plan[i].prepared_call`, and `prepared_calls` per operation: read, quote, order, status) with your key named, never filled in. Made-to-order routes are a dry run unless you confirm; a price is firm only in USD (EUR, KRW, GBP, credits and USDC stay indicative, in their own currency). `irlpls adapters` prints the live list.

The remaining cards are listed for comparison; their `api` block says how to reach the provider.

## Tests

The exact gate commands are in `docs/DEVELOPING.md`. The full suite needs `jsonschema`, `playwright` (with a browser), `mcp>=2` and node; tests that need a missing tool skip.

## What irlpls will not do

Capture anything, sell data, take a cut, verify anything itself, search archives, or run a model. See `GOVERNANCE.md`.

## Licence

Code: Apache-2.0. Card data licence: not yet decided.
