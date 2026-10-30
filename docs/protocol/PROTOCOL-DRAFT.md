# irlpls wire protocol: draft

Status: **draft, private.** STANDARD §14 is the normative text; this document is the background, the worked examples and the open questions (informative). Where they differ, STANDARD §14 and the schemas govern. Version: `2026-10-26` (still draft). Schemas: `schema/protocol/*.schema.json`. Every JSON example below is checked against them by `uv run --with jsonschema --with cryptography python docs/protocol/check_examples.py`, which also recomputes every `request_sha256` and verifies the example receipt with the reference verifier.

## TL;DR

- **Six operations over HTTPS+JSON:** discover, quote, order, status, deliver, plus cancel. MCP tools map to them one to one (`irlpls_quote`, and so on). That is the whole protocol.
- **Same shape at both hops.** A provider speaks it. A router speaks it too: its quote returns several offers instead of one. An agent can talk to a provider directly with the same client code and no irlpls software.
- **Order states are the Job words** (`submitted`, `in_flight`, `delivered`, `failed` plus `reason_code`), so routers no longer need a status map per provider. Orders need an `Idempotency-Key` header and a `nonce`. Async follows HTTP: `202` + `Location` + `Retry-After`, then polling, with optional thin signed webhooks. A problem from `POST /orders` means no order exists; once one does, every failure is `state: failed` (§6).
- **One request hash, end to end.** The provider hashes the request it priced (`request_sha256`), echoes it in the quote and the Order, and signs it into every receipt. The exact address goes only to the chosen provider: router quotes are coarse, and the client re-quotes at the provider before ordering (§10).
- **Every delivery carries provenance stage by stage** (the 8 stages, each mapped to the 7 guarantees, never merged). Each item can carry a detached-JWS receipt bound to the buyer's nonce, the request hash, the media and the item's time and place (STANDARD 6.1, 14.7). Verification is deterministic: one `iss` form, ES256 or EdDSA taken from the key's JWK, keys marked for receipts only. The example receipt below is a real ES256 signature that the check script verifies.
- **Errors are RFC 9457 problems** using the irlpls codes the broker already emits (`outside_coverage`, `policy:<rule>`, `terms`, `needs_account` with `continue_url`, `person_targeting_refused`, `quote_expired`, …). Auth is the provider's own: OAuth 2 with Client ID Metadata Documents preferred (dynamic registration is deprecated by the MCP spec of 2026-07-28), API key allowed. Versions are dates in an `Irlpls-Version` header. Discovery is a `protocol` block in the `/.well-known/irlpls` card.
- **Adoption path: shims first.** Each existing adapter becomes an in-process shim that speaks the protocol in front of a proprietary API. Providers adopt later, or never; nothing breaks either way.

## 1. Go / no-go: does this warrant a protocol?

**Against.**
- No provider asked for one. Of 86 listed providers, none publishes UCP, A2A or STAPI. Every orderable one runs its own proprietary REST API, and two of them (SkyFi, RentAHuman) run OAuth MCP servers (`research/protocols/a2a.md`, `mcp.md`, `ucp/08`).
- Adapters already work. There are 23 in the reference library, and the broker's Job lifecycle covers quote, order, status and delivery today (`irlpls/broker.py`).
- A protocol nobody implements is extra spec text to maintain. It also invites the "is this a startup?" reading the project has steered away from.
- STAPI exists for tasking. Inventing beside it risks the XKCD-927 problem (one more competing standard).

**For.**
- **The reviewers' top gap is this one.** The IETF review's blocker B3 says it is "a data-format spec, not yet an interoperable protocol: no transport, endpoints or error model". The implementer review's change 4 calls an optional provider order contract the thing that "most reduces time to first working card". STANDARD §11 lists the same gap itself: "the provider-side order contract (idempotency header, status words, quote shape). Today each provider needs an adapter."
- **Adapters don't scale and can't be checked.** Every provider passes the idempotency key differently (Bee Maps in a query parameter, ProxyPics in `external_id`, SkyFi not at all), maps status differently, and shapes quotes differently. The broker probes six key names to find a total (`_quoted_total`). The adversarial tests found NaN totals passing budget checks (BUG-3/4/5). A typed `money` string removes that whole class of bug.
- **Nothing existing covers the loop for people-based capture with proof.** STAPI is satellite-shaped (geometry, datetime, CQL2). It leaves delivery "very provider specific" and has no cancel, no receipts and no production deployment. A2A and MCP Tasks give async states but no proof. UCP and ACP give idempotent checkout but no brief and no deliverable (`ORDER-LIFECYCLE.md`).
- **Agents calling providers directly** (AGENTS.md: "there is no hosted irlpls API") currently need custom code per provider. With one shape, one client works with any compliant provider.
- **Receipts (6.1) and per-stage provenance need a place on the wire.** Without a delivery message, the 8-stage chain is only a web page.

**Adopt STAPI wholesale?** No. It covers 2 of the 6 operations, is v0.1 with no known production use, and is built around satellites. **Borrow its shapes:** the opportunity becomes the quote, `status_history`, and `reason_code`. Map to it in a STAPI profile (§10) so satellite providers that adopt STAPI can be shimmed cheaply.

**Recommendation: go, minimal and shim-first.** Specify it as an *optional* conformance tier ("Orderable provider") on top of the card. Publishing a card alone stays fully conformant. Implement it first as the interface of the reference adapters and the MCP server, so it earns its keep with zero provider adoption. Ask a provider to adopt only after a pilot. ProxyPics or RentAHuman are the obvious candidates. If a pilot finds nobody wants it, the shim interface is still a cleaner internal API, so the work isn't wasted.

## 2. Roles, scope and conformance

| Role | Speaks the protocol as | Notes |
|---|---|---|
| **Provider** | server | Captures or reports. Its card's `protocol.endpoint` points at its base URL. |
| **Router** (a broker) | server *and* client | Plans against cards and applies the Policy. Its quote fans out to providers. It hands back offers whose `order_url` is the provider's own. |
| **Shim** | server | Puts the protocol in front of a proprietary provider API (§11). Runs in-process (reference library), at the provider, or at a third party (disclosed). |
| **Client** | client | An agent, or a router acting for one. |

**Normative** (MUST/SHOULD apply): §3 discovery block; §4 endpoints and common rules (version, idempotency, auth); §5 message shapes as fixed by `schema/protocol/*`; §6 state machine; §7 webhook verification rules; §8 problem format and code registry; §9 MCP binding names and argument shapes; §12 security requirements.
**Informative:** §1, §10 (router behaviour beyond the MUSTs listed there), §11 (shim pattern), all examples, the STAPI/UCP/A2A mappings, and the open questions.

**Conformance classes (proposed additions to STANDARD §8):**
- **Orderable provider:** a Card Publisher whose card has a `protocol` block, which implements discover, quote, order, status and deliver, and implements cancel when `protocol.cancel` is true.
- **Router:** a Broker that also implements the protocol on both sides and follows §10.

A Provider never receives a Policy. The Policy stays with the router or agent, which sends only `constraints` (§5.1).

## 3. Discovery

`discover` is the existing STANDARD §2.4 fetch: `GET https://<provider-domain>/.well-known/irlpls`. A card that speaks the protocol adds one member, `protocol` (schema: `common.json#/$defs/protocol_block`):

<!-- validate: common.json#/$defs/protocol_block @/protocol -->
```json
{
  "id": "streetlens",
  "provider_url": "https://streetlens.example",
  "status": "provider-maintained",
  "api": {"auth": [{"type": "oauth2", "resource_metadata": "https://api.streetlens.example/.well-known/oauth-protected-resource",
                    "cimd": true, "pkce": "S256",
                    "scopes": ["com.irlpls.capture:quote", "com.irlpls.capture:order"]}]},
  "protocol": {
    "versions": ["2026-10-26"],
    "endpoint": "https://api.streetlens.example/irlpls",
    "mcp": "https://api.streetlens.example/mcp",
    "jwks_uri": "https://streetlens.example/.well-known/irlpls-keys.json",
    "quote": "required",
    "public_quotes": false,
    "webhooks": true,
    "cancel": true
  }
}
```
(This is an excerpt; only `protocol` is validated here.)

