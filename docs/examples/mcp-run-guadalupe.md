# MCP run: "is the Guadalupe at Comfort, TX good for tubing today"

A real run of the irlpls MCP server answering the landing page's first example. The server ran over stdio and was driven by the official `mcp` Python client (SDK 2.3.0, protocol 2025-11-25) against the live USGS network, with no API keys set.

- Reproduce: `uv run --with mcp python tools/mcp_demo.py`
- Raw record: [`mcp-run-guadalupe.json`](mcp-run-guadalupe.json) has every tool call, its arguments, start and finish timestamps, and its exact JSON response. Nothing was trimmed in this run because every list was under the trim limits (ruled_out has 34 entries; its limit is 60). The byte length and sha256 of each response are recorded too.
- Note (2026-10-28): this run predates the move off USGS WaterServices, which USGS slows from 2026-11-16 and shuts on 2027-02-22. The adapter no longer falls back to it (no `fallback_legacy`, no `via: legacy`), and `daily_stats.source` is now `https://api.waterdata.usgs.gov/statistics/v0/observationNormals` with `count` (years) instead of a year span. The record below is kept as it ran.
- Run at: 2026-10-25 22:57:47 UTC. Job id: `4374a352594b`. Server reported `irlpls` version `0.1.0`.
- This is the second run. The first (22:47 UTC, same day) found the gaps listed under "Page vs reality" and "Agent ergonomics"; the library was then changed and this run regenerated. Each item below says whether it is now **resolved**, **partly resolved** or still **open**.

## Calls

| # | Tool | Arguments | Time (UTC) | Response |
|---|---|---|---|---|
| 1 | `request_data` | need, why, `policy={"max_order_usd": 0}` | 22:57:47.983 to 48.044 | 11,021 B, state `proposed` |
| 2 | `submit_job` | `job_id` | 22:57:48.044 to .490 | 15,048 B, state `delivered` |
| 3 | `get_job` | `job_id` | 22:57:48.491 to .495 | 15,048 B, byte-identical to call 2 (same sha256) |

Every tool now has a description (`tools_listed`): request_data 1,255 characters, the others 159 to 400, and every argument has its own description in the input schema.

## Request

```json
{"need": "is the Guadalupe at Comfort, TX good for tubing today",
 "why":  "user deciding whether to go tubing on the Guadalupe today",
 "policy": {"max_order_usd": 0}}
```

This is how irlpls parsed it (`response.request`):

