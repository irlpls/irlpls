# irlpls protocol: end-to-end demo

Run 2026-10-28T12:17:07Z with `tools/protocol_demo.py` --fast. Private: everything ran on 127.0.0.1 and nothing was published or ordered from a real provider.

- **USGS data: RECORDED.** The live API was not used; the shim answered from a recording of the live API taken 2026-10-28T08:47Z.
- **Simulated provider:** `irlpls.protocol.server.ProviderServer` + `SimFulfiller`. Its photo is a generated gradient PNG. It never captures anything.
- **Client and router:** `irlpls.protocol.client` (discover, quote, order, status, delivery, receipts) and the `irlpls` broker (`irlpls.request` plans; it never orders).

Rerun: `uv run --with cryptography --with jsonschema python tools/protocol_demo.py` (add `--fast` for no network). Smoke test: `tests/test_protocol_demo.py`.

## What each step proves

| # | Step | Result | What it proves |
|---|------|--------|----------------|
| 1 | Start two providers on localhost | pass | Both servers speak the same six operations over HTTP+JSON; nothing leaves this machine except, in live mode, the shim's own read-only calls to the public USGS API. |
| 2 | Router plans 'river level at Comfort, TX now' | pass | The broker ranks the registry's Capture Cards for the ask and picks a free live feed; it plans, it does not order. |
| 3 | Discover the usgs-water shim | pass | A strict client refuses a protocol endpoint that is not on the provider's own domain (draft section 3); the shim's card is unclaimed and names its operator, so it is used only as an explicitly accepted shim. |
| 4 | Quote the river reading | pass | Quotes cost nothing and dispatch nothing; a shim never invents a price: the offer is the card's (free) and not firm. |
| 5 | Order the river reading | pass | A live order is delivered in the order response itself; the client generated the Idempotency-Key and a nonce. |
| 6 | Deliver: readings with per-stage provenance | pass | Items arrive with ids, hashes and an 8-stage provenance whose levels come from the card ('basis: card'); the shim never raises a level. |
| 7 | Verify the river receipts | pass | Each item carries a detached-JWS receipt signed by the shim operator (iss = the shim's origin), bound to our order, nonce, request hash and item hash. It attests custody only: USGS signed nothing. |
| 8 | Router plans 'photo of the storefront at 12 Elm St' | pass | With the simulator's served card in the registry, the router ranks it first for a storefront photo and records that it speaks the protocol (sdk.protocol = its endpoint). |
| 9 | Discover the simulator | pass | GET /.well-known/irlpls returns the card with its protocol block; the client checks endpoint and key URL are the provider's own origin. |
| 10 | Quote the storefront photo | pass | A firm, priced offer under the buyer's ceiling (max_total 40 USD), with its rights and proof levels stated up front. |
| 11 | Order with an Idempotency-Key | pass | POST /orders carries a client-generated Idempotency-Key and a fresh nonce; the provider accepts (202) and points to the order. |
| 12 | Replay the same order | pass | A retry with the same key and body returns the stored response: the same order, never a second one. The same key with a different body is refused. |
| 13 | Poll status until delivered | pass | Status follows the Job's states (submitted -> in_flight -> delivered) with the provider's own words in provider_status; the client waits as long as Retry-After says. |
| 14 | Deliver the photo | pass | The item comes with a short-lived media URL and its sha256; the client downloads it and checks the hash. |
| 15 | Verify the photo receipt | pass | The receipt is signed by the provider (iss = its origin) with the key from its own jwks_uri, and binds this order, our nonce, the quoted request hash and the media hash. |
| 16 | Tamper with the receipt | pass | Changing any signed field, or presenting the receipt for someone else's order, fails verification. |
| 17 | Refuse a person-targeting ask: 'photo of the woman leaving 12 Elm St every morning' | pass | Asks aimed at a specific person are refused at every layer: the router plans nothing, the provider refuses a request flagged targets_person, and the shim refuses on the text alone. |

## Transcript

Printed by the run as it happened. `[+s]` is seconds since start; each step header carries its UTC time.

```text
== 1. Start two providers on localhost  (12:17:07.483Z)
[+  0.01s] simulated provider  irlpls.protocol.server + SimFulfiller(1 s)  endpoint http://127.0.0.1:58131/irlpls
[+  0.02s] usgs-water shim     irlpls.protocol.shim.serve_adapter                 endpoint http://127.0.0.1:58132/irlpls
[+  0.02s] USGS data source:   RECORDED (fast mode: no network); recording taken 2026-10-28T08:47Z from the live API
[+  0.02s] PASS  both servers listening on 127.0.0.1

== 2. Router plans 'river level at Comfort, TX now'  (12:17:07.500Z)
[+  0.04s] plan: usgs-water (live, fit 73), noaa-tides (live, fit 36)
[+  0.04s] 84 other providers ruled out with a reason each
[+  0.04s] PASS  best: usgs-water via live
[+  0.04s] wire request (no policy, no why, no requester): {"need":"river level at Comfort, TX now","within_minutes":60,"quantity":1,"mode":"live","terms":["river","level"],"modalities":["sensor"],"require":["time","...

== 3. Discover the usgs-water shim  (12:17:07.527Z)
[+  0.05s] card usgs-water status=unclaimed provider_url=https://waterdata.usgs.gov/
[+  0.05s] protocol: endpoint=http://127.0.0.1:58132/irlpls quote=optional operator='irlpls protocol demo (localhost shim)' (role shim)
[+  0.05s] PASS  strict discovery refuses it: endpoint 'http://127.0.0.1:58132/irlpls' is not on the provider's domain ('https://waterdata.usgs.gov/')
[+  0.05s] PASS  accepted as an unclaimed card relayed by a disclosed shim operator

== 4. Quote the river reading  (12:17:07.534Z)
[+  0.06s] quote q_1vgI79msx3bfOhg9 request_sha256=089575c21bfdcc88... expires 2026-10-28T12:32:07Z
[+  0.06s] offer o_1: route=live firm=False total={"amount":"0","currency":"USD"} rights=null
[+  0.06s] PASS  the quote is bound to the exact request sent (RFC 8785 hash)

== 5. Order the river reading  (12:17:07.538Z)
[+  0.06s] Idempotency-Key 24d8329f-a5f2-4846-b215-b5e5f99d8f73  nonce wAgUGWRO2YVGnFMTbJEeyQ
[+  0.06s] order ord_i6cA8kerCdCMAmF8So7LCw: state=delivered total={"amount":"0","currency":"USD"} history=['submitted', 'delivered']
[+  0.06s] PASS  delivered at once (live route)

== 6. Deliver: readings with per-stage provenance  (12:17:07.544Z)
[+  0.07s] itm_1: gage height = 3.27 ft at 2026-10-28T08:35:00Z
[+  0.07s] itm_2: discharge = 78.4 ft^3/s at 2026-10-28T08:35:00Z
[+  0.07s] itm_3: gage height = 5.15 ft at 2026-10-28T08:35:00Z
[+  0.07s] itm_4: river camera = image/jpeg 75 B sha256=7873b2eac7ba... at 2026-10-28T08:41:21Z
[+  0.07s]   stage enrolment          Real person              human=none/card
[+  0.07s]   stage capture            Captured locally         camera=none/card, location=claimed/card
[+  0.07s]   stage device_attestation Genuine device           application=none/card
[+  0.07s]   stage signing            Sealed capture           camera=none/card, location=claimed/card, content=claimed/card
[+  0.07s]   stage timestamp          Known timestamp          time=none/card
[+  0.07s]   stage custody            Tracked in transit       
[+  0.07s]   stage appraisal          Independently verified   corroboration=claimed/card
[+  0.07s]   stage delivery           Delivered to you         
[+  0.07s] relayed_by: [{"name":"irlpls protocol demo (localhost shim)","role":"shim"}]
[+  0.07s] PASS  4 item(s); every card-basis level equals the usgs-water card's

== 7. Verify the river receipts  (12:17:07.548Z)
[+  0.07s] operator keys from http://127.0.0.1:58132/.well-known/irlpls-keys.json: ['demo-shim-1']
[+  0.07s] itm_1: verified=True (irlpls.protocol.receipts) signature verifies with the signer's key; bindings match
[+  0.07s] itm_2: verified=True (irlpls.protocol.receipts) signature verifies with the signer's key; bindings match
[+  0.07s] itm_3: verified=True (irlpls.protocol.receipts) signature verifies with the signer's key; bindings match
[+  0.07s] itm_4: verified=True (irlpls.protocol.receipts) signature verifies with the signer's key; bindings match
[+  0.07s] signer says: custody only: the shim received this item from usgs-water and relayed it unchanged. usgs-water did not sign it...
[+  0.07s] PASS  every receipt verifies with the operator's key

== 8. Router plans 'photo of the storefront at 12 Elm St'  (12:17:07.553Z)
[+  0.11s] plan: irlpls-sim (fit 80), proxypics (fit 73), rentahuman (fit 58), doordash-tasks (fit 56)
[+  0.11s] PASS  best: irlpls-sim at http://127.0.0.1:58131/irlpls
[+  0.11s] wire request: {"need":"photo of the storefront at 12 Elm St","within_minutes":480,"quantity":1,"mode":"commission","terms":["storefront"],"modalities":["photo"],"require":...

== 9. Discover the simulator  (12:17:07.594Z)
[+  0.11s] card irlpls-sim (sample): endpoint=http://127.0.0.1:58131/irlpls jwks_uri=http://127.0.0.1:58131/.well-known/irlpls-keys.json cancel=True
[+  0.11s] PASS  discovery checks pass (same origin)

== 10. Quote the storefront photo  (12:17:07.594Z)
[+  0.12s] quote q_k72cC9pLjqRhUjDt expires 2026-10-28T14:17:07Z
[+  0.12s] offer o_1: route=commission firm=True total={"amount":"12.00","currency":"USD"} deliver_by=2026-10-28T12:17:09Z
[+  0.12s]   rights={"commercial_use":"allowed","redistribution":"allowed","modification":"allowed","ai_training":"allowed","resale":"for...
[+  0.12s]   proof={"human":"claimed","application":"none","camera":"none","time":"claimed","location":"claimed","content":"claimed","corroboration":"none"}
[+  0.12s] PASS  firm offer within max_total

== 11. Order with an Idempotency-Key  (12:17:07.599Z)
[+  0.12s] Idempotency-Key ba396059-a7b3-4e73-832b-723c33af5b35  nonce 1rM6BBqx5jI5KIrN4LfcCQ
[+  0.12s] order ord_wdM7YMmT453pqdDU: state=submitted total={"amount":"12.00","currency":"USD"} Retry-After=1s
[+  0.12s] PASS  accepted, not yet delivered

== 12. Replay the same order  (12:17:07.603Z)
[+  0.12s] replay -> order ord_wdM7YMmT453pqdDU
[+  0.12s] PASS  same order id; the provider holds 1 order
[+  0.13s] PASS  same key, different body -> 422 idempotency_key_reused

== 13. Poll status until delivered  (12:17:07.611Z)
[+  0.13s] status: submitted  provider_status=posted                     delivered 0/1
[+  0.41s] status: in_flight  provider_status=accepted_by_photographer   delivered 0/1
[+  1.24s] status: delivered  provider_status=approved                   delivered 1/1
[+  1.24s] history: ['submitted/posted', 'in_flight/accepted_by_photographer', 'in_flight/uploaded', 'delivered/approved']
[+  1.24s] PASS  delivered

== 14. Deliver the photo  (12:17:08.720Z)
[+  1.25s] itm_1: Simulated photo 1 of 1 = image/png 3179 B sha256=2755293e8c43... at 2026-10-28T12:17:08Z
[+  1.26s] PASS  downloaded 3179 B; sha256 matches the item

== 15. Verify the photo receipt  (12:17:08.740Z)
[+  1.27s] receipt kid=es-20261028-PCJ3qg iss=http://127.0.0.1:58131 order_id=ord_wdM7YMmT453pqdDU nonce=1rM6BBqx5jI5KIrN4LfcCQ
[+  1.27s]   media.sha256=2755293e8c43254c... request_sha256=36712c0d4b183f2b...
[+  1.27s] itm_1: verified=True (irlpls.protocol.receipts) signature verifies with the signer's key; bindings match
[+  1.27s] PASS  receipt verifies

== 16. Tamper with the receipt  (12:17:08.749Z)
[+  1.27s] lat changed 39.80168 -> 40.0: verified=False (signature does not verify)
[+  1.27s] media hash swapped:          verified=False (signature does not verify)
[+  1.27s] checked against another nonce: verified=False (nonce does not match)
[+  1.27s] PASS  all three fail

== 17. Refuse a person-targeting ask: 'photo of the woman leaving 12 Elm St every morning'  (12:17:08.750Z)
[+  1.30s] router: targets_person=True; plan=[]; 47 people-based providers ruled out for it
[+  1.30s] PASS  router: nothing planned, nothing sent
[+  1.31s] PASS  provider (sim) -> 422 person_targeting_refused
[+  1.31s] PASS  shim (usgs-water, no flag sent) -> 422 person_targeting_refused

RESULT: all 17 steps passed
```

## Limits

- The river receipts are the shim operator's, not USGS's. They prove custody (the shim relayed these bytes), not that the reading is true. The guarantee levels stay the card's.
- The strict client refuses the shim's card because its endpoint is not on usgs.gov. The demo uses it as an accepted unclaimed card with a disclosed operator (draft section 3). It does not bypass the check.
- The reference server (simulator) refuses person targeting only when the request carries `targets_person`; it does not read the text itself. The router sets the flag and the shim checks the text.
- Plain HTTP on localhost, by explicit opt-out (`allow_http`, `allow_private`). Real endpoints must be HTTPS on public hosts.
