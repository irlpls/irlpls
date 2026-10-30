# Architecture

One page: what the pieces are and how a request moves through them.

## Shape

```
 need + why + policy                            provider's own domain
        |                                        /.well-known/irlpls  (a card)
        v                                                |
  need.py  parse  ------>  match.py  rank  <---  registry.py  loads cards
        |                      |                  (cards/, or bundled snapshot)
        v                      v
  broker.py: questions, plan, ruled_out  <--  policy.py  (checked against what cards state)
        |
        v  submit()
  adapters/<provider>.py  --live()-->        the provider's own API
                          --commission()-->  (dry run unless confirm + idempotency key)
        |
        v
  Job: results, each with the provider's proof levels

  Front doors onto the broker: Python (irlpls/__init__.py), CLI (cli.py), MCP server (mcp_server.py)
  Static side: build.py turns src/ and cards/ into site/ (finder, card pages, cards.json, standard, agent manual)
  Browser side: src/match.js is the same matcher, so a page or an agent can rank cards with no server
```

## Principles that shape the code

- **Metadata only.** irlpls hosts cards and evidence, never media, inference or money. Results and payment go between buyer and provider.
- **No verification of its own.** Providers state proof; independent verifiers attest on the card; irlpls shows who proved what. Seven guarantees, reported separately, no overall score.
- **Unstated means no.** A missing right, an unpublished price against a ceiling, or a guarantee below the required level is a policy violation, never a pass.
- **Fit is per request.** It is computed from card plus request and never stored on a provider.
- **Nothing is ordered by accident.** Live reads run at once; commissions stop at a quote unless a person confirms or a standing mandate (`auto_submit` with `max_order_usd`) covers the order; an order needs an idempotency key.

## Components

| Piece | Files | Role |
|---|---|---|
| Standard and schemas | `docs/STANDARD.md`, `schema/*.schema.json` | The contract. Versioned by date (2026-10-26); fields are only added within a version. |
| Cards | `cards/*.json` | The data. `unclaimed` (compiled, sourced) or `provider-maintained` (served from the provider's domain). |
| Registry | `irlpls/registry.py` | Loads cards from `$IRLPLS_CARDS`, the repo `cards/`, or the bundled snapshot `irlpls/data/cards.json` (written by `build.py`). |
| Parser | `irlpls/need.py`, `geo.py` | Turns a sentence into a Request: mode (live or commission), place, deadline, quantity, terms. No model is used. |
| Matcher | `irlpls/match.py` and `src/match.js` | Fit = 0.4 description + 0.4 guarantees + 0.2 coverage, times description squared. Two implementations kept equal by a parity test. |
| Policy | `irlpls/policy.py` | Evaluates a Policy against cards. |
| Broker | `irlpls/broker.py` | Builds the Job and runs submit and refresh. |
| Adapters | `irlpls/adapters/` | One class per provider: `live()`, `commission()`, `status()`. Registered by `card_id`. |
| Interfaces | `irlpls/__init__.py`, `cli.py`, `mcp_server.py` | Python, command line and MCP (7 `irlpls_*` tools; `--read-only` removes submit). |
| Validation | `irlpls/validate.py`, `irlpls/conformance.py` | Card validation, and a read-only check of a provider's live `/.well-known/irlpls`. |
| Site | `build.py`, `src/`, `tools/` | Static output in `site/` (not tracked). `tools/qa.py` is the visual quality floor. |

## Lifecycle of a Job

`clarifying` (a blocking question is open) to `proposed` (a ranked plan; nothing sent). Live: straight to `delivered`. Commission: `quoted`, then `submitted`, `in_flight`, and `delivered` or `failed`.

## Where to change what

| Change | Start here |
|---|---|
| A provider's facts | `cards/<id>.json`, then `python3 build.py` |
| A new provider you can call | `irlpls/adapters/` and its registration |
| How needs are ranked | `match.py` and `match.js` together, plus `tests/test_match_bench.py` |
| What a card can say | schema, `STANDARD.md`, `CHANGELOG.md`, `validate.py` |
| What an agent is told | `docs/AGENTS.md`, MCP server instructions in `mcp_server.py`, `docs/skill/` |
| The website | `src/`, `build.py`, then `tools/qa.py` |

See `CONTRIBUTING.md` and `docs/DEVELOPING.md` for the steps and `docs/PROVIDERS.md` for the provider side.
