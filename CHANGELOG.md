# Changelog

irlpls versions are dates. Cards carry `spec_version`; each version's schemas are served at `/<version>/schemas/`. A published version is frozen: changes get a new date.

## Unreleased (reference library)

Adapter waves A1–A10: 61 of the 86 listed providers now have an adapter (22 need no key, 37 take a key or account, 2 are paid from the person's own wallet); 25 stay directory-only.

- **Ready calls per operation:** each plan item keeps `prepared_call` (the route's default step) and adds `prepared_calls` `{read, quote, order, status}` for every step the adapter builds. `schema/job.schema.json` and `docs/AGENTS.md` ("Ready calls") describe them.
- **Money:** a provider price is firm only in USD. EUR, KRW, GBP, credits and USDC are shown as `{amount, currency}` (job `quote.price`, shim offer `total` or `x-com.irlpls.indicative_price`), never converted, and fail every USD ceiling. One reader (`adapters.base.quote_price`) serves the broker, `irlpls.commission` and the shim.
- **Two-step orders:** Capella's cost estimate and GLOBHE's quotes surface as job `approvals`; `job.approve(approval_id, confirm=True, idempotency_key, total)`, MCP `irlpls_approve` and shim `POST /orders/{id}/approve` accept one only on an explicit per-order yes with the exact total, never under a standing mandate; `max_order_usd` still applies.
- **Shim:** cooperative cancel through `adapter.cancel()` when the adapter documents one (`protocol.cancel: true`; the order ends `failed / cancelled` only on the provider's word, a refusal is `409 not_cancellable`); a country code matches sub-national coverage (`US` covers a `US-GA` card).
- **Catalog:** new adapter state `sdk-callable-wallet` (Vangrid, Execution Market), never shown as keyless; wallet signing stays prepare-only.
- **Hygiene:** dry runs mask any header whose name contains key, auth, token, secret, client, password, session or cookie, and a test sweeps every adapter's ready calls and dry runs for credential values; `http.request(return_headers=True)` (the MCP adapters now use it, so they are rate limited); `_oauth.TokenCache(json=True)` replaces Truepic's private subclass.

## 2026-10-26

Wire protocol (still draft; additive and optional, so a card alone stays conformant):

- **Standard:** new Section 14, Protocol: six HTTPS+JSON operations (discover, quote, order, status, deliver, cancel) with the same shapes at a provider and at a router; `Irlpls-Version` header; `Idempotency-Key` required on order and cancel (replay returns the stored response, a different body is `422 idempotency_key_reused`); money as decimal strings; order states are the Job words, with `reason_code` on `failed` and cancel as `failed` + `cancelled`; deliveries carry provenance for the eight stages, guarantee by guarantee; detached-JWS ES256 receipts keyed from the card's `jwks_uri`; optional Ed25519 Standard Webhooks; RFC 9457 problems with the irlpls code registry; an MCP binding (`irlpls_discover` … `irlpls_cancel`); router and shim rules (a router never rewrites `order_url` and derives a per-provider idempotency key); security considerations; conformance classes Card-only provider, Orderable provider and Router. Sections 2.2 (`protocol` card member, including `sandbox`), 6.1, 8, 11 (the "no wire protocol" gap is closed) and 13 updated.
- **Schemas:** `schema/protocol/` (`common`, `quote`, `order`, `status`, `delivery`, `problem`), served at `/2026-10-26/schemas/protocol/`. Worked examples in `docs/protocol/PROTOCOL-DRAFT.md` are checked against them.
- **Tooling:** `python -m irlpls.conformance <domain> --protocol` checks an Orderable provider without side effects; with `--sandbox`, and only when the card declares `protocol.sandbox: true`, it also orders, replays the idempotency key, polls, reads the delivery and verifies receipts.

Spec-review fixes (still draft; additive or clarifying):

- **Standard:** fit is a per-request ranking aid, never a trust score; total proof is only a last tiebreak over required guarantees. Draft versus frozen defined (Section 10), with unknown-member and `x-` extension rules. Domain binding is now a MUST only for the new "domain-bound broker" class (the reference is not one yet). New: well-known URI registration text, Security and privacy considerations (12), References (13), BCP 14 wording, a "What a provider implements" box, formats-only scope note, `require_c2pa` and `min_assurance_level` semantics, `sample` cards never ordered, route follows a set `mode`, `time`/`location`/`content` always added.
- **Schemas:** the Request and Job `policy` `$ref` now points at the real Policy `$id`; `verification` allows exactly the seven keys; `evidence_url` must start `https://`; `pkce` is `S256`|`plain`; `delivery.basis` is required; date-time/date formats, lat/lon ranges, `country_code` pattern and `fit` bounds added.
- **Examples:** `docs/examples/minimal-card.json` (valid, v0.2) replaces the stale v0.1 samples, which are removed.


Replaces v0.1. The standard, in `docs/STANDARD.md`, now has a section on discovery, C2PA-backed evidence and capture receipts.

- **Namespace:** capabilities are named `com.irlpls.capture.*`, under the irlpls.com domain, as UCP requires of third-party capabilities.
- **Discovery:** a provider serves its own card at `/.well-known/irlpls`; a provider that runs UCP lists the capability in its profile.
- **Evidence:** `evidenced` is defined in C2PA terms. Guarantees can carry `standard`, `assurance_level`, `attestation_methods` and `conforming_product`.
- **Cards:** added `capability`, `expires`, `coverage.areas`, `api.mcp`, `payment`, `capture`, `requester`, `access.automation_allowed` and `access.gate`. `api.auth` is a list, and its `oauth` type is now `oauth2` with dynamic registration. `licensing.ai_training` and `resale` are `allowed` or `forbidden` (absent means not stated).
- **Policy:** `require_c2pa`, `min_assurance_level`, `allow_person_targeting`. A request that targets a specific person never goes to people-based providers by default.
- **Jobs:** an idempotency key is required to place an order; `continue_url` says where a person finishes what an agent can't; `terms` is a new reason code.
- **Results:** `observed_at` is now `phenomenon_time`, with `result_time`, `retrieved_at`, `sensor` and optional signed `receipt`.
- **Directory:** 10 new cards (Copernicus Sentinel, UP42, Globhe, GetterDone, Kled AI, Airtasker, UK Environment Agency flood data, PEGELONLINE, Fintraffic weathercams, ALERTCalifornia) and sourced corrections to RentAHuman, Toloka, Zeitview, Storesight and Mapillary.
- **Later additions (still 2026-10-26, all optional):** `capture.consent`, `capture.worker_pay`, `capabilities.capture_types`, `delivery.formats`, `providers` with STAC roles, `licensing.terms`, `api.openapi_url`.
- **Rate limits (still 2026-10-26, optional):** `access.rate_limit` states a provider's published limit (`requests`/`per_seconds`, `scope`, `burst`, `batch_max`, `cache_seconds`, `with_key`, `source_url`); Section 2.5 says how a broker paces, retries, caches and batches under it. The reference library does this per provider and reports what a limit leaves queued as a `rate_limited` warning.
- **Hardening (adversarial review):** NaN, infinite, negative, boolean and string money values never pass a price or order ceiling (policy, request budget, quoted totals), and a quote is validated before any order is confirmed. The broker keeps a per-job idempotency ledger (memory and `jobs/<id>.orders.json`): a replayed key or a second submit of an ordered job never orders twice; keys must be 1-255 printable ASCII characters. Expired cards are skipped (`expires`). `licensing: null` is read as not stated. Policy values are type-checked. Person-targeting detection normalises Unicode (NFKC, zero-width and look-alike letters), covers more phrasings and the `why`, Request fields and order spec, `answer()` cannot clear it, and 'track her package' no longer trips it. Absurd numbers in `parse()` are capped.
- **Tooling:** `irlpls validate` checks a card file or URL; `tools/migrate_v02.py` migrates v0.1 cards.

## v0.1 (2026-10-24)

First draft: Capture Cards with seven guarantees, Request, Policy and Job, the matcher, and the reference library.
