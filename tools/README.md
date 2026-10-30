# tools/

## Scheduled jobs (LaunchAgents on the owner's Mac)

| Label | When | Runs | Log |
|---|---|---|---|
| `com.mo.irlpls-refresh` | every 30 min | `tools/snapshot_media.py && build.py` | `~/Library/Logs/irlpls-refresh.log` |
| `com.mo.irlpls-healthcheck` | daily 06:10 (missed runs fire on wake) | `tools/healthcheck.py` | `~/Library/Logs/irlpls-healthcheck.log` |
| `com.mo.irlpls-preview` | always | `tools/preview.py` (Tailscale :8430) | `~/Library/Logs/irlpls-preview.log` |

The healthcheck plist is kept in `deploy/com.mo.irlpls-healthcheck.plist` (install and run-now commands are in its
header comment).

## Daily read check (`tools/healthcheck.py`)

One minimal real read through each provider's adapter, so a broken call is caught before a user meets it: every
keyless live feed (at a test point in `POINTS`), the keyless listings of order-only adapters (`EXTRA`), and keyed live
reads whose key is set. Never an order, quote or paid read (`NEVER`). 10 s per request, 25 s per provider, 100 s for
the run; pacing and User-Agent come from `irlpls.ratelimit` and `irlpls.http`.

- Writes `irlpls/data/health.json` (format in `irlpls/health.py`). **Gitignored**: it changes every day and is a
  machine-local observation, like `site/`. Each provider keeps 14 days of `history`.
- `build.py` reads it: card pages show "Read check: worked 28 Oct" / "Read check: failed 28 Oct (404)" in the API
  section (merged with the landing's 30-minute check, src/media/live/health.json: the newest of the two wins); `catalog.json` has `providers[id].health` and `stats.read_check`. The next 30-minute refresh rebuilds.
- The broker adds `read_check` to each plan option and, when the picked (best or chosen) provider's last check
  failed, a `warning` on that option plus a `read_check_failed` job warning. It never reranks.
- Two calendar days of failure in a row put one `info` update on `docs/research/BOARD.json`
  (id `u-health-<card>-<first failed day>`, once per streak).
- A new live adapter with no test point shows as `skipped` until it gets one in `POINTS`.

Run by hand: `python3 tools/healthcheck.py` (`--only nws,usgs-water`, `--no-board`, `--out PATH`).

## Provider-published cards (`tools/fetch_published.py`)

Runs at the end of the daily read check (healthcheck.py calls it; `--no-published` skips it), so no plist changed.
One conditional GET of `https://<host>/.well-known/irlpls` per `provider_url` host of the compiled cards, plus the hosts
in `irlpls/data/published-domains.txt` (added by hand after a provider writes to hello@irlpls.com). 10 s timeout,
256 KB cap, no redirects, https and public hosts only, no compression, max-age honoured up to 24 h, User-Agent
`irlpls-cardreader/0.1 (+https://irlpls.com/providers.html; hello@irlpls.com)`. Rules and states: `irlpls/published.py`.

- Writes `irlpls/data/published/` (**gitignored**): `state.json`, `<id>.json` (the last good copy of each accepted
  card) and `rejects.log` (private, JSON lines). Also `site/published.json`, the public part; `build.py` rewrites it.
- `build.py` shows a live (or stale, up to 7 days) copy in place of the compiled card, with the badge, "What an agent
  says", "Where it ranks", "What would move you up" (irlpls/standing.py, from `tools/bench_asks.json`), the changes from
  the compiled card, `/badge/<id>.svg`, the Find data strip and the footer line on the landing page.
- A card from a host with no compiled card, or claiming an id its host does not own, never goes on the site: one
  `info` update on `docs/research/BOARD.json` (ids `u-published-new-…`, `u-published-collision-…`, deduped).

Run by hand: `python3 tools/fetch_published.py` (`--only www.proxypics.com`, `--no-board`, `--no-evidence`).
