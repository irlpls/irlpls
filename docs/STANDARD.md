# irlpls 2026-10-26

Status: draft, private preview. Version `2026-10-26` (replaces v0.1; Section 10). Capability names: `com.irlpls.capture.*`. Schemas: `schema/*.json` and `schema/protocol/*.json`, to be served at `https://irlpls.com/2026-10-26/schemas/` and `…/schemas/protocol/`. Reference implementation: `irlpls/`.

## Abstract

irlpls is a universal interface for acquiring new observations of the physical world, with a record of where each one came from. It defines a provider description (the Capture Card) and three messages (Request, Policy, Job), so that any requester, broker and provider can interoperate without sharing code, and an optional wire protocol (Section 14) for quoting, ordering and delivery over HTTPS. A Capture Card states, guarantee by guarantee, what a provider can evidence about capture, device or app attestation, signing, time, place, integrity and corroboration, and what it only claims.

## 1. Terminology

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in capitals. Unknown members in any irlpls document MUST be ignored, and unknown enum values MUST be treated as unsupported (Section 10).

| Term | Meaning |
|---|---|
| **Provider** | A source of new observations: people, drones, satellites, cameras, sensors. |
| **Capture Card** | A JSON document describing one provider. Schema: `capture-card.schema.json`. |
| **Requester** | An agent, or a person through their agent, that needs an observation. |
| **Broker** | Software that turns a Request into a Job: it plans against cards, applies the Policy and calls providers. |
| **Request** | What the requester needs, in natural language, plus optional hints. Schema: `request.schema.json`. |
| **Policy** | Rules set by the requester's owner, executed by the broker. Schema: `policy.schema.json`. |
| **Job** | The broker's response to a Request, updated through its lifecycle. Schema: `job.schema.json`. |
| **Route** | How a Job is fulfilled: `live` (an observation reporting now) or `commission` (a capture made to order). |
| **Guarantee** | One of seven independent properties a provider may prove about its data (Section 3). |
| **Level** | How a guarantee is supported: `evidenced`, `claimed` or `none`. |
| **Attestation** | A record that an independent third party checked a provider's claim. |
| **Standing mandate** | A Policy with `auto_submit` and `max_order_usd`, under which the broker may place orders without asking. |
| **Adapter** | Code in the reference library that calls one provider's API. Not part of the standard. |

## 2. Capture Card

*In short: one JSON file per provider, in the provider's own words, with the evidence for each guarantee stated separately.*

A Capture Card states what a provider can capture, where, how fast, at what price, under what rights, how to call it, and what it can prove. All claims belong to the provider (or, for unclaimed cards, to the cited public sources). A card has no overall quality score. A broker MUST NOT publish, display or rank by any value that aggregates guarantees across keys as a property of a provider. The only permitted use of several guarantees together is the per-request `fit` of Section 7, which is a ranking aid for one Job and never a trust score.

### 2.1 Required fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string, `^[a-z0-9-]+$` | Stable identifier. |
| `name` | string | Provider name. |
| `summary` | string | What the provider does, in plain language. |
| `status` | enum | `sample` (fictional), `unclaimed` (compiled from public sources, not confirmed by the provider), `provider-maintained` (claimed and kept current by the provider), `defunct` (no longer operating). |
| `capabilities` | object | `modalities` (array of `photo`, `video`, `audio`, `aerial`, `sensor`, `text-report`) and `tasks` (array of strings), both required; `capture_types` (optional finer kinds: `egocentric`, `depth`, `mocap`, `teleoperation`, `tactile`, `multispectral`, `sar`, `thermal`, ...). |
| `pricing` | object | `model` (string), `from_usd` (number, ≥ 0; `0` means the price is not published, see Section 2.3), `unit` (string), all required; `notes` (string) optional. |
| `latency` | object | `typical` and `notes` (strings), both required. Informational: brokers display it; matching uses `delivery.speed`. |
| `coverage` | object | `regions` (array of strings) and `notes` (string), both required; `areas` (array of ISO 3166-1 alpha-2 countries or ISO 3166-2 subdivisions, e.g. `US`, `US-WA`; `["*"]` = no country limit stated) optional. When `areas` is present it decides coverage; the text says how dense. |
| `licensing` | object | `summary` (string, required); `ai_training` and `resale` (`allowed` \| `forbidden`; absent = not stated); `buyer_owns_output` (boolean); `vendor_trains_on_data` (`yes` \| `no` \| `unless_opted_out`); `terms` (booleans `commercial_use`, `redistribution`, `attribution_required`, `share_alike`, `modification`; absent = not stated). |
| `verification` | object | One entry per guarantee. Exactly the seven keys of Section 3, all required; no other key is allowed. |
| `delivery` | object | `modes` (array of `live`, `commission`; required), `speed` (`minutes`, `hours`, `days`, `weeks`, `unknown`; required; as stated by the provider), `basis` (string, required; the provider statement the speed rests on), `formats` (optional: `jpeg`, `mp4`, `geotiff`, `cog`, `stac-item`, `lerobot`, `rlds`, `csv`, `json`, ...). |
| `updated` | string, date | Date the card was last revised. |
| `spec_version` | `"2026-10-26"` | Version of this standard the card follows. |
| `capability` | `"com.irlpls.capture.card"` | The capability this document implements (Section 2.4). |

### 2.2 Optional fields