Rules:
- `endpoint` MUST be HTTPS, with no trailing slash, on the same registrable domain as `provider_url`. "Registrable domain" is the Public Suffix List's (ICANN and PRIVATE sections): `api.example.co.uk` and `example.co.uk` match; `alice.github.io` and `bob.github.io` do not. Hosts that are equal always match; a bare public suffix never matches another host. The reference code vendors a dated PSL snapshot (`irlpls/psl.py`). A client MUST NOT use a protocol endpoint from a card that was not fetched from its provider's domain unless its user accepts the card as `unclaimed` (STANDARD 2.4). For a shim, `operator` names who runs it.
- `jwks_uri` MUST be same-origin with `provider_url`, and is REQUIRED when `webhooks` is true or the provider signs receipts. It serves a JWK Set (`common.json#/$defs/jwk_set`): every key has `kid` (unique in the set), `alg` (`ES256` or `EdDSA`) and the irlpls member `irlpls_use` (`receipt` or `webhook`); a retired key carries `not_after` (an RFC 3339 date-time) and is never deleted. A provider that runs UCP publishes the same keys at a JWK Set URL; `keys[]` inside `/.well-known/ucp` is not a JWK Set URL and is not used.
- A shim's own keys go in `operator.jwks_uri`, same-origin with `operator.url`; the card keeps the provider's `provider_url` and has no provider `jwks_uri`.
- Before ordering, a client refetches the card (honouring `Cache-Control: max-age`) and asks the person again if `endpoint`, `jwks_uri` or `operator` changed since the quote (STANDARD §12 rug-pull rule).
- Discover over MCP is `irlpls_discover`, which returns the card.
- A router's `/.well-known/irlpls` is `{"cards":[...]}` (the marketplace form). Cards a router serves for providers that don't speak the protocol stay `unclaimed`. When the router runs a shim for one, `protocol.operator` names the router.

## 4. Endpoints

| Op | HTTP | Success | MCP tool | Side effects |
|---|---|---|---|---|
| discover | `GET /.well-known/irlpls` (on the provider domain) | 200 card | `irlpls_discover` | none |
| quote | `POST {endpoint}/quotes` | 201 Quote, `Location: {endpoint}/quotes/{id}` | `irlpls_quote` | none (no money, no dispatch) |
| — | `GET {endpoint}/quotes/{id}` | 200 Quote | (via `irlpls_quote` with `quote_id`) | none |
| order | `POST {endpoint}/orders` + `Idempotency-Key` | 202 Order (`Location`, `Retry-After`); 200 Order if already `delivered` (live) | `irlpls_order` | **may cost money, may dispatch a person** |
| (find) | `GET {endpoint}/orders?client_reference=<ref>` | 200 `{orders: [...]}` (`status.json#/$defs/order_list`) | — | none |
| status | `GET {endpoint}/orders/{id}` | 200 Order (`Retry-After` while not terminal) | `irlpls_status` | none |
| deliver | `GET {endpoint}/orders/{id}/delivery` | 200 Delivery (also while `in_flight`, with `complete:false`) | `irlpls_deliver` | none |
| cancel | `POST {endpoint}/orders/{id}/cancel` + `Idempotency-Key` | 200 Order (cooperative: the state may still be `in_flight`) | `irlpls_cancel` | may stop work; refund per the provider's terms |

**Common rules (normative).**
- **Transport:** HTTPS only (TLS 1.2 or later, 1.3 SHOULD). `Content-Type: application/json` for requests and successful responses, `application/problem+json` for errors. Request bodies carry a `Content-Length` (a `Transfer-Encoding` body gets `411 length_required`), at most the server's limit (`413 payload_too_large`); numbers are finite and integers lie within ±2^53, or the body is `400 invalid_request`.
- **Version:** clients MUST send `Irlpls-Version: 2026-10-26` on every request, and every response, problems included, echoes the version used in the same header. A request without the header is answered under the newest version the server serves. A version not in `protocol.versions` gets `400 unsupported_version` with `supported_versions`. Versions are dates, and are draft or frozen exactly as in STANDARD §10. Unknown members MUST be ignored, unknown enum values MUST be treated as unsupported, and extensions use `x-<reverse-dns>` members.
- **Idempotency** (draft-ietf-httpapi-idempotency-key-header-08, as UCP and ACP use it): `POST /orders` and `POST /orders/{id}/cancel` MUST carry `Idempotency-Key` (1–255 printable ASCII; clients SHOULD use ≥128 random bits). `POST /quotes` MAY carry one. The server keeps keys for at least 24 h, in one key space per authenticated principal (the account or token subject the credential acts for), across all operations. A request's fingerprint is its method, its path and the RFC 8785 form of its body; an absent body counts as `{}` (a cancel body is `{}` or `{reason}`).
  - same key, same fingerprint: the stored response (status, headers, body) with `Idempotent-Replayed: true`, never a second order;
  - missing key: `400 idempotency_key_required`;
  - same key, different fingerprint (another body, method or path): `422 idempotency_key_reused`;
  - same key while the first call is still running: `409 request_in_progress` with `Retry-After`.

  The server stores 2xx and 4xx responses, except 401, 402, 403, 409 and 429 (the client may fix those and retry with the same key); it never stores a 5xx. After the window, a client finds an order again with `GET {endpoint}/orders?client_reference=<ref>`: this client's orders with that reference, newest first. A firm offer is not single-use: each order under a new key is a new order.
- **Auth:** the provider's own. Clients learn it from the card's `api.auth` and from RFC 9728 metadata at the endpoint.
  - **Preferred:** OAuth 2 authorization code with PKCE `S256` and clients identified by a Client ID Metadata Document (CIMD), so a person links an existing account with one consent and no copied key (Mundane and Execution Market accept CIMD today). RFC 7591 dynamic client registration is deprecated by the [MCP specification of 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration); SkyFi, RentAHuman and others still offer it and a client MAY use it there. Recommended scope names: `com.irlpls.capture:quote`, `:order`, `:read`. Providers MAY use their own.
  - **Allowed:** an API key in the header the card names.
  - `public_quotes: true` lets quotes go unauthenticated. Orders are always authenticated.
  - Tokens stay with the client. A router that is not running in the agent's own process MUST NOT store a person's provider credentials (open question Q1).
  - Clients MAY sign requests with Web Bot Auth (RFC 9421) to identify the agent (`research/protocols/agent-identity.md`).
- **Pagination, filtering, search:** none. Discovery of many providers is the registry's job (cards, catalog), not the protocol's.

## 5. Messages

### 5.1 Quote (`quote.json`)

