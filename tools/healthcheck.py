#!/usr/bin/env python3
"""Daily read check: one minimal real read through each provider's adapter, so a broken call is caught before a user
meets it.

    python3 tools/healthcheck.py              # check, write irlpls/data/health.json, alert BOARD.json on a 2-day failure
    python3 tools/healthcheck.py --only nws,usgs-water --no-board

What runs: every adapter with a keyless read (a live feed near a point where it has data, or a keyless listing such as
a task board or a coverage lookup), plus keyed live reads whose key is in the environment. Exactly one adapter read
each, through the real adapter, under irlpls.ratelimit (the provider's limit, Retry-After) and irlpls.http (polite
User-Agent naming irlpls and a contact). Never an order, quote or paid endpoint: NEVER lists the keyed reads that spend
money (PurpleAir points) or sit under a contract. Each request times out after REQUEST_TIMEOUT_S, each provider gets
PROVIDER_BUDGET_S, and the whole run stops waiting at TOTAL_S (under two minutes).

Result per provider (irlpls/health.py documents the file): ok, or failed with the HTTP status or the error class,
latency, checked_at, a short reason, and one entry per day in `history`. An adapter that swallows an HTTP error into a
warning and returns nothing counts as failed with that warning's status. A read that answers with an empty list is ok
("answered; empty result").

When a provider has failed two calendar days in a row, one 'info' update goes on docs/research/BOARD.json (the board
the owner reads), once per failing streak (id u-health-<card id>-<first failed day>).

health.json is gitignored: it changes every day, and build.py reads it to show "Read check: worked 28 Oct" on card
pages and `health` in catalog.json. Scheduled by the LaunchAgent in deploy/com.mo.irlpls-healthcheck.plist (see
tools/README.md); the 30-minute refresh job's build.py picks the result up.

The same run then reads every provider's own card once (tools/fetch_published.py; --no-published skips it), so a card
a provider publishes is live on irlpls within a day.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import socket
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from irlpls import health, http, ratelimit  # noqa: E402

REQUEST_TIMEOUT_S = 10
PROVIDER_BUDGET_S = 25
TOTAL_S = 100
WORKERS = 8
BOARD = ROOT / "docs" / "research" / "BOARD.json"
ALERT_DAYS = 2

# Keyless test points: a place each feed covers and where it usually has a reading. One live() read, limit 1.
POINTS = {
    "nws": (30.2669, -97.7729), "usgs-water": (29.9677, -98.905), "noaa-tides": (37.8063, -122.4659),
    "caltrans-cctv": (37.79, -122.39), "uk-ea-flood": (51.5, -0.12), "pegelonline": (50.94, 6.96),
    "sensor-community": (52.52, 13.405), "copernicus-sentinel": (50.94, 6.96), "fintraffic-weathercams": (60.17, 24.94),
    "alertcalifornia": (34.1, -118.3), "airnow": (45.52, -122.68), "digitraffic-marine-ais": (60.15, 24.95),
    "emsc-seismicportal": (38.0, 23.7), "fmi-open-data": (60.17, 24.94), "geonet-nz": (-41.29, 174.78),
    "hubeau-hydrometrie": (48.85, 2.35), "nasa-firms": (36.7, -119.4), "ndbc-buoys": (36.75, -122.03),
    "tfl-jamcams": (51.5074, -0.1278), "usgs-earthquakes": (37.77, -122.42), "wsdot-cameras": (47.61, -122.33),
    # keyed: checked only when the key is in the environment
    "windy-webcams": (47.37, 8.54), "openaq": (51.5, -0.12),
}

# Keyless reads of adapters with no live feed: listings that create nothing and cost nothing.
EXTRA = {
    "human-for-ai": ("services()", lambda a: a.services(limit=50)),
    "execution-market": ("board(limit=1)", lambda a: a.board(limit=1)),
    "errand": ("coverage(37.4979, 127.0276)", lambda a: a.coverage(37.4979, 127.0276)),
    "mundane": ("capabilities()", lambda a: a.capabilities()),
}

# Reads never run here, with why.
NEVER = {"purpleair": "each read spends paid PurpleAir points",
         "empiric-earth": "reads sit under a sales contract (Nexar VirtualCam)"}

FAIL_CODES = {"provider_error", "not_found", "refused", "rate_limited", "unexpected_response", "http_error"}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probes(adapters):
    """[(card_id, what, fn(adapter) or None, skip_reason or None)] for every adapter with a read this check may run."""
    out = []
    for cid, a in sorted(adapters.items()):
        if cid in NEVER:
            continue
        if cid in EXTRA:
            what, fn = EXTRA[cid]
            out.append((cid, what, fn, None))
            continue
        if not getattr(a, "can_live", False):
            continue
        if a.env and not a.configured():
            continue   # keyed and no key here: not checked, not listed
        pt = POINTS.get(cid)
        if not pt:
            out.append((cid, "live()", None, "no test point set in tools/healthcheck.py POINTS"))
            continue
        out.append((cid, f"live({pt[0]}, {pt[1]}, limit=1)", (lambda a, pt=pt: a.live(pt[0], pt[1], limit=1)), None))
    return out


def _error_class(e):
    if isinstance(e, (socket.timeout, TimeoutError)) or isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
        return "Timeout"
    if isinstance(e, urllib.error.URLError):
        return "URLError"
    return type(e).__name__


def _short(text, n=160):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def classify(out):
    """(ok, status, reason, items) from what an adapter read returned."""
    if isinstance(out, tuple) and len(out) == 2 and (out[1] is None or isinstance(out[1], dict)):
        val, w = out   # errand.coverage: (dict or None, warning or None)
        items, warns = ([val] if val else []), ([w] if w else [])
    else:
        items = list(out) if isinstance(out, (list, tuple)) else ([out] if out else [])
        warns = list(getattr(out, "warnings", []) or [])
    bad = [w for w in warns if isinstance(w, dict) and (w.get("status") or w.get("code") in FAIL_CODES)]
    if not items and bad:
        w = bad[0]
        return False, w.get("status"), _short(w.get("msg") or w.get("code") or "provider error"), 0
    if not items:
        return True, None, "answered; empty result (nothing near the test point, or nothing listed)", 0
    reason = f"answered with {len(items)} item{'s' if len(items) != 1 else ''}"
    if bad:
        reason += f"; partial: {_short(bad[0].get('msg') or bad[0].get('code'), 80)}"
    return True, None, reason, len(items)


def run_one(cid, adapter, what, fn, timeout_s=REQUEST_TIMEOUT_S, budget_s=PROVIDER_BUDGET_S):
    t0 = time.monotonic()
    rec = {"read": what, "checked_at": now_iso()}
    try:
        with ratelimit.using(cid, budget_s=budget_s), http.timeout_cap(timeout_s):
            out = fn(adapter)
        ok, status, reason, n = classify(out)
        rec.update(result="ok" if ok else "failed", ok=ok, status=status, error=None if ok else "HTTPError" if status else "ProviderWarning",
                   reason=reason, items=n)
    except ratelimit.RateLimited as e:
        rec.update(result="skipped", ok=None, status=None, error="RateLimited", reason=_short(f"not sent: {e}"), items=0)
    except Exception as e:  # anything the read raised is the finding
        status = getattr(e, "status", None)
        rec.update(result="failed", ok=False, status=status if isinstance(status, int) else None, error=_error_class(e),
                   reason=_short(str(e) or type(e).__name__), items=0)
    rec["latency_ms"] = round((time.monotonic() - t0) * 1000)
    return rec


def check(adapters, previous=None, only=None, total_s=TOTAL_S, **kw):
    """Run the probes in parallel (one thread per provider at a time; each under its own rate limit). Returns health.json."""
    previous = previous or {}
    started, t0 = now_iso(), time.monotonic()
    todo = [p for p in probes(adapters) if not only or p[0] in only]
    res = {}
    for cid, what, fn, skip in todo:
        if skip:
            res[cid] = {"result": "skipped", "ok": None, "status": None, "error": None, "latency_ms": 0,
                        "checked_at": started, "reason": skip, "read": what, "items": 0}
    pool = cf.ThreadPoolExecutor(max_workers=WORKERS)
    futs = {pool.submit(run_one, cid, adapters[cid], what, fn, **kw): (cid, what) for cid, what, fn, skip in todo if not skip}
    done, late = cf.wait(futs, timeout=total_s)
    for f in done:
        res[futs[f][0]] = f.result()
    for f in late:
        cid, what = futs[f]
        res[cid] = {"result": "failed", "ok": False, "status": None, "error": "Timeout", "latency_ms": round(total_s * 1000),
                    "checked_at": started, "reason": f"no answer within the run's {total_s:g} s", "read": what, "items": 0}
    pool.shutdown(wait=False, cancel_futures=True)
    today = started[:10]
    prev = previous.get("providers") or {}
    providers = {}
    for cid in sorted(res):
        r = res[cid]
        hist = (prev.get(cid) or {}).get("history") or []
        if r["result"] in ("ok", "failed"):
            hist = health.add_history(hist, today, r["ok"])
        providers[cid] = {**r, "history": hist}
    if only:  # a partial run keeps the others' last results
        providers = {**{k: v for k, v in prev.items() if k not in providers}, **providers}
    n = lambda k: sum(1 for r in providers.values() if r["result"] == k)
    return {"irlpls_health": 1, "checked_at": started, "duration_s": round(time.monotonic() - t0, 1),
            "summary": {"checked": n("ok") + n("failed"), "ok": n("ok"), "failed": n("failed"), "skipped": n("skipped")},
            "providers": dict(sorted(providers.items()))}


def board_alerts(data, names=None, board_path=BOARD, days=ALERT_DAYS):
    """Add one 'info' update to BOARD.json per provider failing `days` calendar days in a row (once per streak).
    Returns the ids added."""
    names = names or {}
    new = []
    for cid, r in (data.get("providers") or {}).items():
        hist = r.get("history") or []
        streak = health.failed_streak(hist)
        if r.get("result") != "failed" or streak < days:
            continue
        start = hist[-streak]["date"]
        new.append({"id": f"u-health-{cid}-{start}", "kind": "info",
                    "text": f"{names.get(cid) or cid}: the daily read check has failed {streak} days in a row "
                            f"({health.why(r)}: {_short(r.get('reason') or '', 120)}). Its card page says so and plans "
                            f"that pick it warn; the adapter or the provider's API needs a look.",
                    "link": f"/c/{cid}.html"})
    if not new or not Path(board_path).exists():
        return []
    b = json.loads(Path(board_path).read_text())
    have = {u.get("id") for u in b.get("updates") or []}
    add = [u for u in new if u["id"] not in have]
    if add:
        b["updates"] = add + list(b.get("updates") or [])
        Path(board_path).write_text(json.dumps(b, indent=1))   # the board's own format: indent 1, ASCII escapes
    return [u["id"] for u in add]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=None, help="health.json to write (default irlpls/data/health.json)")
    ap.add_argument("--only", default="", help="comma-separated card ids")
    ap.add_argument("--no-board", action="store_true", help="never touch docs/research/BOARD.json")
    ap.add_argument("--no-published", action="store_true", help="skip the daily read of provider-published cards")
    a = ap.parse_args()
    from irlpls import adapters, card
    out = Path(a.out) if a.out else health.path()
    data = check(adapters.ADAPTERS, health.load(out), only={x for x in a.only.split(",") if x} or None)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1) + "\n")
    s = data["summary"]
    print(f"{data['checked_at']} read check: {s['ok']} ok, {s['failed']} failed, {s['skipped']} skipped "
          f"in {data['duration_s']} s -> {out}")
    for cid, r in data["providers"].items():
        if r["result"] != "ok":
            print(f"  {r['result']:7} {cid}: {r.get('status') or r.get('error') or ''} {r['reason']}")
    if not a.no_board:
        names = {cid: (card(cid) or {}).get("name") for cid in data["providers"]}
        for i in board_alerts(data, names):
            print("  board: added", i)
    sys.stdout.flush()
    if not a.no_published and not a.only:  # the same daily run reads every provider's /.well-known/irlpls once
        try:
            import fetch_published  # tools/fetch_published.py, beside this file
            fetch_published.main(["--no-board"] if a.no_board else [])
        except Exception as e:  # never let it break the read check
            print(f"  published cards: not read ({type(e).__name__}: {e})")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
