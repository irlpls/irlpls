# Contributing

The project is private and has one maintainer; this describes how a change is made, not an open invitation.

## Setup and the gate

Use `uv run`; the exact setup and gate commands are in `docs/DEVELOPING.md`. The core library has no dependencies. Node is needed for the matcher parity test.

## Recipes

### Add or correct a card

1. Copy a similar file in `cards/` to `cards/<id>.json` (`id` is `^[a-z0-9-]+$`).
2. State only what a public source says. Every claim goes in `sources` as `{ "claim", "url" }`.
3. Guarantees: `evidenced` needs `method` and `evidence_url` anyone can check; `claimed` is the provider's word; `none` if nothing is public. Always fill `unknown`.
4. Leave `status: "unclaimed"`. It becomes `provider-maintained` only when the provider serves the card from its own domain (`docs/PROVIDERS.md`).
5. `uv run irlpls validate cards/<id>.json`, then rebuild (this refreshes the bundled snapshot) and run the gate. The matcher benchmark may need a new row.
6. Add a line to `CHANGELOG.md`.

`pricing.from_usd: 0` means "not published", not free. An absent right means "not stated", which counts as not granted.

### Add an adapter, change matching, add a field

The steps are in `docs/DEVELOPING.md`.

### Change the policy or broker

`irlpls/policy.py` and `irlpls/broker.py`. Keep the invariants in `docs/STANDARD.md` Section 8 (broker): policy is evaluated only against what cards state; a violating order is never sent; a commission stops at a quote unless a person confirms or a standing mandate covers it; an order needs an idempotency key.

## Rules that apply to every change

- No public hookups (publishing, outreach, spending) without the maintainer's approval.
- Copy shows controls and outcomes; it does not argue.
- Do not add a score, a ranking for money, or a verification claim irlpls makes itself.