A quote request is `{request, constraints?}`:
- **`request`** is an irlpls Request (STANDARD §4), with one restriction: it MUST NOT carry `policy`.
- **`constraints`** are the hard limits a provider must meet or decline: `max_total`, `deliver_by`, `rights_needed`, `proof` (a minimum level per guarantee) and `scene_nonce` (the buyer's nonce must appear in the capture, which is the depiction check STANDARD §3 names under `content`).

`request.quantity` is at most 10 000. `why` is optional, and clients SHOULD omit it unless the card's `requester.checks` needs it (Q4). A router's quotes carry coarsened places (§14). The exact place goes only to the chosen provider, in its own quote and order.

`scene_nonce` asks that the order's `nonce` be visible in the capture. The nonce exists only once the order does, so the provider gives it to the capturer after the order is placed.

A Quote has `id`, `created_at`, `expires_at`, `request_sha256` and `offers[]`, all required. `request_sha256` is the lower-case hex SHA-256 of the RFC 8785 form of the `request` member this quote request carried, as the server received it. An order of this quote's offer carries the same value, and so do its receipts.

Each offer has the following members:
- `provider`, `route`
- `firm`: `true` means the provider's binding price until `expires_at`; `false` means indicative, from the card
- `quantity`, `total`, `unit_price`, `deliver_by`
- `rights`: granted by this offer; absent means not granted
- `proof`: the levels per guarantee that deliveries will carry
- `order_url` (required on a firm offer), `terms_url`, `continue_url`
- for routers only, a per-request `fit`, and `provider_quote_id` and `provider_quote_url` (§10)

A firm offer requires `total`, `expires_at` and `order_url`. A provider MUST return exactly one offer, or a problem when it can't serve the request at all (`outside_coverage`, `no_match`, `too_slow`, `terms`, `person_targeting_refused`, `needs_input`, …). A router returns one or more offers plus `ruled_out[]`. With none, it returns `422 no_offer` carrying `ruled_out`. Blocking clarifications are `422 needs_input` with `questions[]`, and the client re-quotes with the answers. Quotes are stateless, so there is no "answer" operation.

**Money.** Amounts never convert between currencies: an amount in another currency fails every ceiling (`over_max_total` at a provider, a violation at a client). Clients MUST compare amounts with exact decimal arithmetic. An order "could cost anything" unless its total is known to be `"0"` before it is placed (a firm offer of `"0"`, or a live reading the card says is free).

### 5.2 Order (`order.json`)

The body takes exactly one of two forms:
- **Accepted offer:** `{quote_id, offer_id}`, where `quote_id` is a quote this server issued (at a provider, the provider's own quote). The usual commission path, after a person says yes or under a standing mandate.
- **Direct:** `{request, constraints:{max_total}}`. The provider MUST NOT charge more than `max_total`, and MUST answer `422 quote_required` if its card says `protocol.quote: "required"` and the order could cost anything. With `max_total` `"0"`, this is the one-call path for free live readings.

Required in both: `nonce`, 22–128 base64url characters carrying at least 128 random bits. It is echoed in every receipt.

Optional members:
- `webhook.url`.
- `approval` (`person` | `standing_mandate`): informative only, since a provider can't verify it.
- `client_reference`: finds the order again after the idempotency window. It and `webhook.url` MUST NOT carry personal data.

### 5.3 Order / status (`status.json`)

An Order has the following members:
- `id` (unguessable), `state`, `created_at`, `updated_at`, `links` (`self` and `delivery` always; `cancel`, `continue`)
- `request_sha256` (required): the hash of the request the provider priced for this order. For an accepted offer it is the quote's `request_sha256`; for a direct order, the SHA-256 of the RFC 8785 form of the order's `request`. Every receipt carries it. The client MUST check it equals the hash of the request it sent to this provider; if not, the order's receipts can't verify (client-local `request_mismatch`).
- `reason_code` (required when `failed`) and `reason_text` (SHOULD)
- `provider_status`: the provider's own word, as sent
- `total`: the amount the provider will charge for this order, never more than the accepted offer's `total` or the direct order's `max_total`, in the same currency
- `progress {ordered, delivered}`: `ordered` is the request's `quantity`; `delivered` counts items delivered so far (a live reading may return more items than `quantity`, one per observed property)
- `status_history[]`: append-only
- `delivery`: inline when the order completed synchronously

**States are the Job's:** `submitted`, `in_flight`, `delivered`, `failed`. Known `reason_code` values for `failed` are `rejected`, `cancelled`, `expired`, `undeliverable` and `provider_error`. A shim maps its provider's words to these and also passes the original through in `provider_status`. A broker reading a compliant provider never has to invent a mapping, which closes STANDARD §6's "a status it cannot map" case.

### 5.4 Delivery (`delivery.json`)

A Delivery has `order_id`, `complete`, `items[]`, `provenance` and `relayed_by[]`. `GET /delivery` answers in every state, with `items: []` before the first; `complete` is true exactly when the order is `delivered`.
- **Items** extend Job `results[]`. They require `id` (unique within the order; a delivery that repeats an id is malformed, and a client verifies nothing in it), `provider`, `kind` and `phenomenon_time`. They add `url`, `url_expires_at`, `media_type`, `bytes`, `sha256`, `precision_m`, `c2pa`, an optional embedded `stac` Item, an optional per-item `provenance`, and an optional `receipt`.
- **`url` is the item's media file and nothing else** (other links go in `x-` members). An item with `url` MUST carry `sha256`, `media_type` and `bytes`, whatever the media type. The client MUST check `sha256` after download and treat a mismatch as a failed item.
- **Provenance** is the 8 stages. Each stage lists the guarantees it covers, and each guarantee carries its own level. Stages never merge guarantees into one value, and no value aggregates across stages (STANDARD §2, §3).

| Stage id | Shown as | Guarantees |
|---|---|---|
| `enrolment` | Real person | `human` |
| `capture` | Captured locally | `camera`, `location` |
| `device_attestation` | Genuine device | `application` |
| `signing` | Sealed capture | `camera`, `location`, `content` |
| `timestamp` | Known timestamp | `time` |
| `custody` | Tracked in transit | none (evidence only: hops, logs; plus `relayed_by`) |
| `appraisal` | Independently verified | `corroboration` |
| `delivery` | Delivered to you | none (evidence: the receipt) |

Each guarantee entry has `level` and `basis`:
- **`card`:** the card's level for every capture. A shim MUST use this, and MUST NOT state a level above the card's.
- **`this_capture`:** stated for this item. `evidenced` with this basis MUST carry at least one checkable `evidence` entry (enforced by the schema). A client shows `this_capture` levels beside the card's level and never in place of it.

The broker still attaches `provider_proof` from the card, as STANDARD §8 requires.

**Receipt (STANDARD 6.1 and 14.7 made concrete).**
- **Form:** `{jws, payload, tsa_token?, log_entry?}`. `jws` is a detached compact JWS (RFC 7515 App. F), `header..signature`. The protected header is `{"alg", "kid", "typ": "irlpls-receipt+jws"}`, all three required, with no `crit`. The key id lives only there; a top-level `kid` member is not part of the format and is ignored. The payload is the RFC 8785 form of `receipt.payload`. Required: `iss`, `order_id`, `item_id`, `nonce`, `request_sha256` (the Order's), `media{sha256, media_type, bytes}`, `phenomenon_time` (the item's, as delivered) and `issued_at`. Optional: `time_method`, `lat`, `lon`, `precision_m`, `place_method`; `lat`, `lon` and `precision_m` appear exactly when the item carries them, with the same values.
- **`iss`** is the RFC 6454 ASCII origin of the signer: `scheme://host[:port]`, lower-case, the default port omitted, no trailing slash, compared as a string. A provider's is the origin of its card's `provider_url`; a shim's is the origin of `protocol.operator.url`.
- **What `media` binds.** For a media item (one with `sha256`), `media` is that file: `media.sha256` MUST equal the item's `sha256`, with its `media_type` and `bytes`. For a **value item** (a reading with no file, i.e. no `sha256`), `media` binds the *value document*: the RFC 8785 form of an object with exactly four members, copied from the item as delivered: `kind`, `value`, `unit` and `phenomenon_time` (the observed-at time, the string as delivered, not re-formatted). Each member is always present; one the item omits is `null`. No other member is included (`id`, `label`, `lat`/`lon`, `sensor` and the rest are bound elsewhere in the payload, or not at all). `media.media_type` is `application/json`, `media.bytes` is the document's length in bytes, and `media.sha256` its SHA-256 in lower-case hex. A verifier MUST rebuild the document from the delivered item and compare. Example: `{"kind":"gage-height","phenomenon_time":"2026-10-28T19:30:00Z","unit":"ft","value":3.4}` (87 bytes; a delivered `3.40` canonicalises the same) has SHA-256 `383d9858481e67f712e4d74234c637a71a17a8a2f42a3bf87b75fdfaf6340406`. The reference code is `irlpls.protocol.receipts.value_media`.
- **Algorithms:** `ES256` (EC P-256) or `EdDSA` (OKP Ed25519). The header `alg` MUST equal the `alg` of the key's JWK, and the JWK MUST carry one. Verifiers MUST support ES256 and MAY support EdDSA; anything else fails.
- **Keys:** verifiers ignore `jwk`/`jku`/`x5u` and take the key named by `kid` only from the provider card's `jwks_uri` (a shim's receipts: `protocol.operator.jwks_uri`). The key MUST have `irlpls_use: "receipt"`; a webhook key never verifies a receipt. A receipt whose `issued_at` is after the key's `not_after` fails.
- **Checks:** a verifier MUST check the signature, then that `iss`, `order_id`, `nonce` and `request_sha256` equal its own order's, and that the item as shown is the item as signed: `item_id`, `media` (the downloaded file's SHA-256, `media_type` and `bytes`, or the value document), `phenomenon_time` (string-equal) and `lat`, `lon`, `precision_m` (present in the payload exactly when on the item, and equal). The reference verifier is `irlpls.protocol.receipts.verify_receipt(..., item=...)`.
- **Archiving:** keys retire with `not_after` and are never deleted. Verifiers archive the key set with the receipt.
- **Options:** `tsa_token` (RFC 3161) and `log_entry` are optional.
- **What it proves, and what it doesn't:** a receipt shows who asserted what. It raises a guarantee only as STANDARD 6.1 says, by tying the claim to something independent.

## 6. State machine

```
           quote (stateless; offers expire)          order
 request ──────────────────────────────▶ Quote ──────────────▶ submitted ──▶ in_flight ──▶ delivered
     │                                    │                       │  │           │  │
     │ needs_input (422, questions)       │ quote_expired (410)   │  └──────────▶│  └──▶ failed (reason_code)
     ▼                                    ▼                       │  (sync live) │
  re-quote with answers               re-quote                    └──▶ delivered └──▶ in_flight (progress)
                                                                  └──▶ failed
```