| Field | Value |
|---|---|
| place | Comfort, TX (29.967715, -98.9050337), country `us`, state `texas` |
| terms | `["tubing"]` |
| require (default proof) | time, location, content |
| mode | **`live`** (a question about today's conditions; no capture was asked for) |
| within_minutes | 720 |
| quantity | 1 |

The policy was echoed back as `{"max_order_usd": 0, "include_unclaimed": true}`.

## Plan

`questions` was empty. The plan had one option:

| Rank | Provider | Route | Fit | Gaps | Proof (time / location / content) | Price | Policy | Callable |
|---|---|---|---|---|---|---|---|---|
| 1 | `usgs-water` USGS stream gauges | live | 73 | "no proof of time", "self-reported location, content" | none / claimed / claimed | **0**, per request | no violations | yes, no key |

Fit rose from 71 to 73 because the request is now live, and USGS is a live feed (the timing part of the score is 1 instead of a commission's deadline score).

## Ruled out

All 34 other in-scope providers are in `ruled_out`, closest misses first. Each entry has `reason` (the main machine-readable code), `label` (one or two words), `why` (a short sentence), and `reasons` (every code that applies, the main one first). The order of precedence is: the requester's policy, then coverage, then speed, then the keyword match (the least certain), then keys. The four providers on the landing page:

| Provider | Page says | `reason` | `label` | `why` | `reasons` |
|---|---|---|---|---|---|
| `nws` NWS weather stations | no flow | `no_match` | no match | its card doesn't mention tubing | `no_match` |
| `caltrans-cctv` Caltrans highway cameras | wrong state | `outside_coverage` | wrong state | doesn't cover Texas | `outside_coverage`, `no_match` |
| `skyfi` SkyFi | paid | `policy:max_order_usd` | paid | costs money; max_order_usd is $0 | `policy:max_order_usd`, `too_slow`, `no_match`, `needs_key` |
| `usgs-water` USGS stream gauges | free (chosen) | in the plan, price 0, chosen | | | |

The rest: 6 more `no_match` (AirNow, NASA FIRMS, NDBC buoys, NOAA tides, USGS earthquakes; OpenAQ also `needs_key`), 2 more `outside_coverage` (WSDOT "wrong state"; TfL "wrong country", doesn't cover US), and 23 more `policy:max_order_usd` "paid" (every provider whose card isn't free to use, including freemium Windy Webcams and PurpleAir).

## Chosen provider

`choice` = `usgs-water`, live route. Log: `proposed: 1 options, 34 ruled out, best usgs-water via live`, then three `warning from usgs-water: ...` lines (below), then `live read from usgs-water: 3 observations`.

## Observations returned

| Site | Distance | Label | Value | Daily median for Oct 25 (p25 / p75, years) | observed_at | age_s | via |
|---|---|---|---|---|---|---|---|
| USGS-08167000 (Guadalupe Rv at Comfort) | 0.8 km | discharge | **76.3 ft3/s** | **103** (51 / 201, 1940-2025, 86 years) | 2026-10-25T17:35:00.000-05:00 | 1368 | legacy |
| USGS-08167000 | 0.8 km | gage height | 3.26 ft | 2.93 (1.71 / 3.37, 1991-2025) | same | 1368 | legacy |
| USGS-08166700 | 8.2 km | gage height | 5.15 ft | none published | same | 1368 | legacy |

Each observation also carries `provider_proof` (time none, location claimed, content claimed) and `daily_stats.source` (`https://waterservices.usgs.gov/nwis/stat/`, the same service `tools/snapshot_media.py` uses). irlpls stays inference-free: it returns USGS's own median, and the agent says what 76.3 against 103 means (26% below the median).

`warnings` (also in `log`):

| code | status | msg |
|---|---|---|
| `fallback_legacy` | 429 | USGS OGC API returned HTTP 429; readings come from the legacy waterservices endpoint (via: legacy); set USGS_WATER_API_KEY to raise the rate limit |
| `daily_stats_missing` | | USGS publishes no daily statistics for USGS-08166700 gage height |
| `camera_unavailable` | 429 | USGS camera service returned HTTP 429; no photo; set USGS_WATER_API_KEY to raise the rate limit |

The reading was taken at 22:35 UTC, 1,368 s (about 23 minutes) before the response. No photo came back: the NIMS camera service was rate-limited again, and this time the job says so.

## Page vs reality

This section compares the run with `src/index.html`, example 1: the scan pane in the `EX` array, the result widget `ex1result`, and the transcript.

1. **Resolved. Ruled-out providers with reasons.** The page shows four candidates with a reason on each rejection. The job now has a `ruled_out` list covering every provider not in the plan, each with a code and a short reason.
2. **Resolved in kind (wording differs). NWS, "no flow".** NWS is ruled out with `no_match`: "its card doesn't mention tubing". irlpls doesn't produce the word "flow". The page's per-column ✓/✗ marks for NWS (Comfort, TX ✓, Now ✓, $0 ✓) are not returned as columns; they follow from NWS having only the one reason.
3. **Resolved. Caltrans, "wrong state".** `outside_coverage`, label "wrong state", "doesn't cover Texas".
4. **Resolved. SkyFi, "paid".** `max_order_usd: 0` is now checked at plan time and excludes every provider that isn't free to use: SkyFi's main reason is `policy:max_order_usd`, label "paid". Its `reasons` also list `too_slow` ("not live; capture takes days"), which matches the page's ✗ under "Now".
5. **Partly resolved. USGS shown passing every check.** Its price is now 0, not null, and it is the one option that passes the policy, coverage and timing. It still has two gaps (no proof of time; location and content self-reported), which the page doesn't show.
6. **Partly resolved. The page's "needs" columns (Flow · Comfort, TX · Now · $0).** "Now" is now real (`mode: live`) and "$0" is real (`max_order_usd: 0` enforced at plan time). "Flow" is not a parsed term: the term is `tubing`, which the matcher maps to river, gauge and streamflow.
7. **Resolved. "irlpls understands it as a live request".** `request.mode` is `live`. "today" is read as live when no capture is asked for, and as a deadline when one is ("photo of the storefront at 123 Main St … today" stays a commission). `src/match.js` parses it the same way.
8. **Resolved. The policy shown on the page is what is echoed.** `{"max_order_usd": 0, "include_unclaimed": true}`.
9. **Resolved. "The typical flow for today's date from USGS's own statistics".** Each reading now carries `daily_median` and `daily_stats` (p25/p50/p75) for its calendar day. The median, 103 ft³/s, equals the page's saved normal. The "% below normal" chip is arithmetic the agent does on these numbers.
10. **Resolved in the library; the page caption is unchanged. "How old the reading is".** Every observation has `age_s` (1,368 s here). The page's "USGS gauge · now" caption is not something this library controls.
11. **Open. Photo.** The page shows the Guadalupe camera frame. This run returned no photo because the camera service answered 429. The difference is now visible to the agent as a `camera_unavailable` warning rather than silent. A run with `USGS_WATER_API_KEY`, or from a quieter IP, should also return a `kind: "photo"` frame.
12. **Unchanged, still true. Gauge site and value match.** irlpls picked USGS-08167000 (0.8 km), and 76.3 ft3/s matches the page's code sample. The unit spelling differs (`ft3/s` vs `ft³/s`).
13. **Unchanged.** The "Real output" `job.results[0]` sample matches this run's first observation (it now also has `daily_median` and `age_s`).
14. **Unchanged, cosmetic.** The page abbreviates the call as `irlpls.request("Guadalupe, tubing, today?")`.

## Agent ergonomics

1. **Resolved.** `request_data` has a 1,255-character description: what it does, an example call, what the job contains (including `ruled_out` and `warnings`), and what to call next. Every argument of every tool is described, including the full policy (with `free_only`, and `max_order_usd: 0` meaning free only).
2. **Resolved.** The policy echo keeps zeros (`Policy.to_dict`, and `Request.to_dict` for lat/lon 0).
3. **Resolved.** `max_order_usd: 0` excludes paid providers at plan time. A positive ceiling also excludes providers whose card's starting price is above it.
4. **Resolved.** `ruled_out` lists every excluded provider with its reasons.
5. **Resolved.** `price: 0` for a provider that is free to use (`access.cost` free or free with key); `null` still means the price isn't published.
6. **Resolved for this request.** `request.mode` and the route are both `live`.
7. **Resolved.** The legacy fallback, the missing statistics and the camera's 429 are listed in `warnings` and in `log`, with a hint to set `USGS_WATER_API_KEY`.
8. **Open.** Readings from a second site (USGS-08166700, 8.2 km) are still mixed in. The agent sorts by `distance_km`.
9. **Partly resolved.** `age_s` removes the arithmetic, but `observed_at` is still in the provider's local offset while `log[].t` is UTC.
10. **Resolved.** See item 9 under Page vs reality.
11. **Open, and larger.** `get_job` after a live `submit_job` still returns the identical job. Every response carries the whole job, which is now 11 to 15 KB because of the 34 `ruled_out` entries (about 7 KB).
12. **Resolved.** `initialize` reports version `0.1.0`.
