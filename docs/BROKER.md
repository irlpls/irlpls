# The broker conversation (draft, 2026-10-25)

irlpls is a broker agent. Requests arrive from another agent (or a person) that has a **goal**, not a spec. The broker's job is to turn that goal into something a provider can capture, under the requester's rules, and to report back honestly on what was delivered and what it proves.

## Why requests arrive the way they do

Requesters reason backwards from a decision. "Should I bike to Zilker?" becomes "is it raining there now?", which becomes "precipitation within ~2 km, observed in the last 15 minutes". Each step of that decomposition sets a tolerance the broker needs:

| Field | Comes from | Example |
|---|---|---|
| **purpose** | the decision being made | "decide whether to bike", "train a door-opening policy" |
| **observable** | what would answer it | rain yes/no; video of a hand opening a push-bar door |
| **where** | how local the answer must be | within 2 km; any real door |
| **freshness / deadline** | how fast it decays, when it's needed | observed ≤15 min ago; delivered within 7 days |
| **quantity** | one answer vs coverage of a distribution | 1; 500 with varied lighting |
| **evidence** | what the requester will rely on | time+place provable; a real person; rights to train |
| **value / budget** | what the decision is worth | $0 (only free reads); up to $2,000 |
| **substitutes** | other observables that would do | a radar product or a photo instead of a gauge |
| **acceptance** | how the requester will judge each item | "my vision model says a push-bar door opens" |

**Purpose matters**, because it lets the broker suggest substitutes. Rain can be read from a station, a radar product, or a fresh photo. Without the purpose, the broker can only match the literal observable.

## Three shapes of requester

1. **A person's assistant** (one answer, now). The request is underspecified and urgent, so the broker asks almost nothing. It infers defaults from the purpose, prefers free live reads, and answers with what the source can prove. A human is in the loop only if money would be spent.
2. **A researcher's agent** (N samples, a spec). The request is detailed but feasibility is unknown. It needs negotiation: who can do it, partial plans across providers, price, timeline, acceptance criteria, and rights. Delivery streams in, and rejected items are re-ordered.
3. **A training harness** (many small requests, in a loop). Machine-generated, budgeted, latency-sensitive, with no human present. It needs a **standing mandate**: a pre-approved policy and budget under which the broker can act. It also needs idempotency and batching, plus a cheap "can anyone answer this?" probe before spending.

## The lifecycle

```
need ──► clarify ──► propose ──► authorize ──► execute ──► deliver ──► feedback
          (only        (options     (policy /     (provider    (items +     (accept /
          questions     with gaps,   mandate, or   jobs,        provider     reject per
          that change   quotes,      escalate to   status,      proofs,      item; shapes
          routing or    rights)      a human)      substitutes  attestations, re-orders and,
          price)                                   on failure)  unknowns)    later, cards)
```

- **Clarify** only when the answer changes which provider, the price, or the feasibility. An agent can usually answer from its own context; a person should rarely be asked.
- **Propose** options with tradeoffs (fastest / cheapest / most provable), each with the gaps its Capture Card admits. Combining providers is allowed.
- **Authorize** happens automatically inside the requester's policy or mandate. Anything outside it (spend, rights, unclaimed providers) escalates to the human who owns the mandate.
- **Deliver** returns the items plus the provider's own proof and any independent attestations, and says plainly what is still unknown. irlpls does not verify anything itself. The requester applies its own acceptance.
- **Feedback** from the requester (accept or reject per item) drives re-orders. Later it may inform cards, carefully: there is no universal score.

## Division of responsibility

| Requester | Broker (irlpls) | Provider |
|---|---|---|
| goal, purpose, acceptance, policy/mandate, money | translate goal → spec, match, plan, enforce policy, execute, monitor, report honestly | capture, and prove time, place and content |

## What this means for the interface

- The unit is a **request with a lifecycle**, not a function call: `draft → clarifying → proposed → authorized → in_flight → delivered | partial | failed`.
- The same messages work for an agent over MCP or A2A, a person in chat, and code via the SDK: `need`, `questions`, `answers`, `options`, `accept`, `status`, `delivery`, `feedback`.
- Live versus commissioned is a routing outcome inside "execute", not something the requester chooses.
- **Mandates** (a standing policy plus a budget plus who to escalate to) are first-class, because the harness case can't work without them.

## Natural language first

Yes, the request is natural language. Agents talk to each other in prose, and a person certainly will. The requester says what it needs and why ("I need to know if it's raining at Zilker Park in the next 15 minutes; my user is deciding whether to bike"). Structure is optional, as hints the requester may attach when it already knows them (a lat/lon, a budget, a policy). The broker's first job is to read that prose and fill in the fields above, asking only when a gap changes the outcome.

**Open decision: whose model reads the prose?** The thesis says "bring your own model; we don't run inference".
- **(A) Broker as a skill in the requester's runtime.** The MCP server and SDK provide the schemas, card data and tool descriptions. The requester's own LLM does the translation and the clarifying. That keeps us inference-free, and every MCP-capable agent already works this way.
- **(B) Hosted broker agent.** irlpls runs a model that holds the conversation. Requesters with no model, such as plain scripts or simple webhooks, can use it. It costs inference and puts us in the loop.
- Likely path: A now (MCP + SDK), with B later as an optional hosted service. The protocol messages are the same either way.
