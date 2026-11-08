# Contributing

The project has one maintainer for now; this describes how a change is made. Issues and pull requests are welcome, and a card correction from the provider itself is the most useful kind.

## Setup and the gate

Use `uv run`; the exact setup and gate commands are in `docs/DEVELOPING.md`. The core library has no dependencies. Node is needed for the matcher parity test.

## Recipes

### Add or correct a card

1. Copy `examples/card-template.json` to `cards/<id>.json` (`id` is lowercase letters, digits and hyphens, and equals the file name). The fields are explained in `examples/card-template.md`; the short path is in `docs/ADD-A-CARD.md`.
2. State only what a public source says. Every claim goes in `sources` as `{ "claim", "url" }`.
3. Guarantees: `evidenced` needs `method` and `evidence_url` anyone can check; `claimed` is the provider's word; `none` if nothing is public. Always fill `unknown`. `short`, `access` and `api` are needed to be listed.
4. Leave `status: "unclaimed"`. It becomes `provider-maintained` only when the provider serves the card from its own domain (`docs/PROVIDERS.md`). A provider that wants its card claimed, corrected or removed: `docs/ADD-A-CARD.md`, last section.
5. Set the card's family: add `"family": "<id>"` to the card, using one of the ids in the table in `docs/ADD-A-CARD.md` (the closest; if none fits, say so in the pull request). No code edit is needed. Then run the gate, which means exactly: `python3 tools/gate.py cards/<id>.json`, `python3 tools/check_cards.py` and `python3 build.py` (about two seconds, nothing to install; `uv run irlpls validate cards/<id>.json` is the same schema check). The full test suite takes 10+ minutes and needs a browser; CI runs it, you do not have to. You do not need to touch tests: a new card in an existing family does not change the matcher benchmark, and a bench row is needed only when you change matching itself. If a test fails after your card is added, paste the failing test name in the pull request and a maintainer will deal with it.
6. Add a line to `CHANGELOG.md`, and paste the gate output in the pull request.

A maintainer-side release check flags some words, such as a short personal name that also appears inside some real place names. Write the place name correctly; the gate does not run that check and a maintainer may reword it later. That is not a problem with your card.

`pricing.from_usd: 0` means "not published", not free. An absent right means "not stated", which counts as not granted.

### Add an adapter, change matching, add a field

The steps are in `docs/DEVELOPING.md`.

### Change the policy or broker

`irlpls/policy.py` and `irlpls/broker.py`. Keep the invariants in `docs/STANDARD.md` Section 8 (broker): policy is evaluated only against what cards state; a violating order is never sent; a commission stops at a quote unless a person confirms or a standing mandate covers it; an order needs an idempotency key.

## Rules that apply to every change

- No public hookups (publishing, outreach, spending) without the maintainer's approval.
- Copy shows controls and outcomes; it does not argue.
- Do not add a score, a ranking for money, or a verification claim irlpls makes itself.