| Field | Type | Meaning |
|---|---|---|
| `provider_url` | string | Provider home page. |
| `short` | string, max 120 chars | One sentence for lists. |
| `tags` | array of strings | Free-form keywords. |
| `freshness` | object | `mode` (`on_demand`, `live`, `archive`, `mixed`), `typical_age` (string), `notes` (string). How old delivered observations can be. Informational: matching uses `delivery`. |
| `api` | object | How an agent calls the provider, from public docs only: `available` (boolean), `docs_url`, `base_url`, `auth` (array, every way to authenticate: `type` [`none`, `api_key_header`, `api_key_query`, `bearer`, `oauth2`, `basic`, `other`], `header`, `env_var`, `how_to_get`; for `oauth2` also `resource_metadata` (RFC 9728 URL), `scopes`, `cimd` (boolean: the provider accepts MCP Client ID Metadata Documents, so an agent identifies itself by a URL and a person links an account with one consent; the way the MCP specification of 2026-07-28 recommends), `dynamic_registration` (boolean: the provider offers RFC 7591 dynamic client registration; **deprecated** for MCP clients by that specification in favour of `cimd`, kept so cards can state it), `pkce` (string, the code-challenge method: `S256` or `plain`; not a boolean), `transport` [`rest`, `mcp`]), `mcp` (`remote`, `registry_name`, `auth`, `first_party`, `community`), `openapi_url`, `notes`, `endpoints` (array of `action` [`search`, `order`, `quote`, `status`, `fetch`, `list`], `method`, `path`, all required; `params`, `notes` optional), `sdk`, `rate_limits` (strings), `self_serve` (boolean: usable without contacting sales). |
| `access` | object | `cost` (`free`, `free with key`, `freemium`, `paid`, `quote`), `key_required` (boolean), `tested_live` (`YYYY-MM-DD` date string or null: when an irlpls health check last received real data), `automation_allowed` (boolean: the provider's terms allow agents; `false` rules it out), `gate` (`none`, `approval`, `contract`, `enterprise`: what stands between sign-up and the first call), `rate_limit` (optional object: the provider's published limit, Section 2.5). |
| `payment` | object | How a buyer pays the provider directly: `methods` (`card`, `hosted_checkout`, `prepaid_balance`, `invoice`, `x402`, `mpp`, `ucp_checkout`, `acp_checkout`, `crypto`), `agent_rails` (the agent payment rails it accepts: `x402`, `mpp` (Machine Payments Protocol), `spt` (Stripe shared payment token), `link_card` (a one-time card from Stripe Link), `card_on_file_api` (the API charges the person's stored card or billing method)), `agent_can_pay` (boolean), `prepaid` (boolean), `notes`. irlpls never handles money. |
| `account` | object | Opening an account with the provider: `headless_signup` (boolean: an agent can create the account through the API), `kyc` (`none`, `email`, `id`, `business`: the strongest check on the buyer before a first order), `agent_may_act` (`allowed` boolean and `source_url`, both required, plus an optional short `quote`: the provider's terms or docs let, or do not let, an AI agent act or post for the person), `notes`. Every member is optional and comes only from a cited public source; absent means not stated. |
| `capture` | object | `consent` (`documented`, `method`, `evidence_url`, `unknown`: how the people captured, and bystanders, consent) and `worker_pay` (`model`, `disclosed`, `notes`) for people-based capture; `by`: `people` (the provider's workers or pilots), `contributors` (a crowd capturing on its own initiative), `devices` (sensors, cameras, satellites, dashcams) or `subject` (the person at the place, from a link). |
| `requester` | object | What the provider checks about who is ordering: `checks`, `unknown`. Matters whenever a person is sent to a place. |
| `expires` | string, date | Recommended. After this date the card is stale and brokers skip it; absent means the broker applies its own maximum age. At most a year after `updated` (a validator checks this; the schema cannot). |
| `providers` | array | Who does what, in STAC's roles: each `name`, `roles` (`producer`, `licensor`, `processor`, `host`, `reseller`), `url`. A reseller card names the producers it sells. |
| `adapter` | string | Name of the reference-library adapter, if any. |
| `sources` | array | Each: `claim` (what the source supports) and `url`, both required. Required, non-empty, on `unclaimed` cards. |
| `attestations` | array | Each: `verifier`, `scope` (which guarantee or claim was checked), `url` (all required); `result` (string), `date` (date) optional. |
| `protocol` | object | The provider speaks the wire protocol: `versions`, `endpoint` (both required), `mcp`, `jwks_uri`, `quote`, `public_quotes`, `webhooks`, `cancel`, `operator`, `sandbox`. Section 14.2; schema `protocol/common.json#/$defs/protocol_block`. |
| `provenance` | object | Optional: what tracks the result after capture. `custody` (a record of who held it, with which hash, and when) and `receipt` (a signed statement over what was delivered), each an entry of Section 3.1. An absent entry counts as not stated. |

### 2.3 Scope

Only providers that deliver **new** observations are in scope. A card with empty `delivery.modes` (archive-only) or `status: defunct` MUST NOT be matched.

`pricing.from_usd: 0` means "price not published", not "free": a price ceiling checked against it is a violation, and brokers show it as "quote". Whether a provider is free is stated only by `access.cost`: it is **free to use** when `access.cost` is `free` or `free with key`; `freemium` is not free to use. A free feed therefore says `access.cost: free` and, because it has no price to publish, `from_usd: 0`; a broker MUST NOT read `from_usd: 0` as free on its own.

### 2.4 Discovery

A provider publishes its own card at `https://<provider-domain>/.well-known/irlpls`. The resource is one card, or `{"cards":[...]}` when a host publishes several (a marketplace); media type `application/json`; HTTPS; no redirects; `Cache-Control: public` with `max-age` of at least 60 seconds. `<provider-domain>` is the host named in the card's `provider_url` (the exact host: `www.example.com` and `example.com` are different domains, and a card is `provider-maintained` only when fetched from the host in its own `provider_url`). A provider that also runs the Universal Commerce Protocol lists the capability `com.irlpls.capture.card` in its `/.well-known/ucp` profile, with `config.card` set to that URL. Every card not fetched that way is `unclaimed` and MUST cite sources. A local file claiming `provider-maintained` is checked against its domain only when fetched by URL.

A broker MUST skip a card past its `expires` date and MUST NOT accept a card for a known `id` from another domain **if it keeps an id-to-domain list**. The list is a reviewed JSON object mapping each provider `id` to its canonical host (`{"skyhop":"skyhop.example"}`); keeping one is a SHOULD for a broker, and the MUST binds every broker that claims the "domain-bound" conformance class (Section 8). The reference implementation does not yet keep such a list (Section 11). A broker fetches cards and registries only over HTTPS from public hosts (Section 12), with a response size limit and a timeout, and treats a changed `api`, `payment` or `access` field as needing re-approval before it spends.

**Well-known URI registration (RFC 8615), to be requested from IANA.** URI suffix: `irlpls`. Change controller: irlpls (hello@irlpls.com until a body exists). Specification: this document, Section 2.4. Status: provisional.

### 2.5 Rate limits (optional)

A card MAY state the provider's published rate limit and fair-use terms in `access.rate_limit`, for the tier a keyless or default caller gets:

| Member | Type | Meaning |
|---|---|---|
| `requests`, `per_seconds` | integers ≥ 1, together | `requests` allowed per window of `per_seconds` (60 per minute, 3600 per hour, 86400 per day). |
| `scope` | `per-key`, `per-ip`, `per-account`, `global`, `unknown` | What the provider counts against. |
| `burst` | integer ≥ 1 | Requests allowed back to back, when stated. |
| `batch_max` | integer ≥ 1 | Most items (sites, stations, ids) one request may name, when stated. |
| `cache_seconds` | integer ≥ 1 | How long one reading stays current: the provider's stated update interval. |
| `notes` | string | Anything else the provider asks (cache, poll no faster than the data changes, send a contact). |
| `source_url` | https URL | Where the provider states it. Required whenever a number is given. |
| `with_key` | object, same members | The limit when the caller sends their own key, if different. |

Only stated numbers go in, each with its source; an estimate never does. An absent `rate_limit` (or member) means "not published", not "unlimited". `api.rate_limits` keeps the provider's own wording.

Because irlpls has no hosted API, the requester's process calls each provider directly, so its limits apply per requester. A broker SHOULD pace its calls to each provider under the stated limit, and under a conservative default where none is stated (the reference library: 1 request per second, burst 5); SHOULD honour `429` and `503` with `Retry-After`, with a capped backoff, pausing that provider for every caller in the process; SHOULD reuse an identical request's answer within `cache_seconds`; SHOULD combine many asks to one provider into as few requests as its API allows (within `batch_max`); and MUST NOT drop asks silently: what a limit leaves unsent is returned as still queued, with a warning naming the limit. Requests SHOULD identify the software and a contact in `User-Agent`.

## 3. Guarantees

*In short: seven questions about where a result came from, each answered on its own as evidenced, claimed or none (shown to people as Evidenced, Claimed or Not stated). They are the card fields behind the eight provenance links that people see (Real person, Captured locally, Genuine device, Sealed capture, Known timestamp, Tracked in transit, Independently verified, Delivered to you; Section 14.5 and the glossary give the mapping). The glossary names each link and what it cannot show.*

`verification` holds exactly seven guarantees. Each is reported separately; a broker MUST NOT combine them into a single score, label or ordering of providers (see Section 2). A guarantee key other than these seven is invalid, and extensions use `x-` members elsewhere in the card, not new guarantee keys.

| Key | Question |
|---|---|
| `human` | Did a person whose identity was checked at enrolment, with documented re-verification, do this? |
| `application` | Was it captured with an app or device that passed a server-verified attestation? |
| `camera` | Was it signed by a conforming capture product on genuine hardware? |
| `time` | Did it exist no later than the time it states? |
| `location` | Does the capture carry a position that was authenticated, not just reported? |
| `content` | Is it unchanged since signing (integrity), and does it show what it claims (depiction)? |
| `corroboration` | Do independent sources confirm it? |

**Shown as.** Sites and agents label the guarantees for people. The labels map to the keys one to one, and a display MUST NOT merge them:

| Shown as | Key |
|---|---|
| Person | `human` |
| App | `application` |
| Camera | `camera` |
| Time | `time` |
| Place | `location` |
| Content | `content` |
| Cross-check | `corroboration` |

Each entry is an object:

| Field | Type | Meaning |
|---|---|---|
| `level` | `evidenced` \| `claimed` \| `none` | Required. |
| `unknown` | string | Required. What remains uncertain, in plain words. |
| `method` | string | How the provider supports the guarantee. |
| `evidence_url` | string | Public proof that anyone can check. |
| `standard` | `c2pa` | The provenance standard that makes the guarantee checkable. |
| `assurance_level` | integer | C2PA conformance assurance level of the signing product. |
| `attestation_methods` | array of strings | C2PA attestation strings, e.g. `Android_KeyAttestation`, `Apple_AppAttest`. |
| `conforming_product` | string | The product's entry on the C2PA Conforming Products List. |

Level semantics:

- **`evidenced`**: the provider documents its method and publishes proof anyone can check. The entry MUST include `method` and `evidence_url` (the schema enforces this). The `evidence_url` MUST be an `https` URL on a public host (not a loopback, private, link-local or other RFC 6890 special-purpose address), reachable without a login. The schema checks only the `https://` prefix; host class and reachability are checked by validators and brokers, which reject any other. A broker or verifier SHOULD record the content hash and retrieval date of what it finds there. `evidenced` means "checkable by anyone", not "true": no level means a capture is authentic, unmodified or not AI-generated, and no display may say so.
- **`claimed`**: the provider states a method but publishes no checkable proof. The entry SHOULD include `method`.
- **`none`**: not offered, or nothing stated.

**C2PA.** For `camera`, `application` and `time`, the following counts as `evidenced` evidence:
- a C2PA manifest that validates as Trusted against the C2PA Trust List;
- from a product on the C2PA Conforming Products List;
- with an RFC 3161 time-stamp from an authority on the C2PA TSA Trust List.

`time` evidenced this way means "signed no later than". `location` stays `claimed` unless the capture device itself signs it. A manifest trusted only through the frozen Interim Trust List is `claimed`. `human` needs an identity assertion beyond C2PA, and `corroboration` is outside it.

**What each guarantee can honestly say.** An `unknown` entry states what the method does not cover.

- **`camera`**: signed by a conforming capture product on genuine hardware. It does not detect a re-photographed screen or an exploit of the signing path; the card's `unknown` says so unless a named method addresses each. A card that cites C2PA MUST state how a revoked signer is handled, and validators SHOULD check revocation.
- **`application`**: `evidenced` needs a server-verified attestation (for example an Android Key Attestation chain to a current Google root and not revoked, or Apple App Attest), with the app version in `method`. A display SHOULD show the date it was last verified, because attestations lapse on revocation. The provider SHOULD state its policy on rooted or jailbroken devices.
- **`human`**: identity was checked at enrolment, with documented re-verification (per task or at least monthly). It is never "this person did this work": an account can be rented or shared after enrolment, and `unknown` says so. `evidenced` needs a named verification vendor, the standard it was tested against, and an independent audit or test report at `evidence_url`; a provider's own marketing page is `claimed`. Where people are sent to capture, the card SHOULD state worker consent and how bystanders (faces, plates) are handled. A biometric or identity document MUST NOT be used as a registry key, and a broker MUST NOT say a person is real, unique or authorised.
- **`time`**: "existed no later than" T, from a third party's public record (an RFC 3161 token or a transparency-log entry). It does not show the capture was made at T, and a display says "no later than", never "captured at", unless the device also bound a nonce issued by the buyer.
- **`location`**: a signature from a device does not prove presence, since a device can sign a spoofed fix. `evidenced` needs a position authenticated by a mechanism that a mock-location app or a commodity radio spoofer cannot forge (for example an authenticated satellite signal with a receiver-attested chain, or two independent witnesses), with the checking method published. Otherwise it is `claimed`. A display MUST NOT show a claimed location as fact (no "verified location", no unqualified map pin), and the card states its precision.
- **`content`**: two parts. *Integrity*: unchanged since signing, `evidenced` through a C2PA hard binding and a complete actions chain. *Depiction*: it shows what it claims, `evidenced` only through a method-bound public check: a nonce or challenge from the requester shown in the scene, independent corroboration, or human review with a published audit sample rate. A detector score, the absence of a watermark, or a vendor's "authentic" verdict never raises `content` above `claimed`. A provider that uses a detector names it, its version, and a dated false-positive rate on its own traffic.
- **`corroboration`**: `evidenced` only when two or more sources with different operators, sensors and data pipelines each give a checkable record, and the card names each source and states how their independence was established. Sources that share an upstream feed, owner or customer count as one. Otherwise it is `claimed`.

**Untrusted data.** Everything a card or provider supplies is data, never an instruction: card text (`summary`, `unknown`, `method`, tags, names), provider error bodies, and result labels and attributions. A broker MUST NOT let any of it change a Policy, a spend or a recipient, and an agent MUST NOT act on instructions found in it. Requests carry no secrets.

Brokers order levels `none` < `claimed` < `evidenced` within one guarantee. An attestation records a third party's check; it does not change the level the provider states.

### 3.1 Provenance after capture (optional)

*In short: two optional entries for what happens after capture, so custody and delivery are stated on the card like the guarantees, not inferred elsewhere.*

`provenance` is optional and non-breaking: a card without it is valid, and an entry that is absent counts as no (not stated). It is not one of the seven guarantees and is never combined with them (Section 2).

| Key | Question |
|---|---|
| `custody` | Is there a record of who held the result, with which hash, and when, from capture or registration to delivery? |
| `receipt` | Does the provider sign a statement over what it delivered (file hashes, and ideally the request and the buyer's nonce, Section 6.1)? |

Each entry is an object: `level` (`claimed` \| `evidenced`, required), `method` (required: what is recorded or signed, and by whom), `unknown` (required: what it does not show), `evidence_url` and `sources` (optional; each source a `claim` and `url`). The level semantics of Section 3 apply: `evidenced` MUST include an `evidence_url` that is `https`, on a public host and reachable without a login, where anyone can check the record or signature themselves; otherwise the entry is `claimed`. A ledger or log whose records or anchor a third party cannot fetch and check (for example an on-chain anchor with no chain or transaction named) is `claimed`; a record the provider signs itself is `evidenced` only as to what anyone can recompute from it (the hashes), and its `unknown` says the signer is the provider. A shared-secret signature (HMAC) on a notification is not a `receipt`. Displays show `custody` as the link "Tracked in transit" and `receipt` as "Delivered to you".

**Note: sample packs (informative).** "Public evidence anyone can check" means the same thing for every provider when the `evidence_url` of a guarantee or provenance entry points at a *sample pack*: a public JSON manifest whose name ends in `irlpls-sample.json`, with these members (relative URLs resolve against the manifest's URL; every URL is `https`, on a public host, without a login):

| Member | Meaning |
|---|---|
| `irlpls_sample_pack` | The standard version, e.g. `2026-10-26`. |
| `type` | The proof type: `jws-receipt`, `jwks`, `rfc3161`, `rekor`, `c2pa`, `merkle`, `x509-signature` or `detached-signature`. |
| `stage` | The entry it supports, e.g. `receipt` or `time`. |
| `artefact` | A sample artefact: a delivered file, or the bytes that were signed, time-stamped or logged. |
| `proof` | Its proof: a receipt, a time-stamp token, a log entry with its inclusion proof, a Merkle proof, a signature. |
| `trust_anchor` | The key or trust anchor: the JWK Set (for receipts it MUST be the card's `jwks_uri` when the card has one), a public key, the TSA or log certificate or key, or the CA. |
| `params` | What a verifier needs and cannot guess, e.g. for `merkle` the `hash` (`keccak256`, `sha256`), the `pair` rule (`sorted`, `ordered`), the `odd`-node rule (`explicit`, `duplicate`, `promote`) and the `leaf` encoding. |
| `readme` | How to verify it by hand, step by step, with a command that uses no irlpls code. |
| `verify` | Optional: that command on one line. |

A sample pack shows that the mechanism works on one public sample. It does not show that every delivery is covered, and a verifier MUST NOT raise a level because a pack verifies. The reference checker (`python -m irlpls.conformance <card> --evidence`) runs one verifier per type, reports each entry as verified, failed or not checkable with its reasons, and records the hash and retrieval time of everything it read. A validator warns when an `evidenced` entry's `evidence_url` is not a sample pack.

## 4. Request

*In short: say what you need in a sentence; everything else is an optional hint.*

A Request is natural language first. Only `need` is required; every other field is a hint the requester already knows.

| Field | Type | Meaning |
|---|---|---|
| `need` | string, required | What to observe or capture. |
| `why` | string | The decision or purpose it serves. Lets a broker propose substitutes and set tolerances. |
| `place` | string | Place name or address. |
| `lat`, `lon` | number | Coordinates (-90..90, -180..180). |
| `country_code` | string | Two uppercase letters: an ISO 3166-1 alpha-2 country code. |
| `within_minutes` | integer | Delivery deadline. |
| `quantity` | integer, ≥ 1, default 1 | Number of items. |
| `mode` | `live` \| `commission` | Usually inferred by the broker. When set it is a requirement: the broker MUST NOT plan another route. |
| `terms` | array of strings | What the provider must be able to do. |
| `modalities` | array of modality enums | Requested formats (Section 2.1). |
| `require` | array of guarantee keys | Guarantees whose proof the requester needs. |
| `max_price_usd` | number | Budget hint. Affects fit; it is not a policy limit. |
| `policy` | Policy | Inline policy (Section 5). A broker MUST apply it; a policy passed separately takes precedence. |
| `targets_person` | boolean | The request watches, follows or photographs a specific person. Set by the requester or detected in the need. |

A broker MUST add `time`, `location` and `content` to `require`. A broker SHOULD NOT require its own model: the requester's model writes the need and answers questions. Without `mode`, the broker infers it (Section 7); the reference parser defaults to `commission`.

## 5. Policy

*In short: the requester's owner sets the evidence, rights and spend they accept, once. What a card leaves unstated counts as no.*

A Policy is set once by the requester's owner and evaluated against each card. All fields are optional.

| Field | Type | Meaning |
|---|---|---|
| `require` | object: guarantee key → `claimed` \| `evidenced` | Minimum level per guarantee. |
| `max_unit_price_usd` | number | Ceiling on the card's `pricing.from_usd`. |
| `max_order_usd` | number | Ceiling on a made-to-order request's quoted total. |
| `free_only` | boolean, default false | Only providers whose card says they are free to use. |
| `need_ai_training` | boolean | Card must state AI training is allowed. |
| `need_resale` | boolean | Card must state resale is allowed. |
| `allow` | array of card ids | Only these providers. |
| `deny` | array of card ids | Never these providers. |
| `include_unclaimed` | boolean, default true | Whether cards not confirmed by the provider may be used. |
| `auto_submit` | boolean, default false | Standing mandate: the broker may confirm orders within `max_order_usd` without asking. Without `max_order_usd` it has no effect. |
| `require_c2pa` | boolean, default false | Satisfied only when the card's `camera` has level `evidenced` and `standard: c2pa`. |
| `min_assurance_level` | integer | The `camera` entry's `assurance_level` must be at least this. On its own (without `require_c2pa`) it still requires a stated `assurance_level`. |
| `allow_person_targeting` | boolean, default false | A request with `targets_person` may go to providers whose `capture.by` is `people` or `contributors`. |

Rules:

1. A broker MUST evaluate a Policy only against what cards state. An unstated right, an unpublished price checked against a ceiling, or a guarantee below the required level is a **violation**, never a pass.
2. A broker MUST NOT send an order that violates the Policy. It MUST report the violations instead, in the Job, without raising: a chosen option that violates the Policy ends the Job `failed`; a quote over `max_order_usd` (or with no total to check) leaves it `quoted`, marked blocked.
3. Without a standing mandate, a broker MUST stop every made-to-order request at a quote for a person to approve.
4. A broker MUST NOT send a request with `targets_person` to a provider whose `capture.by` is `people` or `contributors` unless the Policy sets `allow_person_targeting`. A standing mandate SHOULD NOT cover such requests.
5. A broker MUST NOT use a provider whose card says `access.automation_allowed: false`.
6. An open payment mandate (AP2 `amount_range`, `allowed_payees`) MAY carry the Policy's money limits to the payment network; rights, proof and denials stay broker checks.

## 6. Job lifecycle

*In short: a Job moves from question to plan to result. Live routes deliver at once; made-to-order requests stop at a quote until someone says yes.*

```
clarifying → proposed
proposed   → delivered
proposed   → quoted
proposed   → submitted
quoted     → submitted
submitted  → in_flight | delivered | failed
in_flight  → in_flight | delivered | failed
proposed   → failed
quoted     → failed
```

To place an order a broker MUST have an idempotency key, from the requester or derived from the Job for a standing mandate, and MUST pass it to the provider when the provider accepts one. A retry with the same key never places a second order.

A live route goes from `proposed` straight to `delivered`. A made-to-order (`commission`) Job stops at `quoted` unless a person confirms it or a standing mandate covers it; then it moves to `submitted`. After that, each status poll maps the provider's own status to `in_flight`, `delivered` or `failed`. A broker MUST NOT invent a provider status: a status it cannot map (or none at all) keeps the Job `in_flight`, and the provider's status string is reported as sent (`provider_status`).

| State | Meaning |
|---|---|
| `clarifying` | At least one `blocking` question is open. |
| `proposed` | A ranked `plan` exists; nothing has been sent. |
| `quoted` | A made-to-order request was priced (dry run) but not placed, or was blocked by the Policy. |
| `submitted` | The provider accepted an order. |
| `in_flight` | The provider reports the order in progress, or its status could not be mapped. |
| `delivered` | `results` are present. For a made-to-order Job, the provider reports the order delivered; if its status carries no items, the single result points to the provider's order (`kind: order`). |
| `failed` | No policy-compliant option, the chosen option violates the Policy, or the provider reports the order failed, expired or cancelled. A chosen option with no callable adapter is not a failure: the plan marks it `by_hand` (Section 7). |

Job fields (required: `id`, `state`, `need`, `plan`):

- `questions[]`: `field`, `ask`, `blocking` (required); `why`. A broker SHOULD ask only what changes the provider, the price or the feasibility.
- `plan[]`: `card_id`, `route`, `fit` (integer 0–100 or null; this request only), all required; `gaps` (strings), `policy` (violations; empty means compliant), `proof` (the card's levels for `time`, `location`, `content`), `name`, `price` (the card's `from_usd`, null when not published), `price_unit`, `speed` (the card's `delivery.speed`), `sdk` (reference library: `callable`, `needs_key`, `needs_wallet`, `env`), `continue_url` (where a person goes for what an agent can't do: sign up, get a key, ask for a quote), `prepared_call` and `prepared_calls` (reference library: the provider's own API call filled in from the ask, per operation `read`, `quote`, `order`, `status`; credentials only named, never included; `safe_to_run_without_approval` is false for `quote` and `order`).
- `ruled_out[]`: `card_id`, `name`, `reason`, `label`, `why`, `reasons` (every code that applies), `continue_url` (when an account or key is missing). Codes, best first: `policy:<rule>`, `terms` (the provider's terms ban agents), `outside_coverage`, `too_slow`, `no_match`, `needs_key`.
- `request` (the structured request the broker derived; besides the Request fields it may carry `specifics`, `regions`, `state` and `default_proof`, see Section 7), `why`, `policy` (the Policy applied), `choice`, `quote` (the dry run; its `price` `{amount, currency, firm}` is firm only in USD, and a price in any other currency stays indicative, unconverted, and fails every USD ceiling), `provider_response`, `status` (the provider's last status response), `provider_status` (its status string, unmapped), `approvals` (a placed order's second, paid step waiting for a person's explicit, per-order approval, never covered by a standing mandate), `log`.
- `results[]`: `provider`, `kind`, `phenomenon_time` (when the observed thing happened; date-time or null), all required. Optional:
  - `result_time` (when the provider produced the result), `retrieved_at`
  - `lat`, `lon`, `value`, `unit`, `label`, `url`
  - `sensor.kind` (`device`, `human` or `software`; SOSA counts people as sensors)
  - `provider_proof` (the card's levels at delivery)
  - `c2pa` (validation state of delivered media: `trusted`, `valid`, `well-formed`, `absent`)
  - `receipt` (Section 6.1)

  Geospatial results MAY embed a STAC Item.

### 6.1 Capture receipts

*In short: an optional signed delivery that binds the files, the time and place, and the buyer's request together. No listed provider issues an irlpls receipt (this section and Section 14.7) yet; three cards claim to sign deliveries their own way (Human For AI, Execution Market, Satellogic: `provenance.receipt`, level `claimed`).*

A provider MAY sign each delivered item with a key published at its card's `protocol.jwks_uri` (a JWK Set). Section 14.7 fixes the wire form, the allowed algorithms, the key rules and every check a verifier makes. The receipt is a detached JWS (RFC 7515) over the RFC 8785-canonical JSON of:
- the SHA-256, media type and size of the item's media file (or of its value document, for a reading);
- the capture time and place as delivered, and how each was measured;
- the SHA-256 of the Request the provider priced for the order;
- the nonce the buyer sent with the order.

A receipt proves that the key holder made these claims and that they have not changed since; it does not prove the claims are true. Verifiers MUST follow Section 14.7: `alg` equal to the key's JWK `alg` (ES256 or EdDSA), `jwk`, `jku` and `x5u` headers ignored, and the key taken only from the card's `jwks_uri`. A receipt is `evidenced` as to time only when its hash is covered by an RFC 3161 token or a public transparency-log entry showing it existed before a stated time. Providers retire a key by publishing its `not_after` date-time and never deleting it. It raises a guarantee to `evidenced` only when it ties the claim to something independent, for example C2PA, an RFC 3161 time-stamp, the nonce visible in the capture, or a second provider's receipt. Verifiers SHOULD archive the key set, the log inclusion proof and the time-stamp token with each receipt, since keys rotate, and a display shows the checks it ran (signature valid, key listed, proof present) beside the claim.

Unless the Request sets `mode`, the requester does not choose the route; a set `mode` is a requirement. The broker uses `live` when the request is about now and the card has a live mode; otherwise `commission` if the card offers it; otherwise `live`. A card with neither is dropped from the plan.

## 7. Matching (reference implementation, informative)

*In short: fit ranks options for one request. It is never a score for a provider.*

This section is informative: `fit` is broker-defined, and a different broker may compute it differently. `fit` is a per-request ranking aid. It is comparable only within one Job, is never stored on a card or shown as a property of a provider, and is never an overall trust score. `fit: null` means no fit was computed for that option (for example, a Policy violation ruled it out before matching). The reference computation is in `irlpls/match.py` (mirrored in `src/match.js`).

**Parsing** (`irlpls/need.py`). Without a model, the parser extracts `terms`, `modalities`, `require` (always including `time`, `location`, `content`), deadline, quantity, budget and place. Place names, addresses, states and countries set coverage and are not terms. A term is a *specific* (weight 0.5) if it follows "photos of" / "clips of" or a spot word ("at", "near", ...), or if no card uses it. `mode` is `live` for "now"-type words or a deadline of 60 minutes or less, and `commission` for quantities above 1 or dataset words. The default is `commission`.

**Description match `d`** (0–1). Each term weighs 1 (specifics 0.5) and counts if the card's name, summary, tags, tasks or modalities mention it or a mapped concept. A *custom-capture* card (has the `commission` mode, and its tasks mention "custom", "upload" or "submission") offering every requested modality earns 0.75 of the weight for specifics it does not mention, except imaging-sensor terms (`optical`, `radar`, `thermal`, `allweather`): a provider can take a custom task but cannot change its sensor. Each requested modality weighs 1 and counts only if the card lists it and either a term matched, the card is custom-capture, or there are no terms.

**Guarantees `g`** (0–1). Mean over the guarantees in this Request's `require` only (never all seven): `evidenced` 1, `claimed` 0.5, `none` 0.

**Coverage `c`** (0–1). Mean of the available factors:
- place: 1 if the card covers the country (and US state), 0 if it clearly does not, 0.5 if unclear. `coverage.areas` decides when present (`*` covers everywhere); otherwise regions naming "Global" or "Worldwide" cover everywhere;
- timing: for `live`, 1 if the card is live or delivers in minutes, 0.4 for hours, 0 otherwise. For `commission`, the base is 1 if the card offers `commission` (0.75 if live only). The base is kept if the stated speed meets the deadline or there is no deadline; it is multiplied by 0.6 if the speed is `unknown`, and by 0.25 if the speed misses the deadline;
- price: if `max_price_usd` is set and the card publishes a nonzero `from_usd`, 1 if within budget, else 0.

**Total.**

```
s   = (0.4·1 + 0.4·g + 0.2·c) / W
fit = round(100 · s · d²)
```

`W` is the sum of the weights of the parts present; a part that cannot be computed (no terms, no required guarantees, no coverage factor) is left out of both numerator and `W`, and without a description part there is no `d²` gate. The description enters the weighted sum as 1; it acts only through the `d²` gate, so proof and coverage cannot rescue a provider that does not do what was asked. Fit is 0 when `d < 0.35` or the card does not cover the place. Cards with fit 0 are dropped. For a `live` request, a card with no `live` mode whose speed is not `minutes` has its fit multiplied by 0.85, so a source that can answer now ranks above one that can't.

**Ranking.** By fit, then cards with `access.cost: free` first (free and keyless, so callable at once; a narrower test than "free to use" in Section 2.3, on purpose), then, as a last tiebreak only, by the proof levels of the guarantees in `require` (sum of `evidenced` 2, `claimed` 1). That sum is never shown, stored or published, and it never outranks fit. After it, a card published by its provider (`provider-maintained`) goes before a compiled one; publishing adds no fit, and placement is never paid for. The broker's default choice is the best policy-compliant option with a callable adapter when one is within 15 fit points of the top (10 on a live route); otherwise the top option wins even without an adapter, and the plan marks it `by_hand` (a message, the provider's docs and its documented endpoint: the person orders it from the provider directly). Among callable options, one that needs no API key wins if it is within 5 fit points of the lead (10 on a live route, where an answer now beats a slightly better match that first needs an account). Last, **one-yes readiness** breaks a near-tie within 5 fit points: a provider the person can use with one yes goes before one that needs an account first, and never before a much better fit.

**One-yes readiness** (derived from the card, never stored on it; `irlpls/readiness.py`). `contract` if `access.gate` is `approval`, `contract` or `enterprise`, or the terms do not let an agent act (`access.automation_allowed` or `account.agent_may_act.allowed` is false); else `ready_now` if `access.key_required` is false, or `payment.agent_rails` has `x402` or `mpp` and `account.headless_signup` is true and `account.agent_may_act.allowed` is true; else `one_consent` if `account.agent_may_act.allowed` is true and an `oauth2` method has `cimd` or `dynamic_registration`, or `account.headless_signup` is true; else `account_first`. An unstated field never moves a provider up. A broker shows it in each plan option as a plain line ("One sign-in the first time.", "Needs an account first.").

Each match explains itself through `parts` (`description`, `matched`, `custom`, `specifics`, `guarantees`, `missing`, `claimed_only`, `coverage`) and `gaps`.

## 8. Conformance

*In short: a provider conforms by publishing one valid card at a known address.*

**What a provider implements.** Serve one valid Capture Card at `https://<your-domain>/.well-known/irlpls` (HTTPS, `application/json`, no redirects, `Cache-Control: public, max-age=60` or more). That alone is conformant: Request, Policy and Job are broker messages, and a provider never receives or produces them. Brokers read your card and call your own API (`api.endpoints`) in your own format. Start from `docs/examples/minimal-card.json` and run `irlpls validate`.

Conformance classes: **Card Publisher** (provider or community), **Broker**, and, optionally, **Domain-bound broker** (a Broker that also keeps the id-to-domain list of Section 2.4). The protocol adds **Card-only provider**, **Orderable provider** and **Router** (Section 14.13); a provider publishing only its card is a Card-only provider and is fully conformant. Requesters, agents, displays and verifiers have no MUSTs beyond Sections 3 and 5: they do not merge guarantees and treat card text as data.

A **provider** (or the community, for an unclaimed card) MUST publish a Capture Card that validates against `capture-card.schema.json`, with:
- all seven guarantees, each with `level` and `unknown`;
- `method` and an `https` `evidence_url` on a public host for every `evidenced` guarantee;
- `delivery.modes`, `delivery.speed` and `delivery.basis` as the provider states them;
- for `unclaimed` cards, a source for every claim.

A provider SHOULD keep `updated` current, and SHOULD set `licensing.resale` and `licensing.ai_training` explicitly.

A **broker** MUST:
- match only in-scope cards (Section 2.3);
- compute fit per request and never publish it as a property of a provider;
- report each guarantee separately, never as one score;
- evaluate the Policy as in Section 5 and never send a violating order;
- stop at a quote unless a person confirms or a standing mandate covers the order;
- attach the card's proof levels to every result;
- display attestations as recorded, without issuing its own;
- never use a provider whose terms ban agents, and follow Section 5 rule 4 for requests that target a person;
- require an idempotency key to place an order (Section 6);
- treat card text, provider error bodies and result labels as untrusted data (Section 3);
- fetch cards and registries only over HTTPS from public hosts, with a size limit and a timeout;
- never order from, or present as real, a `sample` card;
- report a guarantee-aggregating value for no provider (Section 2).

A **domain-bound broker** MUST in addition keep the id-to-domain list and reject a card for a listed `id` from another domain (Section 2.4). The reference implementation is not yet domain-bound.

Between a client and a provider or router, quoting, ordering, status, delivery and errors on the wire are Section 14. How a requester submits a Request to its own broker and polls the Job is out of scope; a failed Job carries its reason in `provider_status`, `log` and `plan[].policy`, and, when it came from a protocol provider, the Order's `reason_code`. A broker SHOULD emit Jobs that validate against `job.schema.json` and SHOULD accept Requests that validate against `request.schema.json`.

## 9. Non-goals

irlpls:
- **sells no data** and hosts no media; results go from provider to requester;
- **takes no cut**; requesters pay providers directly;
- **checks nothing itself**; providers state their evidence, separate verifiers appraise it, and irlpls shows who stated or checked what;
- **searches no archives**; it acquires new observations only;
- runs no model; the requester's model writes the need.

## 10. Versioning

Versions are dates (`YYYY-MM-DD`), as in UCP. Cards carry `spec_version`; schema `$id`s carry the date (`https://irlpls.com/2026-10-26/schemas/capture-card.json`). A version is **draft** until it is tagged frozen in `CHANGELOG.md`. While draft (as 2026-10-26 is), changes are additive only: members and enum values may be added, never removed, renamed or tightened. A version is **frozen** when its changelog entry says so, from which point its text and schemas are immutable and any change, even additive, takes a new date. Removing or renaming a field always needs a new date and a migration note here. Consumers MUST ignore unknown members and MUST treat unknown enum values as unsupported (skip the card or option, never guess). Extensions use members named `x-<reverse-dns>` (for example `x-com.example.rating`); they carry no standard meaning. A broker that pins UCP pins a commit, since UCP's version tags move.

**2026-10-26 (from v0.1):**
- `licensing.ai_training` and `resale` change from booleans to `allowed`/`forbidden`.
- `api.auth` changes from an object to an array, and its `oauth` type is renamed `oauth2`.
- `results[].observed_at` is renamed `phenomenon_time`.
- Added:
  - on cards: `capability`, `expires`, `coverage.areas`, `api.mcp`, `api.auth[].cimd`, `payment` (and `payment.agent_rails`), `account`, `capture`, `requester`, `access.automation_allowed` and `access.gate`, the C2PA fields on guarantees, and discovery (Section 2.4);
  - in the Policy: `require_c2pa`, `min_assurance_level` and `allow_person_targeting`;
  - in the Request: `targets_person`;
  - in the Job: idempotency keys, `continue_url`, the `terms` reason, and the result fields `result_time`, `retrieved_at`, `sensor`, `c2pa` and `receipt`.
  - the optional wire protocol (Section 14): the card member `protocol` and the schemas in `schema/protocol/`.

`tools/migrate_v02.py` migrates v0.1 cards.

## 11. Known gaps (2026-10-26)

*In short: what this version does not yet cover, listed so no one has to discover it.*

- **Signing cards** (as A2A signs agent cards) is not yet specified. A provider-maintained card rests on HTTPS from the provider's domain.
- **Receipts** are optional. No listed provider issues an irlpls receipt yet; three cards claim their own signed deliveries (level `claimed`, none checkable against the Section 14.7 rules).
- **The protocol (Section 14) has no provider implementation yet.** The reference adapters are being turned into in-process shims; whether a hosted router may ever place orders is open (Section 14.11 allows it only in the agent's own process).
- **Between a requester and its own broker**, submitting a Request and polling a Job has no wire format; a broker API that is not a router (Section 14) is out of scope.
- **Handing a quote to a provider's UCP or ACP checkout** is not yet specified; `payment.methods` names the rails.
- **Reported by the security review**, not yet fixed in the standard or the reference code:
  - The reference broker keeps no id-to-domain list (Section 2.4), so it is not domain-bound: a lookalike domain can publish a card for a known provider; there is no card hash pinning or change alert.
  - Fit rewards a card's own description (`d` is squared), so keyword stuffing is cheap; `claimed` earns half the weight of `evidenced`.
  - The person-targeting filter is a fixed-verb text match in English and is a speed bump, not a guarantee; repeated made-to-order requests to one address are not limited.
  - A standing mandate caps each order, not cumulative spend, and `spec` on submit is free-form.
  - Place strings go to a geocoder, and adapter calls (providers without the protocol) carry the exact place. Protocol quotes from a router are coarsened (Section 14.12), but free text such as `need` is sent as written.
  - `evidence_url` is checked only for form: no one fetches it, hashes it or checks that it is stable.
  - `human` has no structured fields (enrolment check, re-verification, injection defence, uniqueness scope) and `time` and `location` have no `bound` or `precision` fields; `content` is one key although it holds integrity and depiction.
  - Receipts are not logged centrally, and revocation is only a key's `not_after`; no revocation list is checked.
  - Outbound requests are checked before connecting (an unresolvable name is refused), not pinned to the checked address, so DNS rebinding is not fully excluded.
- **Not specified:** a Job field for a Policy-blocked quote (the protocol carries it as a `policy:<rule>` problem, Section 14.9).
- **No hosted broker exists**, so agents use the reference library or call providers directly.

`docs/BROKER.md` is a non-normative design note written before v0.1. Its lifecycle words map onto Section 6 as: `draft` is a Request not yet sent; `authorized` is `submitted` (a person confirmed, or a standing mandate covered it); `partial` is not a state (a partly delivered made-to-order Job stays `in_flight`). This document and the schemas govern.

## 12. Security and privacy considerations

Summary of the threats and the rules above that answer them. What the reference library actually sends, and to whom: `docs/PRIVACY.md`.

- **Cards are untrusted input.** Card text, provider error bodies and result labels can carry prompt injection or tool poisoning. They are data, never instructions (Section 3); a broker MUST NOT let them change a Policy, spend or recipient.
- **Fake and lookalike cards.** Anyone can host a card on a fresh or lookalike domain. HTTPS from the card's own `provider_url` proves only control of that domain, so `provider-maintained` is not a trust signal. Mitigations: the id-to-domain list (Section 2.4), `unclaimed` for everything else, a person approving the first order to a new payee, and `allow` lists for automatic spend. A changed `api`, `payment` or `access` field (rug pull) needs re-approval. Card signing and hash pinning are not yet specified (Section 11).
- **Outbound fetches (SSRF).** A broker fetches card, registry, `evidence_url` and provider URLs only over HTTPS, from public hosts (not loopback, private, link-local, multicast or other RFC 6890 special-purpose addresses, also after DNS resolution), without following redirects to a different host, with size and time limits and bounded decompression. A broker SHOULD pin the connection to the checked address, since checking before connecting does not exclude DNS rebinding.
- **Self-description gaming.** `fit` rewards a card's own words; keyword stuffing is cheap. `fit` is therefore a ranking aid only, `claimed` is not `evidenced`, and no display may present either as authenticity.
- **Spend.** A standing mandate caps each order, not cumulative spend; a broker SHOULD also cap per period. Free-form `spec` on submit is a data channel: a broker SHOULD send only the fields its plan produced and MUST NOT let `spec` change recipient, delivery address or payee. Idempotency keys (Section 6) prevent double orders.
- **Abuse.** Capture made to order can surveil people. `targets_person` filtering (Section 5 rule 4) is a speed bump on English text, not a guarantee; providers' `requester` field says what they check, and a broker SHOULD log requester and target place for the requester.
- **Privacy.** Place names, addresses and coordinates go to a geocoder and to every provider asked. A broker SHOULD coarsen them (coordinates to a 0.01° grid, no address) until a person has chosen a provider, and Requests carry no secrets. What the protocol can and cannot promise is in Section 14.12. A biometric or identity document MUST NOT be used as a registry key (Section 3).
- **Receipts and keys.** Section 6.1 rules on algorithms, header handling and key sources apply; keys rotate, so archive the key set with each receipt.
- **Evidence.** `evidenced` means checkable by anyone, not true. A verifier SHOULD record the hash and retrieval date of an `evidence_url`, since content at a URL can change.

## 13. References

Normative: BCP 14 (RFC 2119, RFC 8174); RFC 3161 (time-stamp protocol); RFC 3339 (date-times); RFC 6454 (origins); RFC 7515 (JWS); RFC 7517 (JWK); RFC 7518 (JWA: ES256); RFC 8037 (EdDSA in JOSE); RFC 8615 (well-known URIs); RFC 8785 (JSON canonicalization); RFC 9110 (HTTP semantics); RFC 9457 (problem details); RFC 9535 (JSONPath); ISO 3166-1 and -2; ISO 4217 (currency codes); JSON Schema 2020-12; draft-ietf-httpapi-idempotency-key-header-08 (Idempotency-Key); Standard Webhooks (webhook headers and `v1a` signatures).

Informative: RFC 6750 (bearer tokens); RFC 6890 (special-purpose addresses); RFC 7591 (dynamic client registration, deprecated for MCP clients); MCP specification 2026-07-28, authorization: client registration (Client ID Metadata Documents); RFC 9421 (HTTP message signatures); RFC 9728 (protected resource metadata); STAPI; Model Context Protocol; C2PA specification, Conforming Products List and Trust Lists; Universal Commerce Protocol (UCP); AP2; STAC; SOSA/SSN.

## 14. Protocol

*In short: six HTTPS+JSON operations (discover, quote, order, status, deliver, cancel) that let any client order from any provider that implements them, with the same shapes at a provider and at a router. Optional: a card alone stays conformant.*

This section is normative. Its message shapes are fixed by the schemas in `schema/protocol/`, served at `https://irlpls.com/2026-10-26/schemas/protocol/`:

| Schema | `$id` suffix | Defines |
|---|---|---|
| `common.schema.json` | `protocol/common.json` | `money`, `idempotency_key`, `proof_levels`, the eight provenance links, `provenance`, `party`, `receipt`, `receipt_payload`, `jwk` and `jwk_set`, the card's `protocol` block, `question`, `ruled_out`, `code`. |
| `quote.schema.json` | `protocol/quote.json` | Quote request, `constraints`, `offer`, Quote. |
| `order.schema.json` | `protocol/order.json` | Order request (accepted offer, or direct). |
| `status.schema.json` | `protocol/status.json` | The Order resource, its states, `order_list` and the webhook event. |
| `delivery.schema.json` | `protocol/delivery.json` | Delivery, its items and per-item provenance. |
| `problem.schema.json` | `protocol/problem.json` | Every error (RFC 9457 problem details). |

The schemas carry every shape rule this text states; where the two still disagree, this text governs. The background, worked examples and open questions are in `docs/protocol/PROTOCOL-DRAFT.md` (informative).

### 14.1 Roles

| Role | Speaks the protocol as | Notes |
|---|---|---|
| **Provider** | server | Its card's `protocol.endpoint` is its base URL. A provider never receives a Policy. |
| **Router** | server and client | A Broker that quotes on the protocol: it plans against cards, applies the Policy locally and fans quotes out to providers. Its offers point at each provider's own `order_url` and quote. |
| **Shim** | server | Puts the protocol in front of a provider's proprietary API, in process (the reference library), at the provider, or at a disclosed third party (`protocol.operator`). |
| **Client** | client | An agent, or a router acting for one. |

### 14.2 Discovery

`discover` is the card fetch of Section 2.4. A card whose provider speaks the protocol carries the optional member `protocol` (`common.json#/$defs/protocol_block`):

| Member | Type | Meaning |
|---|---|---|
| `versions` | array of dates, required | Protocol versions served, e.g. `["2026-10-26"]`. |
| `endpoint` | `https` URL, required | Base URL, no trailing slash. MUST be on the same registrable domain as `provider_url`, as the Public Suffix List defines it (ICANN and PRIVATE sections; equal hosts always match). |
| `mcp` | `https` URL | A remote MCP server exposing the tools of Section 14.10. |
| `jwks_uri` | `https` URL | JWK Set (`common.json#/$defs/jwk_set`) holding the provider's receipt and webhook keys. MUST be same-origin with `provider_url`. REQUIRED when `webhooks` is true or the provider signs receipts. |
| `quote` | `required` \| `optional` | `required`: every order that could cost anything needs a firm `quote_id`. An order could cost anything unless its total is known to be `"0"` before it is placed (a firm offer of `"0"`, or a live reading the card says is free). |
| `public_quotes` | boolean | Quotes need no credentials. Orders are always authenticated. |
| `webhooks` | boolean | The provider sends webhook events (Section 14.8). |
| `cancel` | boolean | The provider implements cancel. |
| `operator` | party | Set when a shim, not the provider, runs the endpoint: who runs it (`name`, `role: shim`, `url`) and, when it signs, `jwks_uri` (same-origin with `operator.url`) for its own keys. |
| `sandbox` | boolean | The endpoint is a test endpoint: orders placed there never dispatch anyone and never charge. A production endpoint MUST NOT set it. |

Every key in a JWK Set served here has `kid` (unique in the set), `alg` (`ES256` with an EC P-256 key, or `EdDSA` with an OKP Ed25519 key) and `irlpls_use`. irlpls registers two JWK members: `irlpls_use` (`receipt`: verifies receipts only; `webhook`: verifies webhook signatures only, always `EdDSA`) and `not_after` (an RFC 3339 date-time after which nothing the key signs is accepted). Keys retire with `not_after` and are never deleted. A UCP provider publishes these keys at a JWK Set URL; `keys[]` inside `/.well-known/ucp` is not used.

A client MUST NOT use a protocol endpoint from a card that was not fetched from its provider's domain, unless its user accepts the card as `unclaimed` (Section 2.4). Before it orders, a client MUST refetch the card (honouring its `Cache-Control: max-age`) and MUST ask the person again if `endpoint`, `jwks_uri` or `operator` changed since the quote (the rug-pull rule of Section 12).

### 14.3 Operations

| Op | HTTP | Success | Side effects |
|---|---|---|---|
| discover | `GET https://<provider-domain>/.well-known/irlpls` | 200 card | none |
| quote | `POST {endpoint}/quotes` | 201 Quote, `Location: {endpoint}/quotes/{id}` | none: no money, no dispatch |
| (read quote) | `GET {endpoint}/quotes/{id}` | 200 Quote | none |
| order | `POST {endpoint}/orders` with `Idempotency-Key` | 202 Order with `Location` and `Retry-After`; 200 Order when already `delivered` (a live reading) | **may cost money and may dispatch a person** |
| (find orders) | `GET {endpoint}/orders?client_reference=<ref>` | 200 `{orders}` (`status.json#/$defs/order_list`): this client's orders with that reference, newest first | none |
| status | `GET {endpoint}/orders/{id}` | 200 Order, with `Retry-After` while not terminal | none |
| deliver | `GET {endpoint}/orders/{id}/delivery` | 200 Delivery in every state (`items: []` before the first, `complete: false` until `delivered`) | none |
| cancel | `POST {endpoint}/orders/{id}/cancel` with `Idempotency-Key` | 200 Order (the state may still be `in_flight`) | may stop work; refunds follow the provider's terms |

discover, quote, find, status and deliver MUST NOT cost money or dispatch anyone.

**Problem or Order.** A problem from `POST /orders` means no order exists and nothing was charged. Once the provider has created an order, it answers with that Order, and every later failure is `state: failed` with a `reason_code`:

| Situation | Answer | Order exists |
|---|---|---|
| Body, version, `Content-Length` or idempotency fault | `400`, `411`, `413`, `415` or `422` problem | no |
| Credentials, scope, account, payment, terms | `401`, `402` or `403` problem | no |
| Unknown quote or offer; the quote lapsed before the order (`quote_expired`) | `404` or `410` problem | no |
| `quote_required`, `needs_input`, `no_match`, `outside_coverage`, `too_slow`, `over_max_total`, `rights_unavailable`, `proof_unavailable`, `person_targeting_refused` | `422` problem | no |
| Same key still running; rate limit; provider down; any server error | `409`, `429`, `503` or other `5xx` problem; retry with the same key | no |
| Accepted, then the work fails, including a synchronous live reading whose source fails or returns nothing | `200` or `202` Order; then `state: failed` with `undeliverable`, `provider_error`, `rejected`, `expired` (an accepted order whose deadline passed) or `cancelled` | yes |

A server MUST NOT answer `POST /orders` with a `5xx` after creating the order; if it cannot tell, a retry with the same key MUST return that order.

### 14.4 Common rules

- **Transport.** HTTPS only (TLS 1.2 or later; 1.3 SHOULD). Requests and successful responses are `application/json`; errors are `application/problem+json`. A request body MUST carry `Content-Length` (a `Transfer-Encoding` body gets `411 length_required`); a server MAY cap the size (`413 payload_too_large`) and MUST NOT read more than its cap. Numbers MUST be finite and integers within ±2^53; otherwise `400 invalid_request`.
- **Version.** A client MUST send `Irlpls-Version: <date>` on every request. Every response, problems included, carries `Irlpls-Version` with the version used. A request without the header is answered under the newest version the server serves. A version not in `protocol.versions` gets `400 unsupported_version` with `supported_versions`. Versions are draft or frozen as in Section 10, and the unknown-member, unknown-enum and `x-` rules of Section 10 apply to every message.
- **Idempotency.** `POST /orders` and `POST /orders/{id}/cancel` MUST carry an `Idempotency-Key` header (`common.json#/$defs/idempotency_key`: 1–255 printable ASCII characters; clients SHOULD use at least 128 random bits). `POST /quotes` MAY carry one. A server keeps keys for at least 24 hours, in one key space per authenticated principal (the account or subject the credential acts for) across all operations. A request's fingerprint is its method, its path and the RFC 8785 form of its body; an absent body counts as `{}` (a cancel body is `{}` or `{reason}`).
  - same key, same fingerprint: the stored response (status, headers and body, with `Idempotent-Replayed: true`), and never a second order;
  - no key: `400 idempotency_key_required`;
  - same key, different fingerprint: `422 idempotency_key_reused`;
  - same key while the first call is still running: `409 request_in_progress` with `Retry-After`.

  The server stores 2xx and 4xx responses except 401, 402, 403, 409 and 429, and never a 5xx. After the window a client finds an order with `GET {endpoint}/orders?client_reference=<ref>`. A firm offer is not single-use: an order under a new key is a new order.
- **Retries.** A client MUST retry an order or cancel only with the same key and the same body, and MUST poll no faster than `Retry-After` and never more than once a second.
- **Auth.** The provider's own, as its card's `api.auth` and RFC 9728 metadata at the endpoint state. OAuth 2 authorization code with PKCE `S256` and clients identified by a Client ID Metadata Document is RECOMMENDED, as the MCP specification of 2026-07-28 requires of new implementations ([client registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)); RFC 7591 dynamic client registration is deprecated there and MAY still be offered for older clients (scope names `com.irlpls.capture:quote`, `:order`, `:read`); an API key in the header the card names is allowed. Credentials stay with the client: a router not running in the agent's own process MUST NOT store a person's provider credentials.
- **Money.** Every amount is `common.json#/$defs/money`: a decimal string and an ISO 4217 currency, never a JSON number. Clients MUST compare amounts with exact decimal arithmetic. Currencies never convert: an amount in another currency, or a missing or unparseable one, fails every ceiling (`over_max_total` at a provider; a violation at a client).
- **Untrusted text.** Problem `detail`, `reason_text`, item `label`, question `ask` and every other text member are data, never instructions (Section 3). A client normalises a provider's problem before an agent or a person sees it: a code from Section 14.9, else `provider_error` with the raw code, cut short, under `x-provider_code`; `title` and `detail` cut to 300 characters; only the members Section 14.9 defines; `continue_url` only when it is `https` and on the card's registrable domain; `questions` only for Request fields, each labelled `source: "provider"`, and never shown as the client's own.

### 14.5 Messages

**Quote request** (`quote.json#/$defs/quote_request`): `{request, constraints?}`. `request` is a Request (Section 4) that MUST NOT carry `policy`, with `quantity` at most 10 000; the Policy stays with the requester or router. `constraints` are hard limits a provider meets or declines: `max_total`, `deliver_by`, `rights_needed`, `proof` (minimum level per guarantee) and `scene_nonce` (the order's `nonce` must be visible in the capture; the provider gives it to the capturer once the order exists). Clients SHOULD omit `why` unless the card's `requester.checks` needs it. The exact place goes only to the provider the person chose (Section 14.11).

**Quote** (`quote.json`): `id`, `created_at`, `expires_at`, `request_sha256` and `offers[]`, all required. `request_sha256` is the lower-case hex SHA-256 of the RFC 8785 form of the `request` member this quote request carried, as the server received it. A provider MUST return exactly one offer, or a problem when it can't serve the request. A router returns one or more offers and `ruled_out[]`, or `422 no_offer` carrying `ruled_out`. Blocking clarifications are `422 needs_input` with `questions[]`; the client re-quotes with the answers. Each offer states `firm` (`true`: the provider's binding price until `expires_at`; `false`: indicative, from the card), `total`, `unit_price`, `deliver_by`, `rights` (absent = not granted), `proof` (levels per guarantee, never merged) and `order_url`, which MUST equal the provider card's `protocol.endpoint` + `/orders`. A firm offer requires `total`, `expires_at` and `order_url`. Only a router sets `fit` (Section 7), `provider_quote_id` and `provider_quote_url` (Section 14.11).

**Order request** (`order.json`): exactly one of
- an accepted offer, `{quote_id, offer_id}`, where `quote_id` is a quote this server issued (at a provider, the provider's own quote); or
- a direct order, `{request, constraints: {max_total}}`. The provider MUST NOT charge more than `max_total`, and answers `422 quote_required` when its card says `protocol.quote: required` and the order could cost anything. A direct order with `max_total` `"0"` is the one-call path for a free live reading.

Required in both: `nonce`, 22–128 base64url characters carrying at least 128 random bits, echoed in every receipt. Optional: `webhook.url`, `approval` (informative), `client_reference`. Neither `client_reference` nor `webhook.url` may carry personal data.

**Order** (`status.json`): `id` (unguessable), `state`, `created_at`, `updated_at`, `request_sha256` and `links` (`self` and `delivery` always; `cancel`; `continue`), all required; `total`, `progress {ordered, delivered}`, append-only `status_history[]`, `provider_status` (the provider's own word, verbatim) and, for a synchronous live order, an inline `delivery`.
- `request_sha256` is the hash of the request the provider priced for this order: for an accepted offer, the quote's `request_sha256`; for a direct order, the SHA-256 of the RFC 8785 form of the order's `request`. Every receipt carries it. A client MUST check that it equals the hash of the request the client sent to this provider; otherwise the order's receipts cannot verify (`request_mismatch`).
- `total` is the amount the provider will charge for this order. A provider MUST NOT charge more than the accepted offer's `total` or the direct order's `max_total`, in the same currency, and a client MUST check `Order.total` against what was approved.
- `progress.ordered` is the request's `quantity`; `progress.delivered` counts the items delivered so far (a live reading may return more items than `quantity`, one per observed property).
- A failed order MUST carry `reason_code` and SHOULD carry `reason_text`.

**Delivery** (`delivery.json`): `order_id`, `complete`, `items[]`, `provenance` and `relayed_by[]`. `complete` is true exactly when the order is `delivered`. Items extend Job `results[]` (Section 6) with `id`, `url`, `url_expires_at`, `media_type`, `bytes`, `sha256`, `precision_m`, `c2pa`, an optional `stac` Item, optional per-item `provenance` and an optional `receipt`.
- Item ids are unique within an order. A delivery that repeats an id is malformed, and a client MUST NOT show any of its items as verified.
- `url` is the item's media file and nothing else; other links go in `x-` members. An item with `url` MUST carry `sha256`, `media_type` and `bytes`, whatever the media type. The client MUST check `sha256` after download and treat a mismatch as a failed item.

**Provenance** lists the eight links; each link states its own guarantees, each with `level` and `basis`, and no value aggregates guarantees or links (Sections 2 and 3):

| Link | Shown as | Guarantees it may state |
|---|---|---|
| `enrolment` | Real person | `human` |
| `capture` | Captured locally | `camera`, `location` |
| `device_attestation` | Genuine device | `application` |
| `signing` | Sealed capture | `camera`, `location`, `content` |
| `timestamp` | Known timestamp | `time` |
| `custody` | Tracked in transit | none (evidence only; and `relayed_by`) |
| `appraisal` | Independently verified | `corroboration` |
| `delivery` | Delivered to you | none (evidence: the receipt) |

`basis: card` is the card's level for every capture; a shim MUST use it and MUST NOT state a level above the card's. `basis: this_capture` is stated for this item; with level `evidenced` it MUST carry at least one checkable `evidence` entry. A client shows a `this_capture` level beside the card's level, never in place of it, and a broker still attaches `provider_proof` (Section 8), never above the registry's copy of the card.

### 14.6 States

Order states are the Job's words: `submitted`, `in_flight`, `delivered`, `failed`. Transitions: `submitted → in_flight | delivered | failed`; `in_flight → in_flight | delivered | failed`. `delivered` and `failed` are terminal and never change; a follow-up is a new order. Known `reason_code` values for `failed` are `rejected`, `cancelled`, `expired`, `undeliverable` and `provider_error`; an unknown value is unsupported and never re-mapped. A partial delivery keeps the order `in_flight`, grows `progress.delivered`, and makes `/delivery` return `complete: false`. If the order then fails, the items already delivered stay readable, and `total` states what is charged. A shim maps only the statuses its provider documents; an unmapped word stays `in_flight` with `provider_status` verbatim (Section 6).

Cancel is cooperative: it returns the Order, which may still be `in_flight` and later ends `failed` with `reason_code: cancelled`, or `delivered` if it was too late. Cancelling a terminal order returns it unchanged with 200. A provider that cannot stop the work, or whose card says `protocol.cancel: false`, answers a cancel of a non-terminal order with `409 not_cancellable`.

Clients poll `status` as Section 14.4 says (60 seconds by default for made-to-order Jobs), with backoff. A router maps Order states onto its Job verbatim: `clarifying` is a `422 needs_input` quote; `proposed` a Quote with no offer accepted; `quoted` a firm offer held for approval or blocked by the router's own `policy:<rule>`; `submitted`, `in_flight`, `delivered` and `failed` are the Order's `state`.

### 14.7 Receipts

The receipt of Section 6.1, on the wire (`common.json#/$defs/receipt`), is `{jws, payload, tsa_token?, log_entry?}`:
- `jws` is a detached compact JWS (RFC 7515 Appendix F), `header..signature`. The protected header is `{"alg", "kid", "typ": "irlpls-receipt+jws"}`; all three are required, and `crit` is not allowed. The key id lives only in this header; a top-level `kid` member is not part of the format and is ignored.
- The signed payload is the RFC 8785 form of `payload`. Required: `iss`, `order_id`, `item_id`, `nonce`, `request_sha256` (the Order's), `media {sha256, media_type, bytes}`, `phenomenon_time` (the item's, as delivered) and `issued_at`. Optional: `time_method`, `lat`, `lon`, `precision_m`, `place_method`; `lat`, `lon` and `precision_m` appear exactly when the item carries them, with the same values.
- `iss` is the RFC 6454 ASCII serialisation of the signer's origin: `scheme://host[:port]`, lower case, the scheme's default port omitted, no path and no trailing slash. A provider's is the origin of its card's `provider_url`; a shim's is the origin of `protocol.operator.url`.
- `tsa_token` (RFC 3161) and `log_entry` are optional.

`media` binds the delivered content. For an item with `sha256` (a media file), `media` is that file's `sha256`, `media_type` and `bytes`. For a value item (a reading with no file, so no `sha256`), `media` binds the value document: the RFC 8785 form of an object with exactly the members `kind`, `value`, `unit` and `phenomenon_time` (the observed-at time), each copied from the delivered item unchanged and set to `null` when the item omits it, and no other member. Then `media.media_type` is `application/json`, `media.bytes` is the document's length in bytes and `media.sha256` its lower-case hex SHA-256. For example `{"kind":"gage-height","phenomenon_time":"2026-10-28T19:30:00Z","unit":"ft","value":3.4}` is 87 bytes, and its SHA-256 is:

```
383d9858481e67f712e4d74234c637a71a17a8a2f42a3bf87b75fdfaf6340406
```

A verifier MUST:
- accept `alg` `ES256` (MUST support) or `EdDSA` (MAY support) only, and only when it equals the `alg` of the key's JWK;
- ignore `jwk`, `jku` and `x5u`, and take the key named by `kid` only from the provider card's `jwks_uri`, or, for an endpoint with `protocol.operator`, only from `operator.jwks_uri`; the key MUST have `irlpls_use: "receipt"`, and a receipt whose `issued_at` is after its `not_after` fails;
- check that `iss`, `order_id`, `nonce` and `request_sha256` equal its own order's (`iss` compared as a string);
- check that the item as shown is the item as signed: `item_id`; `media` against the downloaded file (or the value document it rebuilds from the item), including `media_type` and `bytes`; `phenomenon_time`, string-equal; and `lat`, `lon` and `precision_m`, present in the payload exactly when on the item, and equal.

Verifiers archive the key set with the receipt. A shim MUST NOT sign as the provider; it MAY sign as itself (`iss` = the operator's origin, keys at `operator.jwks_uri`), which attests custody only and never raises a provider guarantee. A valid receipt shows who asserted what (Section 6.1).

### 14.8 Webhooks (optional)

An order's `webhook.url` receives a thin event (`status.json#/$defs/webhook_event`: `{type: "order.updated", timestamp, data: {order_id, state, order_url}}`) with the Standard Webhooks headers `webhook-id` (the dedupe key, stable across retries), `webhook-timestamp` and `webhook-signature: v1a,<base64 Ed25519 signature over id.timestamp.body>`, plus `irlpls-webhook-kid`, which names the signing key in `jwks_uri` (for a shim, `operator.jwks_uri`). That key MUST have `irlpls_use: "webhook"` and `alg: "EdDSA"`.

The receiver MUST verify the signature over the raw bytes with the named key, MUST reject a `webhook-timestamp` more than 5 minutes from now, MUST drop a `webhook-id` it has seen within the last 10 minutes, and MUST check that `data.order_url` equals `{endpoint}/orders/{data.order_id}` exactly, compared as a string, where `endpoint` is the one it ordered from. It then MUST re-read the order with `GET` before acting: a webhook never changes state by itself. The provider retries with backoff and applies the outbound rules of Section 12 to the URL (HTTPS, public host, no redirects).

### 14.9 Errors

Every non-2xx response is a problem (`problem.json`, RFC 9457) with a `type` URI of the form `…/<version>/problems/` plus the code, under `https://irlpls.com` (every `policy:<rule>` uses `…/problems/policy`), and `title`, `status`, `code` and `resolution` (`recoverable`, `requires_buyer_input`, `requires_buyer_review`, `retry_later`, `unrecoverable`), all required; as relevant, `detail`, `instance`, `continue_url`, `errors[]` (RFC 9535 JSONPath `path`), `questions`, `violations`, `ruled_out`, `supported_versions`, `quote_id`, `order_id` and, from a router, `upstream` (the provider's problem, as data). The HTTP status MUST equal `status`. For a code it does not know, a client acts on `resolution`; with no known `resolution`, on the status class (`429` and `5xx`: retry later; any other `4xx`: do not retry unchanged).

| Code | HTTP | Resolution | Meaning |
|---|---|---|---|
| `invalid_request` | 400 | recoverable | Body fails the schema, or a number is not finite or not within ±2^53; `errors[]` has paths. |
| `unsupported_version` | 400 | recoverable | `Irlpls-Version` not served; `supported_versions`. |
| `idempotency_key_required` | 400 | recoverable | Order or cancel without the header. |
| `unauthorized` | 401 | recoverable | Missing or bad credentials; `WWW-Authenticate` per RFC 6750 and RFC 9728. |
| `payment_required` | 402 | requires_buyer_input | No payment method or balance; `continue_url`. |
| `needs_account` | 403 | requires_buyer_input | A person must sign up, add billing or pass a check; `continue_url` required. (The Job reason `needs_key` maps here.) |
| `insufficient_scope` | 403 | requires_buyer_input | The token lacks a scope such as `:order`. |
| `terms` | 403 | unrecoverable | The provider's terms forbid this use or automated ordering. |
| `not_found` | 404 | unrecoverable | Unknown quote or order, or not this client's. |
| `method_not_allowed` | 405 | unrecoverable | Wrong method for the path; `Allow` header. |
| `not_acceptable` | 406 | unrecoverable | The `Accept` header excludes both `application/json` and `application/problem+json` (no `Accept` accepts anything; media downloads are exempt). The 406 is itself sent as a problem. |
| `quote_expired` | 410 | recoverable | The quote lapsed before an order was placed; re-quote; `quote_id`. |
| `length_required` | 411 | recoverable | No `Content-Length`, or a `Transfer-Encoding` body. |
| `payload_too_large` | 413 | recoverable | The body is over the server's limit. |
| `unsupported_media_type` | 415 | recoverable | The body is not `application/json`. |
| `idempotency_key_reused` | 422 | unrecoverable | Same key, different method, path or body. |
| `quote_required` | 422 | recoverable | A direct order where a firm quote is required. |
| `needs_input` | 422 | requires_buyer_input | Blocking `questions[]`; re-quote with answers. |
| `no_match` | 422 | unrecoverable | This provider does not capture this. |
| `outside_coverage` | 422 | unrecoverable | Place not covered. |
| `too_slow` | 422 | recoverable | Can't meet `deliver_by`. |
| `over_max_total` | 422 | requires_buyer_review | The cheapest possible total exceeds `max_total`, or is in another currency. |
| `rights_unavailable` | 422 | requires_buyer_review | A `rights_needed` right can't be granted. |
| `proof_unavailable` | 422 | requires_buyer_review | A `constraints.proof` level can't be met. |
| `person_targeting_refused` | 422 | unrecoverable | The request watches, follows or photographs a specific person. A provider MAY refuse regardless of the buyer's Policy. |
| `policy:<rule>` | 422 | requires_buyer_review | Router only: the requester's Policy forbids it; `violations[]`. |
| `no_offer` | 422 | requires_buyer_review | Router only: no provider fits; `ruled_out[]`. |
| `request_in_progress` | 409 | retry_later | Same key still being processed. |
| `not_cancellable` | 409 | unrecoverable | The work can no longer be stopped, or the provider does not cancel; `order_id`. |
| `rate_limited` | 429 | retry_later | `Retry-After`. |
| `provider_error` | 500 | retry_later | Unexpected server error; no order was created. |
| `upstream_error` | 502 | retry_later | Router only: a provider failed; its problem in `upstream`. |
| `provider_unavailable` | 503 | retry_later | `Retry-After`. |

**Client-local codes.** A client library or an in-process router produces these; no provider sends them.

| Code | Status | Resolution | Meaning |
|---|---|---|---|
| `no_protocol` | 422 | unrecoverable | The chosen offer's provider does not speak the protocol (an indicative offer); order it through the provider's own API or the reference library. |
| `price_changed` | 422 | requires_buyer_review | A re-quote came back higher than the total the person approved; nothing was ordered, ask again. |
| `request_mismatch` | 422 | unrecoverable | The Order's `request_sha256` is not the hash of the request the client sent; its receipts cannot verify. |
| `unsafe_endpoint` | 400 | unrecoverable | Refused before any request: an endpoint, key or order URL breaks the discovery rules (Section 14.2) or the outbound-request rules (Section 14.12). |
| `media_mismatch` | 502 | unrecoverable | A downloaded media file's SHA-256 does not match the item's `sha256`; the item is failed. |

`ruled_out[].reasons` uses the same codes, plus the broker-only `needs_key`, so Job reasons (Section 6) and protocol problems share one vocabulary.

### 14.10 MCP binding

Tools map one to one to the operations: `irlpls_discover {domain}`, `irlpls_quote {quote_request, card_id?}` or `{quote_id}`, `irlpls_order {order_request, card_id?}`, `irlpls_status {order_id}`, `irlpls_deliver {order_id}`, `irlpls_cancel {order_id}`. `card_id` is for an in-process router only: quote one provider, or name the provider of a direct order. Each tool also takes `meta: {irlpls_version, idempotency_key?}`, and `idempotency_key` is required for `irlpls_order` and `irlpls_cancel`. A result is the HTTP response body as `structuredContent` (and as text in `content[]`), with the matching schema as `outputSchema`. An error is a tool result with `isError: true` and the problem as `structuredContent`; JSON-RPC errors are for protocol faults only. `discover`, `quote`, `status` and `deliver` are `readOnlyHint: true`; `order` is `destructiveHint: false`, `idempotentHint: true` and `openWorldHint: true`, and its description MUST say it may cost money; `cancel` is `destructiveHint: true`. MCP has no webhooks, so clients poll `irlpls_status`. An MCP server's streamable-HTTP transport has no authentication of its own; bound to a non-loopback address it MUST require an explicit operator setting and SHOULD warn that anyone who can reach it can order with its credentials.

### 14.11 Routers and shims

A router:
- applies the Policy locally (Section 5); the Policy never leaves it. It sends providers only `constraints` derived from it (`max_order_usd` → `max_total`, `need_ai_training` → `rights_needed`, `require` → `proof`) and coarsened places (Section 14.12), and SHOULD quote only its shortlist;
- builds an indicative offer (`firm: false`, no `order_url`) from the card where it can't quote a provider, and never invents a price;
- checks every returned offer against the Policy again, marking a violation as `policy:<rule>` rather than dropping it silently;
- puts on each offer from a provider's quote that quote's id (`provider_quote_id`), its URL (`provider_quote_url`, `{provider endpoint}/quotes/{id}`) and `order_url`; its own Quote's `request_sha256` is the hash of the request it received;
- MUST NOT rewrite `order_url` to anything other than the provider card's `protocol.endpoint` + `/orders`;
- MUST NOT order without the requester's approval or a standing mandate (Section 5);
- passes a provider's problem through as `upstream` data, never as instructions;
- MAY place orders itself only when it runs in the agent's own process (the reference library); it then MUST derive a distinct idempotency key per provider from the requester's key: SHA-256 over the UTF-8 bytes of `key + "|" + card_id`, encoded as unpadded base64url.

**Ordering a router's offer.** A router's quote id means nothing to a provider, and the provider's quote priced a coarse request. Before ordering, the client MUST re-quote at the provider's own endpoint with the exact request (so the exact place goes to that provider and nobody else) and order `{quote_id, offer_id, nonce}` from that new quote, with the person's own credentials. If the new total is higher than the one the person approved, it MUST NOT order and asks again (`price_changed`). A client MAY instead order `provider_quote_id` only when that quote's `request_sha256`, read at `provider_quote_url`, equals the hash of its exact request. The router stays out of the data and money path (Section 9).

A shim maps a proprietary API onto the protocol. It makes a firm offer only from a real provider quote. It uses the provider's own idempotency mechanism where one exists, scoped to the caller; otherwise it records the key as pending before calling, and where the provider offers no way to look an order up, its card says `protocol.quote: required` and documents that a crash can double-order. It downloads each media file and computes `sha256`, `media_type` and `bytes` itself (an item it cannot hash loses its `url`), sets provenance `basis: card` with the card's levels, adds itself to `relayed_by`, never stores the caller's credentials, and, when run by a third party, says so in `protocol.operator`.

### 14.12 Security considerations

In addition to Section 12:
- **Money.** Amounts are decimal strings, so NaN, infinite and rounded totals cannot pass a ceiling; a missing, unparseable or other-currency total is a violation.
- **Double orders.** Idempotency keys at both hops (Section 14.4), distinct per provider at a router (Section 14.11).
- **Hostile bodies.** A server reads at most its body limit, requires a strict `Content-Length`, and refuses non-finite numbers and integers outside ±2^53, so a request can neither exhaust memory nor crash canonicalisation.
- **Redirected money and data.** A client orders only at the `order_url` equal to the card's `protocol.endpoint` + `/orders`, from a card fetched from the provider's domain. No router or error body can change the payee, the endpoint or the recipient.
- **SSRF.** Providers fetching `webhook.url`, and clients fetching cards, media and evidence URLs, MUST use HTTPS to public hosts only (RFC 6890 special-purpose addresses excluded after DNS resolution; a name that does not resolve is refused), MUST NOT follow cross-host redirects, MUST apply size and time limits, and SHOULD pin the connection to the checked address. A client that follows a redirect to another origin MUST drop every credential header.
- **Webhooks are hints.** Verified with the named webhook key, time-boxed, deduplicated, `order_url` matched exactly, then followed by a `GET`; a forged webhook can at worst cause a read.
- **Media.** `sha256` is checked after download; a mismatch is a failed item. Media URLs are short-lived capability URLs and MUST NOT appear in logs that leave the client. A provider serving media from its API origin MUST send `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`, and `Content-Disposition: attachment` for anything but `image/`, `video/` and `audio/` types (`image/svg+xml` included), so delivered content never runs as script on that origin.
- **Receipts.** The algorithm, key and binding rules of Section 14.7. A key marked for webhooks cannot sign a receipt. A receipt proves who claimed what, not that it is true.
- **Privacy: what irlpls can promise.** The Policy never leaves the requester's side, and `why` is sent only when a card's `requester.checks` needs it. A router's quotes never carry the `place` field and round `lat` and `lon` to a 0.01° grid (no jitter, which averages away). The exact place goes only to the provider the person chose, in its own quote and order. Idempotency keys and nonces are random.
- **Privacy: what irlpls cannot promise.** Orders are made with the person's own provider account, so that provider learns who the account holder is, and also sees the IP address, the webhook host and `client_reference` (neither may carry personal data). `need` and other free text are sent as written and may name a place or a person; irlpls cannot strip them reliably, so a requester who wants coarse quotes keeps the address out of `need`. What a provider logs is up to the provider.
- **Person targeting.** A provider MAY refuse any request with `person_targeting_refused`. A router follows Section 5 rule 4, and a standing mandate SHOULD NOT cover such orders.
- **Sandbox.** `protocol.sandbox` is the provider's own statement. A tool that places test orders only against sandbox endpoints still relies on that statement, so the person running it accepts the risk; a production endpoint MUST NOT declare it.
- **Abuse.** Providers answer `429` with `Retry-After`; clients back off and SHOULD cap spend per period as well as per order.

### 14.13 Conformance classes

These extend Section 8. Each class includes the rules of the class it builds on.

- **Card-only provider.** A Card Publisher (Section 8) whose card has no `protocol` block. Nothing in this section applies to it; it is fully conformant to this standard.
- **Orderable provider.** A Card-only provider whose card has a valid `protocol` block, and which implements discover, quote, order, find, status and deliver as in Sections 14.2–14.9, cancel when `protocol.cancel` is true (`409 not_cancellable` otherwise), receipts as in Section 14.7 when it issues them, and webhooks as in Section 14.8 when `protocol.webhooks` is true. In particular it MUST: honour `Irlpls-Version`; enforce the idempotency rules; return exactly one offer per quote, with `order_url` equal to `endpoint` + `/orders`; echo `request_sha256` in every Order; never charge more than the accepted offer or `max_total`; answer `POST /orders` with a problem only when no order exists; use only the four order states, with `reason_code` on `failed` and terminal states never changing; carry `sha256`, `media_type` and `bytes` with every item `url`; and answer every error with a problem whose `status` equals the HTTP status.
- **Router.** A Broker (Section 8) that serves quotes on this protocol and follows Section 14.11. A router that runs shims is responsible for them meeting the Orderable provider rules.

Clients have no MUSTs beyond those stated in Sections 14.2 and 14.4–14.12.

**Checking.** `python -m irlpls.conformance <domain> --protocol` checks an Orderable provider without side effects: discovery, the `protocol` block, version negotiation, a quote and its `request_sha256`, problem shapes for bad requests and unknown ids. It never places an order unless it is also given `--sandbox` **and** the card declares `protocol.sandbox: true`; then it also orders, checks the Order's `request_sha256`, replays the idempotency key, polls status, reads the delivery, checks media hashes and verifies receipts (with the item bindings) against `jwks_uri`.
