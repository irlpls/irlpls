# Publish your Capture Card

For providers of new observations of the physical world: people, drones, satellites, dashcams, sensors.

A **Capture Card** is one JSON file. It says what you capture, where, how fast, at what price, under what rights, how to call you, and, link by link, what you can evidence about where your data came from. Brokers and agents read it directly, so buyers compare you on facts you stated yourself. irlpls takes no cut and sells no data; buyers pay you directly.

A card for your company may already exist. It was compiled from your public pages and is marked `unclaimed`. Your own statement always wins over it.

## 1. Get your card

If a compiled card exists, download it at `/cards/<your-id>.json` and save it as `.well-known/irlpls`. If none exists, copy the closest card and edit it. The format is `schema/capture-card.schema.json`; the field tables are in the [standard](standard.html), Section 2. You can [check your card in a browser](check.html); the reference validator and conformance checker are part of the reference tools.

## 2. Correct it

Settle what only you can, most important first:

- **The eight links**: Real person, Captured locally, Genuine device, Sealed capture, Known timestamp, Tracked in transit, Independently verified, Delivered to you. They are shown from your card's `verification` fields (human, application, camera, time, location, content, corroboration) and its optional `provenance` block; the [standard](standard.html), Section 14.5, gives the mapping. Each is `evidenced` or `claimed`, or `none` (shown as Not stated), with an `unknown` line saying what remains uncertain, in plain words. There is no higher level. Mark a link `evidenced` only with a `method` and a public `evidence_url` that anyone can check: a signing key, a time-stamp authority, an attestation report. Not offering a link is fine; `none` with an honest `unknown` reads better than an unsupported `claimed`.
- **Rights**: `licensing.ai_training` and `licensing.resale` (`allowed` or `forbidden`). Leave one out and it reads "not stated", which brokers treat as not granted.
- **Price**: `pricing.from_usd`. `0` means "not published", not free.
- **Coverage**: `coverage.areas`, as ISO country codes (or `US-CA`-style codes).
- **Automation**: `access.automation_allowed`. `false` means agents will not use you.
- **Who can order**: `requester.checks`, if you send people to places.
- **Sources**: remove any compiled source that is wrong; add your own.

Update `updated`, and set `expires` no more than one year ahead. A card past its `expires` date is skipped.

## 3. Validate

In a browser, with nothing to install: [check your card](check.html). It runs the same rules and says what a browser cannot check.

The validator checks the schema and the rules the schema cannot state: an `evidenced` link has a method and an evidence URL; expiry is within a year; a provider-maintained card comes from its own domain. Run against the compiled card, it also flags anything you state beyond what the compiled card says (higher levels, rights, wider coverage, faster delivery, a lower price, automation). It never overrides your card; it tells a reader where to look.

## 4. Publish at `/.well-known/irlpls`

Serve the file at `https://<your-domain>/.well-known/irlpls` with:

- HTTPS, and no redirects;
- `Content-Type: application/json`;
- `Cache-Control: public, max-age=N`, with N of 60 or more;
- the card's `provider_url` on the same domain (this is what makes it `provider-maintained`).

If you also run the Universal Commerce Protocol, list the capability `com.irlpls.capture.card` in your `/.well-known/ucp` profile, with `config.card` pointing at that URL.

## 5. Check it

The conformance checker makes one read-only request for your card (and one for `/.well-known/ucp` if you list it). It never calls your API. It checks redirects, content type, cache headers, expiry and domain, and exits 0 only when nothing fails.

## 6. Become orderable (optional)

A card alone is fully conformant: agents read it and call your own API. To let any agent quote and order from you the same way it orders from everyone else, speak the wire protocol ([standard](standard.html), Section 14). That makes you an **orderable** provider.

**Implement the six operations**, under one HTTPS base URL on your own domain:

| Operation | HTTP |
|---|---|
| discover | `GET /.well-known/irlpls` (your card) |
| quote | `POST {endpoint}/quotes`: no charge, no dispatch |
| order | `POST {endpoint}/orders` with an `Idempotency-Key` |
| status | `GET {endpoint}/orders/{id}` |
| deliver | `GET {endpoint}/orders/{id}/delivery` |
| cancel | `POST {endpoint}/orders/{id}/cancel` with an `Idempotency-Key` |

The rules that matter most: the same key with the same body returns the first answer and never a second order; one offer per quote; never charge more than the accepted offer or `max_total`; money as decimal strings; order states `submitted`, `in_flight`, `delivered`, `failed`; a `sha256` on every media item; errors as problem details.

**Or run the shim.** The reference provider server handles the protocol, idempotency, receipts and errors, and calls your API through a small plug-in that carries your card and has four methods (quote, start, poll, cancel): `python -m irlpls.protocol.server --fulfiller yourmodule:YourFulfiller --endpoint https://<your-domain>/irlpls --data <dir>`, behind your TLS terminator (`--data` keeps orders and signing keys across restarts). Try it first with `--sim`, as in the quickstart. If someone else runs the shim for you, your card names them in `protocol.operator`, and a shim never signs receipts as you.

**Then add the `protocol` block to your card:** `versions`, `endpoint` (on your domain), and as they apply `jwks_uri` (your signing keys, same origin as `provider_url`), `quote` (`required` if every paid order needs a firm quote), `public_quotes`, `webhooks`, `cancel`. Receipts are optional; if you sign them, publish the key at `jwks_uri` and sign each delivered item.

**Check it** with a sandbox endpoint whose card declares `protocol.sandbox: true` (orders there never dispatch anyone and never charge; a production endpoint must not declare it):