- **Transitions:** `submitted → in_flight | delivered | failed`, `in_flight → in_flight | delivered | failed`. `delivered` and `failed` are terminal and never change. A follow-up is a new order.
- **Partial delivery:** the order stays `in_flight`, `progress.delivered` grows, and `/delivery` returns `complete:false` (STANDARD §11 note: partial is not a state). If it then ends `failed`, the items already delivered stay readable, and `total` states what is charged for them.
- **Cancel** is cooperative. It returns the Order, which may still be `in_flight`, and later ends `failed` with `reason_code: cancelled` or `delivered` (if it was too late). Cancelling a terminal order returns it unchanged with 200. A provider that can't stop the work, or whose card says `protocol.cancel: false`, answers `409 not_cancellable` for a non-terminal order.
- **Polling:** clients MUST poll `status` no faster than `Retry-After` (and never more than once a second), or 60 s by default for commissions, with backoff. Webhooks only cut the delay.

**Problem or failed order?** One rule: a problem from `POST /orders` means no order exists and nothing was charged. Once an order exists, the answer is that Order, and every later failure is `state: failed` with a `reason_code`.

| Situation | Answer | Order exists? |
|---|---|---|
| Body, version, Content-Length or idempotency fault | `400`/`411`/`413`/`415`/`422` problem | no |
| Credentials, scope, account, payment, terms | `401`/`403`/`402` problem | no |
| Unknown quote or offer; quote lapsed before the order (`quote_expired`, `410`) | problem | no |
| `quote_required`, `needs_input`, `no_match`, `outside_coverage`, `too_slow`, `over_max_total`, `rights_unavailable`, `proof_unavailable`, `person_targeting_refused` | `422` problem | no |
| Same key still running; rate limit; provider down; any `5xx` | `409`/`429`/`503`/`5xx` problem; retry with the same key | no |
| Accepted, then the work fails, including a synchronous live reading whose source fails or returns nothing | `200` (done at once) or `202` Order, later `state: failed` with `undeliverable`, `provider_error`, `rejected`, `expired` (an accepted order whose deadline passed) or `cancelled` | yes |

A server MUST NOT answer `POST /orders` with a `5xx` after it has created the order. If it can't tell, a retry with the same key MUST return that order.

**Mapping to the Job (STANDARD §6), which a router keeps for its requester:**

| Job state | Protocol |
|---|---|
| `clarifying` | quote answered `422 needs_input` |
| `proposed` | Quote with offers, none accepted (router plan) |
| `quoted` | firm offer held for approval; "blocked by policy" = the router's own `policy:<rule>` on that offer |
| `submitted` / `in_flight` / `delivered` / `failed` | Order `state`, verbatim; `failed` carries `reason_code` |

STAPI order statuses map as follows (informative): `received`, `accepted` → `submitted`; `scheduled`, `processing` → `in_flight`; `completed` → `delivered`; `rejected`, `cancelled`, `expired`, `failed` → `failed` with the same word as `reason_code`.

## 7. Webhooks (optional)

The order's `webhook.url` gets a **thin** event, following Standard Webhooks (normative):
- **Headers:** `webhook-id` (stable across retries; the dedupe key), `webhook-timestamp`, `webhook-signature: v1a,<base64 Ed25519 sig over id.timestamp.body>` and `irlpls-webhook-kid`, which names the signing key in the provider's `jwks_uri` (a shim's: `operator.jwks_uri`). That key MUST have `irlpls_use: "webhook"` and `alg: "EdDSA"`. Body: `{type:"order.updated", timestamp, data:{order_id, state, order_url}}` (`status.json#/$defs/webhook_event`).
- **The receiver** MUST verify the signature over the raw bytes with the named key, MUST reject a `webhook-timestamp` header more than 5 minutes from now, MUST drop a `webhook-id` it has seen in the last 10 minutes, and MUST check that `data.order_url` equals `{endpoint}/orders/{data.order_id}` exactly, as a string, where `endpoint` is the one it ordered from. It then MUST **re-read the order with `GET`** before acting, so the body never changes state by itself (A2A pattern).
- **The provider** retries with backoff, and MUST apply SSRF rules to the URL (HTTPS, public host, no redirects).

## 8. Errors

All errors are `application/problem+json` (RFC 9457) with `type: https://irlpls.com/2026-10-26/problems/<code>`, `title`, `status`, `detail`, `instance`, plus:
- `code` (required);
- `resolution` (required: `recoverable`, `requires_buyer_input`, `requires_buyer_review`, `retry_later`, `unrecoverable`, following ACP and UCP);
- `continue_url`;
- `errors[]` with RFC 9535 JSONPath `path`;
- `questions`, `violations`, `ruled_out`, `supported_versions`, `quote_id`, `order_id`, `upstream`.

`detail` and every other text member are **untrusted data** (STANDARD §3). A client normalises a provider's problem before an agent or a person sees it: a code from the table below or `provider_error` (the raw code, cut short, under `x-provider_code`); `detail` and `title` cut to 300 characters; only the members listed above; `continue_url` only when it is `https` and on the card's registrable domain; `questions` only for Request fields, each labelled `source: "provider"` and never shown as the client's own. For a code it doesn't know, a client acts on `resolution`; with no known `resolution`, on the status class (`429` and `5xx`: retry later; other `4xx`: don't retry unchanged).

| Code | HTTP | Resolution | Who | Meaning |
|---|---|---|---|---|
| `invalid_request` | 400 | recoverable | any | Body fails the schema; `errors[]` has paths. |
| `unsupported_version` | 400 | recoverable | any | `Irlpls-Version` not served; `supported_versions`. |
| `idempotency_key_required` | 400 | recoverable | any | Order or cancel without the header. |
| `unauthorized` | 401 | recoverable | any | Missing or bad credentials; `WWW-Authenticate` per RFC 6750/9728. |
| `needs_account` | 403 | requires_buyer_input | any | A person must sign up, add billing or pass KYC; `continue_url` required. (The broker's `needs_key` reason maps here.) |
| `insufficient_scope` | 403 | requires_buyer_input | any | Token lacks e.g. `:order`. |
| `terms` | 403 | unrecoverable | provider | The provider's terms forbid this use or automated ordering. |
| `payment_required` | 402 | requires_buyer_input | provider | No payment method or balance; `continue_url`. (An x402/MPP challenge MAY ride alongside.) |
| `not_found` | 404 | unrecoverable | any | Unknown quote or order (or not this client's). |
| `method_not_allowed` | 405 | unrecoverable | any | Wrong method for the path; `Allow` header. |
| `not_acceptable` | 406 | unrecoverable | any | The `Accept` header excludes both `application/json` and `application/problem+json` (no `Accept` accepts anything; media downloads are exempt). The 406 is itself sent as a problem. |
| `quote_expired` | 410 | recoverable | any | Re-quote; `quote_id`. No order exists. |
| `length_required` | 411 | recoverable | any | No `Content-Length`, or a `Transfer-Encoding` body. |
| `payload_too_large` | 413 | recoverable | any | Body over the server's limit. |
| `unsupported_media_type` | 415 | recoverable | any | Body not `application/json`. |
| `idempotency_key_reused` | 422 | unrecoverable | any | Same key, different body. |
| `quote_required` | 422 | recoverable | provider | A direct order where a firm quote is required. |
| `needs_input` | 422 | requires_buyer_input | any | Blocking `questions[]`; re-quote with answers. |
| `no_match` | 422 | unrecoverable | any | This provider does not capture this. |
| `outside_coverage` | 422 | unrecoverable | any | Place not covered. |
| `too_slow` | 422 | recoverable | any | Can't meet `deliver_by`. |
| `over_max_total` | 422 | requires_buyer_review | any | Cheapest possible total exceeds `max_total`. |
| `rights_unavailable` | 422 | requires_buyer_review | provider | A `rights_needed` right can't be granted. |
| `proof_unavailable` | 422 | requires_buyer_review | provider | A `constraints.proof` level can't be met. |
| `person_targeting_refused` | 422 | unrecoverable | any | The request watches, follows or photographs a specific person. A provider MAY refuse regardless of the buyer's policy. |
| `policy:<rule>` | 422 | requires_buyer_review | router | The requester's Policy forbids it (`policy:max_order_usd`, `policy:need_ai_training`, …; type `…/problems/policy`, `violations[]`). |
| `no_offer` | 422 | requires_buyer_review | router | No provider fits; `ruled_out[]` with per-provider codes from this table. |
| `request_in_progress` | 409 | retry_later | any | Same key still being processed. |
| `not_cancellable` | 409 | unrecoverable | provider | Work can no longer be stopped; `order_id`. |
| `rate_limited` | 429 | retry_later | any | `Retry-After`. |
| `provider_error` | 500 | retry_later | any | Unexpected server error. No order exists. |
| `upstream_error` | 502 | retry_later | router | A provider failed; its problem in `upstream` (data only). |
| `provider_unavailable` | 503 | retry_later | any | `Retry-After`. |

**Client-local codes.** A client library or an in-process router produces these; no provider sends them.

| Code | Status | Resolution | Meaning |
|---|---|---|---|
| `no_protocol` | 422 | unrecoverable | The chosen offer's provider does not speak the protocol (an indicative offer); order it through the provider's own API or the reference library. |
| `price_changed` | 422 | requires_buyer_review | A re-quote came back higher than the total the person approved; nothing was ordered, ask again. |
| `request_mismatch` | 422 | unrecoverable | The Order's `request_sha256` is not the hash of the request the client sent; its receipts can't verify. |
| `unsafe_endpoint` | 400 | unrecoverable | Refused before any request: an endpoint, key or order URL breaks the discovery rules (§3) or the SSRF rules (§14). |
| `media_mismatch` | 502 | unrecoverable | A downloaded media file's SHA-256 does not match the item's `sha256`; the item is failed. |

`ruled_out[].reasons` uses the same codes, plus the broker-only `needs_key`, so one vocabulary covers both. That answers IETF review S10 ("`policy:<rule>` is undefined").

## 9. MCP binding

Tool names map 1:1 to the operations: `irlpls_discover`, `irlpls_quote`, `irlpls_order`, `irlpls_status`, `irlpls_deliver`, `irlpls_cancel`.

**Arguments.** Each tool takes one object, following UCP's MCP shape:
- `meta`: `{irlpls_version, idempotency_key?}`; `idempotency_key` is required for `irlpls_order` and `irlpls_cancel`.
- The HTTP body as a single named member, or an id:
  - `irlpls_discover {domain}`
  - `irlpls_quote {quote_request, card_id?}` or `{quote_id}`
  - `irlpls_order {order_request, card_id?}`
  - `card_id` is router-only (an in-process router): quote one provider, or name the provider of a direct order.
  - `irlpls_status {order_id}`
  - `irlpls_deliver {order_id}`
  - `irlpls_cancel {order_id}`

**Results.** A result is the HTTP response body in `structuredContent`, with the same JSON as text in `content[]`. Each tool's `outputSchema` is the matching schema. An error is a tool result with `isError: true` and the problem object as `structuredContent`; JSON-RPC errors are reserved for protocol faults.

**Annotations.**
- `discover`, `quote`, `status` and `deliver` are `readOnlyHint: true`.
- `order` is `destructiveHint: false`, `idempotentHint: true` (given the key) and `openWorldHint: true`, and its description says it may cost money.
- `cancel` is `destructiveHint: true`.

**Async.** MCP has no webhooks, so clients poll `irlpls_status`. MCP Tasks MAY wrap `irlpls_order`, but host support varies.

**Mapping from today's reference tools (informative):**

| Today | Protocol |
|---|---|
| `irlpls_request` / `irlpls_find_providers` | router `irlpls_quote` |
| `irlpls_answer` | re-quote |
| `irlpls_choose_provider` | pick an `offer_id` |
| `irlpls_submit(confirm=false)` | `irlpls_quote` |
| `irlpls_submit(confirm=true, idempotency_key)` | `irlpls_order` |
| `irlpls_get_job(refresh=true)` | `irlpls_status` + `irlpls_deliver` |

`irlpls_get_card` is `irlpls_discover`. `irlpls_list_capabilities` stays a registry tool outside the protocol.

## 10. Routers, and agents without irlpls

**A router talks to a provider** as an ordinary client:
1. **Discover.** Read cards from the registry and refetch `/.well-known/irlpls` for the shortlist.
2. **Filter.** Apply the Policy locally (STANDARD §5). The Policy never leaves the router.
3. **Quote.** Fan out `POST /quotes` to the compliant providers that speak the protocol, using shims for those that don't. Send `constraints` derived from the Policy (`max_order_usd` becomes `max_total`; `need_ai_training` becomes `rights_needed`; `require` becomes `proof`) and a coarsened place: `lat`/`lon` rounded to a 0.01° grid (no jitter, which averages away), `place` left out. Where the router holds no credentials for a provider without `public_quotes`, it builds an **indicative** offer from the card (`firm:false`, no `order_url`).
4. **Re-check.** Check every returned offer against the Policy again: never trust a provider's claim that it complied. Mark violations (`policy:<rule>`) instead of dropping silently.
5. **Hand back.** Return offers ranked by `fit`, plus `ruled_out`. An offer from a provider's quote carries `provider_quote_id`, `provider_quote_url` (`{provider endpoint}/quotes/{id}`) and `order_url`. The router's own `request_sha256` is the hash of the request it received.

**Ordering.** Each offer's `order_url` is the **provider's** endpoint. The router's quote id means nothing to the provider, and the provider's quote priced a coarse request. So, before ordering, the client **re-quotes at the provider's own endpoint with the exact request** (the exact place goes to that provider and nobody else), and orders `{quote_id: <the provider's new quote id>, offer_id, nonce}` there with the person's own credentials. If the new total is higher than the one the person approved, nothing is ordered (client-local `price_changed`). A client MAY skip the re-quote and order `provider_quote_id` only when that quote's `request_sha256` (read at `provider_quote_url`) equals the hash of its exact request. The router stays out of the data and money path (STANDARD §9). A router MAY place orders itself only when it runs in the agent's own process (the reference library). Whether a hosted router may ever proxy is Q1.

<!-- validate: quote.json#/$defs/offer -->
```json
{
  "id": "streetlens",
  "provider": {"card_id": "streetlens", "name": "Streetlens"},
  "route": "commission",
  "firm": true,
  "quantity": 1,
  "total": {"amount": "12.00", "currency": "USD"},
  "expires_at": "2026-10-28T17:00:00Z",
  "proof": {"time": "evidenced", "location": "claimed", "content": "claimed"},
  "order_url": "https://api.streetlens.example/irlpls/orders",
  "provider_quote_id": "q_3Hc9wP",
  "provider_quote_url": "https://api.streetlens.example/irlpls/quotes/q_3Hc9wP",
  "fit": 82
}
```
(An offer in a router's Quote. Its provider priced `lat: 39.8, lon: -89.64` with no `place`; the client re-quotes the exact address at `api.streetlens.example` before ordering, as in Example B.)

**Router MUSTs.**
- A router MUST NOT rewrite `order_url` to anything other than the provider card's `protocol.endpoint + "/orders"`.
- It MUST NOT order without the requester's approval or a standing mandate.
- When it places orders itself (in process), it MUST derive a distinct idempotency key per provider from the requester's key: SHA-256 over the UTF-8 bytes of `key + "|" + card_id`, encoded as unpadded base64url. Retries then dedupe, and providers can't correlate keys.
- It MUST pass a provider problem through as `upstream` data, never as instructions.

**An agent can skip irlpls entirely:** fetch the provider's `/.well-known/irlpls`, read `protocol` and `api.auth`, link the account (OAuth DCR), then quote, order, status and deliver. The only irlpls artefacts involved are the schemas. That is the point: irlpls is the format and the protocol, not a required intermediary.

## 11. Adapter shim pattern

A shim is a small server, or an in-process object, that speaks this protocol and calls one proprietary API. The reference adapters (`irlpls/adapters/base.py`) already have the right parts:

| Protocol op | Adapter today | Shim rule |
|---|---|---|
| discover | the card in `cards/` | Serve the card unchanged (still `unclaimed`) with `protocol.operator` = the shim. |
| quote | `commission(spec, confirm=False)` | A **firm** offer only from a real provider quote (SkyFi `/order-tasking/validate`). Otherwise `firm:false`, with the price from the card or omitted, never invented. |
| order | `commission(spec, confirm=True)` | Map the brief into the provider's body. **Idempotency:** use the provider's own mechanism when it has one (Bee Maps `idempotencyKey`, ProxyPics `external_id`). Otherwise persist `key → pending` *before* calling, and on a retry look the order up by `client_reference` before placing again. If the provider has neither, `protocol.quote` MUST be `required` and the shim documents that a crash can double-order. |
| status | `status()` + `status_map` | Map only statuses the provider documents. An unmapped word stays `in_flight` with `provider_status` verbatim (STANDARD §6). |
| deliver | `deliverables()` | Download each media file and compute `sha256`, `media_type` and `bytes` itself; an item it can't hash loses `url`. Other links move to `x-` members. Set provenance `basis: card` with the card's levels. Add itself to `relayed_by`. |
| cancel | (none yet) | Call the provider's cancel if documented. Otherwise `protocol.cancel: false`. |

**Shim MUSTs.**
- A shim MUST NOT sign a receipt as the provider. It MAY sign one as itself (`iss` = the origin of `protocol.operator.url`, keys at `protocol.operator.jwks_uri`), which attests custody only (stage `custody`, "Tracked in transit") and never raises a provider guarantee.
- It forwards the caller's credentials and never stores them. A shim hosted by a third party sees keys and data, and MUST say so in `protocol.operator`.
- Deployment preference: in-process (the reference library), then provider-hosted (at which point it is simply the provider's implementation), then third-party.

## 12. Example A: river reading (live, one call)

An agent asks a router. The router uses the in-process USGS shim; USGS publishes no card or protocol. This shows the hosted shape. Today the reference library produces the same messages in process. Readings are illustrative.

```
POST https://router.irlpls.example/irlpls/orders
Irlpls-Version: 2026-10-26
Idempotency-Key: 1b4e28ba-2fa1-41d2-883f-0016d3cca427
Content-Type: application/json
```
<!-- validate: order.json -->
```json
{
  "request": {"need": "river level at Comfort, TX now", "lat": 29.9683, "lon": -98.9054, "country_code": "US",
              "mode": "live", "modalities": ["sensor"], "require": ["time", "location", "content"]},
  "constraints": {"max_total": {"amount": "0", "currency": "USD"}},
  "nonce": "q8XvY2m1Lw0aPp5sR3tU7w"
}
```
`200 OK` (completed synchronously, so no 202). `request_sha256` is the hash of the order's `request`; `progress.delivered` is 2 because one reading returns two items:
<!-- validate: status.json -->
```json
{
  "id": "ord_r1vErQ7c",
  "state": "delivered",
  "card_id": "usgs-water",
  "route": "live",
  "total": {"amount": "0", "currency": "USD"},
  "request_sha256": "9461ca0eed7b9843e339413e7b6dc8fe4129d56730c981c90ab43977749e9052",
  "created_at": "2026-10-28T15:20:04Z",
  "updated_at": "2026-10-28T15:20:05Z",
  "progress": {"ordered": 1, "delivered": 2},
  "status_history": [{"at": "2026-10-28T15:20:04Z", "state": "submitted"},
                     {"at": "2026-10-28T15:20:05Z", "state": "delivered"}],
  "links": {"self": "https://router.irlpls.example/irlpls/orders/ord_r1vErQ7c",
            "delivery": "https://router.irlpls.example/irlpls/orders/ord_r1vErQ7c/delivery"},
  "delivery": {
    "order_id": "ord_r1vErQ7c",
    "complete": true,
    "items": [
      {"id": "itm_1", "provider": "usgs-water", "kind": "discharge", "phenomenon_time": "2026-10-28T15:00:00Z",
       "lat": 29.96994, "lon": -98.90457, "value": 74.2, "unit": "ft3/s",
       "label": "Guadalupe Rv at Comfort, TX (USGS 08167000), provisional", "sensor": {"kind": "device"}},
      {"id": "itm_2", "provider": "usgs-water", "kind": "gage_height", "phenomenon_time": "2026-10-28T15:00:00Z",
       "lat": 29.96994, "lon": -98.90457, "value": 2.31, "unit": "ft",
       "label": "Guadalupe Rv at Comfort, TX (USGS 08167000), provisional", "sensor": {"kind": "device"}}
    ],
    "provenance": {
      "card_id": "usgs-water",
      "stages": [
        {"stage": "enrolment", "shown_as": "Real person", "guarantees": {"human": {"level": "none", "basis": "card"}}},
        {"stage": "capture", "shown_as": "Captured locally",
         "guarantees": {"camera": {"level": "none", "basis": "card"}, "location": {"level": "claimed", "basis": "card"}}},
        {"stage": "device_attestation", "shown_as": "Genuine device", "guarantees": {"application": {"level": "none", "basis": "card"}}},
        {"stage": "signing", "shown_as": "Sealed capture",
         "guarantees": {"camera": {"level": "none", "basis": "card"}, "location": {"level": "claimed", "basis": "card"},
                        "content": {"level": "claimed", "basis": "card",
                                    "unknown": "Provisional values (the freshest ones) can be substantially wrong."}}},
        {"stage": "timestamp", "shown_as": "Known timestamp", "guarantees": {"time": {"level": "none", "basis": "card"}}},
        {"stage": "custody", "shown_as": "Tracked in transit", "unknown": "Fetched over TLS from the public USGS API by the router's shim; no hop is logged."},
        {"stage": "appraisal", "shown_as": "Independently verified", "guarantees": {"corroboration": {"level": "claimed", "basis": "card"}}},
        {"stage": "delivery", "shown_as": "Delivered to you", "unknown": "No receipt: USGS signs nothing, and the shim does not sign for it."}
      ]
    },
    "relayed_by": [{"name": "irlpls reference router (usgs-water shim)", "role": "shim", "url": "https://router.irlpls.example/irlpls"}]
  }
}
```

## 13. Example B: commissioned storefront photo (quote → order → status → deliver)

The agent talks **directly** to a provider that speaks the protocol. Streetlens is fictional (`.example`), and its card is the §3 excerpt. The person's OAuth link was made once, the client identified by its Client ID Metadata Document.

**1. Quote.** `POST https://api.streetlens.example/irlpls/quotes` (with `Authorization: Bearer …` and `Irlpls-Version: 2026-10-26`):
<!-- validate: quote.json#/$defs/quote_request -->
```json
{
  "request": {"need": "photo of the storefront at 123 Main St, Springfield, IL today: is it open, and what does the sign say",
              "place": "123 Main St, Springfield, IL 62701", "lat": 39.8017, "lon": -89.6436, "country_code": "US",
              "within_minutes": 480, "quantity": 1, "mode": "commission", "modalities": ["photo"],
              "require": ["time", "location", "content"]},
  "constraints": {"max_total": {"amount": "40.00", "currency": "USD"}, "deliver_by": "2026-10-28T23:00:00Z",
                  "proof": {"time": "evidenced"}, "rights_needed": ["commercial_use"], "scene_nonce": true}
}
```
(Sent directly to the chosen provider, the exact address goes in. Behind a router this is the re-quote of §10: the router's own fan-out carried only the coarse place.)

`201 Created`, `Location: https://api.streetlens.example/irlpls/quotes/q_8Rk2vT`:
<!-- validate: quote.json -->
```json
{
  "id": "q_8Rk2vT",
  "created_at": "2026-10-28T15:02:11Z",
  "expires_at": "2026-10-28T17:02:11Z",
  "request_sha256": "c480de7c621be47e13e1200ff1867b7921150a3a3c0055ad66ccaa06b1862627",
  "offers": [{
    "id": "o_1",
    "provider": {"card_id": "streetlens", "name": "Streetlens", "card_url": "https://streetlens.example/.well-known/irlpls"},
    "route": "commission",
    "firm": true,
    "quantity": 1,
    "total": {"amount": "12.00", "currency": "USD"},
    "unit_price": {"amount": "12.00", "currency": "USD"},
    "unit": "photo",
    "expires_at": "2026-10-28T17:02:11Z",
    "deliver_by": "2026-10-28T21:00:00Z",
    "rights": {"commercial_use": "allowed", "ai_training": "forbidden", "resale": "forbidden", "buyer_owns_output": true},
    "proof": {"human": "claimed", "application": "evidenced", "camera": "claimed", "time": "evidenced",
              "location": "claimed", "content": "claimed", "corroboration": "none"},
    "scene_nonce": true,
    "order_url": "https://api.streetlens.example/irlpls/orders",
    "terms_url": "https://streetlens.example/terms"
  }]
}
```

**2. The person approves $12.00.** `POST https://api.streetlens.example/irlpls/orders`, with `Idempotency-Key: 7c0d6b0e-5d4b-4b8e-9f43-2a1c2f9d0e11`:
<!-- validate: order.json -->
```json
{
  "quote_id": "q_8Rk2vT",
  "offer_id": "o_1",
  "nonce": "Zx3qN0p8YgS1rT6uWv9a2Q",
  "client_reference": "job-2f81",
  "webhook": {"url": "https://agent.example/hooks/irlpls"},
  "approval": {"by": "person", "at": "2026-10-28T15:04:40Z"}
}
```
`202 Accepted`, `Location: https://api.streetlens.example/irlpls/orders/ord_5d1Hq`, `Retry-After: 600`:
<!-- validate: status.json -->
```json
{
  "id": "ord_5d1Hq",
  "state": "submitted",
  "quote_id": "q_8Rk2vT",
  "offer_id": "o_1",
  "card_id": "streetlens",
  "route": "commission",
  "total": {"amount": "12.00", "currency": "USD"},
  "request_sha256": "c480de7c621be47e13e1200ff1867b7921150a3a3c0055ad66ccaa06b1862627",
  "created_at": "2026-10-28T15:04:41Z",
  "updated_at": "2026-10-28T15:04:41Z",
  "progress": {"ordered": 1, "delivered": 0},
  "status_history": [{"at": "2026-10-28T15:04:41Z", "state": "submitted", "provider_status": "posted"}],
  "links": {"self": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq",
            "delivery": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq/delivery",
            "cancel": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq/cancel"}
}
```
`request_sha256` repeats the quote's, so the client knows the provider will sign the request it sent. A retry with the same key and body returns this same response (with `Idempotent-Replayed: true`). The same key with a different body gets `422 idempotency_key_reused`.

**3. Webhook, then status.** The provider POSTs to the hook. Headers: `webhook-id: msg_2Lq9`, `webhook-timestamp: 1761681600`, `webhook-signature: v1a,<Ed25519 sig>` and `irlpls-webhook-kid: sl-wh-2026-10`. The body:
<!-- validate: status.json#/$defs/webhook_event -->
```json
{"type": "order.updated", "timestamp": "2026-10-28T19:41:30Z",
 "data": {"order_id": "ord_5d1Hq", "state": "delivered", "order_url": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq"}}
```
The agent verifies it with key `sl-wh-2026-10` from `jwks_uri`, checks `order_url` is exactly `https://api.streetlens.example/irlpls/orders/ord_5d1Hq`, then `GET`s that URL:
<!-- validate: status.json -->
```json
{
  "id": "ord_5d1Hq",
  "state": "delivered",
  "provider_status": "approved",
  "quote_id": "q_8Rk2vT",
  "offer_id": "o_1",
  "card_id": "streetlens",
  "route": "commission",
  "total": {"amount": "12.00", "currency": "USD"},
  "request_sha256": "c480de7c621be47e13e1200ff1867b7921150a3a3c0055ad66ccaa06b1862627",
  "created_at": "2026-10-28T15:04:41Z",
  "updated_at": "2026-10-28T19:44:30Z",
  "progress": {"ordered": 1, "delivered": 1},
  "status_history": [
    {"at": "2026-10-28T15:04:41Z", "state": "submitted", "provider_status": "posted"},
    {"at": "2026-10-28T18:55:02Z", "state": "in_flight", "provider_status": "accepted_by_photographer"},
    {"at": "2026-10-28T19:44:30Z", "state": "delivered", "provider_status": "approved"}
  ],
  "links": {"self": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq",
            "delivery": "https://api.streetlens.example/irlpls/orders/ord_5d1Hq/delivery"}
}
```

**4. Deliver.** `GET https://api.streetlens.example/irlpls/orders/ord_5d1Hq/delivery`:
<!-- validate: delivery.json -->
```json
{
  "order_id": "ord_5d1Hq",
  "complete": true,
  "items": [{
    "id": "itm_1",
    "provider": "streetlens",
    "kind": "photo",
    "phenomenon_time": "2026-10-28T19:41:07Z",
    "result_time": "2026-10-28T19:44:30Z",
    "lat": 39.80168, "lon": -89.64357, "precision_m": 12,
    "label": "Storefront, 123 Main St: lights on, door open, sign reads 'Main St Bakery'",
    "url": "https://media.streetlens.example/o/ord_5d1Hq/itm_1.jpg?sig=k2Vb",
    "url_expires_at": "2026-11-04T19:44:30Z",
    "media_type": "image/jpeg",
    "bytes": 2481337,
    "sha256": "83f1545e06f99652d73face53acf4310d6f630f6c8e08d292b252f7cfb2f11c7",
    "sensor": {"kind": "human"},
    "c2pa": "valid",
    "receipt": {
      "jws": "eyJhbGciOiJFUzI1NiIsImtpZCI6InNsLTIwMjYtMTAiLCJ0eXAiOiJpcmxwbHMtcmVjZWlwdCtqd3MifQ..6SzPiO680GT23KdLmF5NcSAxZTo1emDKiOhFzlxv-4x-G0_CVQ0IG4EMM0QaViLX8dbI46QeHXrYm2HYVyQNpw",
      "payload": {
        "iss": "https://streetlens.example", "order_id": "ord_5d1Hq", "item_id": "itm_1",
        "nonce": "Zx3qN0p8YgS1rT6uWv9a2Q",
        "request_sha256": "c480de7c621be47e13e1200ff1867b7921150a3a3c0055ad66ccaa06b1862627",
        "media": {"sha256": "83f1545e06f99652d73face53acf4310d6f630f6c8e08d292b252f7cfb2f11c7", "media_type": "image/jpeg", "bytes": 2481337},
        "phenomenon_time": "2026-10-28T19:41:07Z", "time_method": "device clock, RFC 3161 token over the media hash",
        "lat": 39.80168, "lon": -89.64357, "precision_m": 12, "place_method": "device GNSS, not authenticated",
        "issued_at": "2026-10-28T19:44:30Z"
      }
    }
  }],
  "provenance": {
    "card_id": "streetlens",
    "card_url": "https://streetlens.example/.well-known/irlpls",
    "card_updated": "2026-10-20",
    "stages": [
      {"stage": "enrolment", "shown_as": "Real person",
       "guarantees": {"human": {"level": "claimed", "basis": "card", "unknown": "ID checked at sign-up; the account could be shared since."}}},
      {"stage": "capture", "shown_as": "Captured locally",
       "guarantees": {"camera": {"level": "claimed", "basis": "card"},
                      "location": {"level": "claimed", "basis": "this_capture", "unknown": "Phone GNSS; a mock-location app could forge it."}}},
      {"stage": "device_attestation", "shown_as": "Genuine device",
       "guarantees": {"application": {"level": "evidenced", "basis": "this_capture",
         "evidence": [{"type": "device_attestation", "url": "https://media.streetlens.example/o/ord_5d1Hq/itm_1.attest.json",
                       "sha256": "5f2b0c1a9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b"}]}}},
      {"stage": "signing", "shown_as": "Sealed capture",
       "guarantees": {"content": {"level": "claimed", "basis": "this_capture",
         "evidence": [{"type": "c2pa_manifest", "url": "https://media.streetlens.example/o/ord_5d1Hq/itm_1.c2pa"}],
         "unknown": "Manifest is valid but its signer is not on the C2PA Trust List; the nonce card is visible in frame (depiction check by the buyer)."}}},
      {"stage": "timestamp", "shown_as": "Known timestamp",
       "guarantees": {"time": {"level": "evidenced", "basis": "this_capture",
         "evidence": [{"type": "rfc3161_token", "url": "https://media.streetlens.example/o/ord_5d1Hq/itm_1.tsr"}],
         "unknown": "Shows it existed no later than 19:41:12Z; the nonce shows no earlier than 15:04:41Z."}}},
      {"stage": "custody", "shown_as": "Tracked in transit", "unknown": "Provider storage to you over TLS; hops are not logged."},
      {"stage": "appraisal", "shown_as": "Independently verified", "guarantees": {"corroboration": {"level": "none", "basis": "card"}}},
      {"stage": "delivery", "shown_as": "Delivered to you",
       "evidence": [{"type": "receipt", "detail": "Detached JWS on itm_1, key sl-2026-10 from jwks_uri."}]}
    ]
  }
}
```
The receipt's signing key, published at `jwks_uri` (an example key generated for this draft; the check script verifies the JWS with it, and that the item as shown matches the payload, place and time included). The webhook key `sl-wh-2026-10` (`irlpls_use: "webhook"`, `alg: "EdDSA"`) sits in the same set and is left out here:
<!-- validate: common.json#/$defs/jwk_set -->
```json
{"keys": [{"kty": "EC", "crv": "P-256", "kid": "sl-2026-10", "alg": "ES256", "use": "sig", "irlpls_use": "receipt",
           "x": "ZebiMAKG2LZBJfAQ2pMUokYgp9caLqPBDAQ-sTZdPxQ", "y": "JsSpe7y2OzK_qoJsI7AYmZ31aNOEeAEITf-yBm1Zxs0",
           "not_after": "2027-10-31T00:00:00Z"}]}
```

**5. Problems along the way.** A person with no Streetlens account, at quote time (`403`):
<!-- validate: problem.json -->
```json
{"type": "https://irlpls.com/2026-10-26/problems/needs_account", "title": "An account is needed", "status": 403,
 "code": "needs_account", "resolution": "requires_buyer_input",
 "detail": "Link a Streetlens account and add a card before ordering.",
 "continue_url": "https://streetlens.example/signup"}
```
Approving after the quote lapsed (`410`):
<!-- validate: problem.json -->
```json
{"type": "https://irlpls.com/2026-10-26/problems/quote_expired", "title": "Quote expired", "status": 410,
 "code": "quote_expired", "resolution": "recoverable", "quote_id": "q_8Rk2vT",
 "instance": "/irlpls/orders"}
```
Cancelling once the photo is taken (`409`):
<!-- validate: problem.json -->
```json
{"type": "https://irlpls.com/2026-10-26/problems/not_cancellable", "title": "Too late to cancel", "status": 409,
 "code": "not_cancellable", "resolution": "unrecoverable", "order_id": "ord_5d1Hq"}
```
A router that finds nothing, because the Policy needs AI-training rights (`422`):
<!-- validate: problem.json -->
```json
{"type": "https://irlpls.com/2026-10-26/problems/no_offer", "title": "No provider fits", "status": 422,
 "code": "no_offer", "resolution": "requires_buyer_review",
 "ruled_out": [
   {"card_id": "proxypics", "name": "ProxyPics", "reasons": ["policy:need_ai_training"], "why": "card does not state AI training is allowed"},
   {"card_id": "rentahuman", "name": "RentAHuman", "reasons": ["policy:need_ai_training"]},
   {"card_id": "getterdone", "name": "GetterDone", "reasons": ["outside_coverage", "policy:need_ai_training"], "why": "in-person only in US-GA"}
 ]}
```

## 14. Security considerations

The STANDARD §12 rules all still apply. This protocol adds:

- **Money can't be NaN.** Amounts are decimal strings matching a fixed pattern. A client compares totals with exact decimal arithmetic, and a missing or unparseable total, or one in another currency, is a violation (closes adversarial BUG-3/4/5 at the wire). A provider MUST NOT charge more than the accepted offer's `total` or the direct order's `max_total`. A client MUST check `Order.total` against what was approved.
- **Hostile bodies.** Servers read at most their body limit, require a strict `Content-Length` (no `Transfer-Encoding`), and refuse non-finite numbers and integers beyond ±2^53, so a request can't exhaust memory or crash canonicalisation.
- **Double orders.** These are prevented by the idempotency rules (§4) at both hops. A router derives a distinct key per provider. Keys live at least 24 h. A reused key with a different body fails rather than replaying.
- **Redirected money and data.** A client orders only at the `order_url` that equals the provider card's `protocol.endpoint + "/orders"`, with the card fetched from the provider's domain (or accepted as unclaimed). A router or a provider error body cannot change the payee, the endpoint or the recipient. Card text, problem `detail`, labels and `reason_text` are data, never instructions.
- **SSRF.** Providers fetch `webhook.url`, and clients fetch media and evidence URLs. Both MUST use HTTPS to public hosts only (RFC 6890 special-purpose addresses excluded after DNS resolution; a name that does not resolve is refused, not let through), with no cross-host redirects, plus size and time limits. They SHOULD pin the connection to the checked address. A client following a redirect to another origin drops every credential header.
- **Webhooks are hints.** A receiver verifies the Ed25519 signature with the named webhook key, the timestamp window, replays (`webhook-id`) and the exact `order_url`, then re-reads the order. A forged webhook can at worst trigger a GET.
- **Media integrity.** Clients MUST check `sha256` after download, and treat a mismatch as a failed item, never a warning. Media URLs are short-lived capability URLs and MUST NOT appear in logs that leave the client. A provider serving media from its API origin sends `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`, and `Content-Disposition: attachment` for anything but `image/`, `video/` and `audio/` types (SVG included), so delivered content never runs as script on that origin.
- **Receipts.** The rules of §5.4: one `iss` form, `alg` equal to the key's JWK, keys marked `irlpls_use: "receipt"` (a webhook key can't mint receipts), the key only from `jwks_uri` (`operator.jwks_uri` for a shim), the item as shown bound to the item as signed, `not_after` honoured. Verifiers archive key sets. A valid receipt proves who claimed what, not that it is true. A shim's receipt covers custody only.
- **Privacy: what irlpls can and cannot promise.** It can promise: the Policy and `why` never leave the requester's side unless a provider needs `why`; a router's fan-out never carries the `place` field and rounds coordinates to a 0.01° grid; the exact place goes only to the provider the person chose, in its own quote and order; idempotency keys and nonces are random. It cannot promise anonymity. An order is made with the person's own provider account, so that provider learns who the account holder is, and also sees the IP address, the webhook host and `client_reference` (neither of which may carry personal data). `need` and other free text are sent as written and may name a place or a person; irlpls can't strip them reliably. What a provider logs is up to the provider. Router fan-out tells several providers roughly where someone wants to look, so routers SHOULD quote only the shortlist.
- **Person targeting.** A provider MAY refuse any request with `person_targeting_refused`, whatever the buyer's policy. A router MUST NOT send a `targets_person` request to people-based providers without `allow_person_targeting` (STANDARD §5 rule 4). Standing mandates SHOULD NOT cover such orders.
- **Credentials.** They are the provider's own and held by the client. OAuth uses PKCE `S256` and exact redirect matching, and the client revokes on unlink. A hosted router or third-party shim that sees credentials must say so (`protocol.operator`).
- **Abuse and rate limits.** Providers answer `429` with `Retry-After`. Clients back off (never more than one poll a second, whatever `Retry-After` says), and SHOULD cap spend per period in addition to the per-order cap (STANDARD §12).
- **Local servers.** The reference servers bind loopback by default; binding another address needs an explicit flag. An MCP server on streamable HTTP has no authentication of its own, so on a non-loopback host it needs `--allow-remote` and warns.

## 15. What would change elsewhere (still open)

Done: STANDARD §14 (normative), the `protocol` card member, the six MCP tools, the reference server, client, shims and the conformance checker. Still open:
- **Job schema:** `results[]` items gain `id`, `sha256`, `media_type`, `bytes`, `url_expires_at`, `precision_m` and `provenance` (or `$ref` `delivery.json#/$defs/item`); `Job.error` becomes a problem object (IETF review B3).
- **Adapters:** `adapters/base.py` becomes the shim interface (`quote/order/status/deliver/cancel`, typed money).
- **Not in the reference server:** webhook replay dedupe is the receiver's job (`verify_webhook(..., seen=...)`). (`406 not_acceptable` is now sent when `Accept` excludes both `application/json` and `application/problem+json`; delivered media is exempt.)

## 16. Open questions

1. **Hosted router in the order path?** Recommended: no. A hosted router only discovers and quotes, and orders go to the provider directly. Only the in-process library proxies. This keeps "never in the path of data or money" literally true.
2. **Cancelled: its own state, or `failed` + `reason_code: cancelled`?** Recommended: the latter. It needs no STANDARD state change, and STANDARD already says failed covers cancelled.
3. **Money format:** decimal strings (`"12.00"`, readable, NaN-proof), or UCP's integer minor units? Recommended: decimal strings.
4. **Should providers ever see `why` or who the requester is?** Recommended: not by default, only when a card's `requester.checks` requires it. But people-dispatch providers may reasonably demand an accountable principal.
5. **Publish the protocol now as part of 2026-10-26, or hold it for a provider pilot (ProxyPics/RentAHuman shim, then one real provider) and a new date?** Recommended: keep it private, build the shims, then decide.

Minor:
- 202 (the brief) vs 201 (STAPI/OGC, and the lifecycle research) for order creation. This draft uses 202 + `Location` with the Order as the status monitor.
- Thin webhooks with Ed25519 (Standard Webhooks) vs UCP's RFC 9421. This draft uses Standard Webhooks.