```
python -m irlpls.conformance <domain> --protocol --sandbox
```

Without `--sandbox` it makes no orders: it checks discovery, the `protocol` block, version negotiation, a quote and the error shapes. With it, and only against a card that declares `sandbox`, it also orders, replays the idempotency key, polls status, reads the delivery, checks media hashes and verifies receipts. It speaks HTTPS only; it exits 0 only when nothing fails.

## 7. Make a link evidenced (optional)

Today no listed provider evidences any link: each shows Claimed, Reported (a time only) or Not stated. A link becomes `evidenced` when a stranger can check one public artefact themselves. The cheapest path, about one engineer-week and $0:

1. **Delivered to you (`receipt`).** Sign each delivery with a detached JWS (ES256 or EdDSA, [standard](standard.html) Section 14.7) over the file SHA-256s, the request hash and the buyer's nonce. Publish the keys at your `jwks_uri` and never delete an old one; retire it with `not_after`. The `unknown` line says the signer is you.
2. **Known timestamp (`time`).** Once a day, hash the day's receipts into a Merkle root and get it time-stamped (an RFC 3161 token, for example from FreeTSA) or logged (Sigstore Rekor's public instance). That shows each receipt existed no later than that time, nothing more. Publish your leaf encoding, pair rule and odd-node rule, and proofs that include every step.
3. **Publish a sample pack** and point `evidence_url` at it: one sample artefact, its proof, your key or trust anchor, and a README with a command that checks it without irlpls code (Section 3.1 note). A web page describing your signing is not evidence.
4. **Check it yourself** before you change a level:

```
python -m irlpls.conformance <your-domain> --evidence
```

It fetches every `evidence_url`, runs the verifier for its type and says verified, failed or not checkable, with reasons. It never changes your card. The common failures it catches: samples signed by a key other than the one you publish, a signing certificate that had expired when you signed, and Merkle proofs that leave out the steps where an odd node is paired with itself.

### Sample packs

A sample pack is a small public JSON file (named `*irlpls-sample.json`) that links one sample file, its proof, your key or trust anchor and a README. The [card checker](check.html) only checks a pack's signature against its stated key, in the browser. Nothing else.

Sealed capture (`camera`) is the costly one: C2PA signing with `c2patool` is free, but `evidenced` needs a product on the C2PA Conforming Products List, a signer's certificate that chains to the C2PA Trust List and an RFC 3161 time-stamp from a TSA on the C2PA TSA Trust List (Section 3). A self-made certificate stays `claimed`.

## 8. What happens next

- **Within a day, it is live.** irlpls reads every listed provider's `/.well-known/irlpls` once a day, with one conditional request (an irlpls User-Agent, a 10 second timeout, no redirects followed, at most 256 KB). The next site build, within 30 minutes, shows it. [Check your card](check.html) says when the next read is, and `check.html?d=<your-domain>` shows your status any time.
- **What it must pass.** The schema and the standard's own rules; the card's `id` must be your compiled card's, served from the host in its `provider_url` (or that host's `www.` twin). A subdomain, a lookalike or another registrable domain never counts. Until it passes, your compiled card stays up.
- **Your page.** It shows **Published by <you> from <your domain>, checked <date>**, what an agent says about you (from irlpls's own plan), where you rank on typical asks of your kind, the one change in evidence that would move you up (computed by re-ranking, shown only when it changes a rank), and every field that differs from the compiled card. Buyers see all of it.
- **Your badge.** A static image at `/badge/<your-id>.svg`, rebuilt with the site: `irlpls | Capture Card · checked <date>`. It turns grey when your card expires, cannot be read, stops validating or is withdrawn. No score, no rank, no tracking. Snippet: `<a href="https://irlpls.com/c/<your-id>.html"><img src="https://irlpls.com/badge/<your-id>.svg" alt="<Name> on irlpls"></a>`.
- **When something goes wrong.** Unreachable: your last good copy stays up to 7 days, with a grey badge. Expired or not valid: the compiled card is shown again at once. Not found two days in a row: treated as withdrawn.
- **Keep it current.** Keep `expires` in the future. Update the file when your terms, price or evidence change.
- **New to irlpls?** If no card for you is listed yet, write to hello@irlpls.com with your domain. A new provider is listed after one review, never automatically.
- Want to sign each delivery? The standard defines an optional signed receipt (Sections 6.1 and 14.7). No listed provider issues an irlpls receipt yet; three cards claim to sign deliveries their own way (level `claimed`).

## 9. Ordering

- **Fit is about the ask.** Every request is ranked by fit to that ask alone. Publishing your own card adds no fit.
- **Ties only.** When two providers fit equally, a provider-maintained card goes before a compiled one, and evidence counts before that. That is the only place publishing or evidence changes an order.
- **Never above a search.** Once someone types a request or sets a filter on Find data, they see only their results, ranked by fit. Nothing published is pinned above them.
- **Recently published by providers** is a quiet section at the end of Find data, after the full provider list, and only while someone is browsing (no request, no filter). It lists cards whose provider published them: a card with a link whose evidence verified first, then by the date of the last real change (price, coverage, rights, speed, a link's level or automation; wording and `updated` do not count). A card moves up there at most once every 30 days. The full list stays alphabetical. The landing page's one line naming published providers is the last line of its footer; card pages promote no other provider.
- **No paid placement**, ever. Nothing on irlpls can be bought: not a rank, not a position, not a badge.
- **Traffic.** irlpls logs nothing per request, so it cannot count your readers. Brokers fetch `/.well-known/irlpls` with an irlpls User-Agent; your own logs show them.
