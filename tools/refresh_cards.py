#!/usr/bin/env python3
"""Card expiry helper: list by expiry, probe endpoints politely, propose (or write) a date bump.

  python tools/refresh_cards.py                    # soonest expiry first, counts per month
  python tools/refresh_cards.py --probe [ID...]    # GET each card's doc/endpoint URLs; read-only
  python tools/refresh_cards.py --bump ID...       # print the proposed diff only
  python tools/refresh_cards.py --bump ID... --write

A bump sets `updated` to today and `expires` to today + 365 days (schema: at most a year after updated).
It never touches access.tested_live or any claim: a human re-reads the provider first (docs/research/ops/CARD-REFRESH-PLAN.md).
A card is bumped only if every probed URL answered 2xx/3xx in this run. Without --write nothing on disk changes.
"""
import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls import safefetch  # noqa: E402
CARDS = ROOT / "cards"
UA = safefetch.UA
TIMEOUT = 10
MIN_GAP = 1.0  # seconds between requests to one host


def load_cards(cards_dir=CARDS):
    out = []
    for f in sorted(Path(cards_dir).glob("*.json")):
        try:
            c = json.loads(f.read_text())
        except ValueError:
            continue
        out.append((f, c))
    return out


def by_expiry(cards):
    """[(expires, id)] soonest first; cards without a valid date sort last as ''."""
    rows = []
    for f, c in cards:
        e = c.get("expires")
        try:
            date.fromisoformat(e)
        except (TypeError, ValueError):
            e = ""
        rows.append((e, c.get("id", f.stem)))
    return sorted(rows, key=lambda r: (r[0] == "", r[0], r[1]))


def month_counts(rows):
    return sorted(Counter(e[:7] or "none" for e, _ in rows).items())


def card_urls(card):
    """Documented endpoint/doc URLs only: api.docs_url, api.base_url, provider_url."""
    api = card.get("api") or {}
    urls = [api.get("docs_url"), api.get("base_url"), card.get("provider_url")]
    seen, out = set(), []
    for u in urls:
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


ROOT_INFO = (400, 401, 403, 404, 405, 406)


def url_kind(card, url):
    """'docs' for docs_url/provider_url, 'base' for an API base_url that is not also a docs/provider page."""
    api = card.get("api") or {}
    if url in (api.get("docs_url"), card.get("provider_url")):
        return "docs"
    return "base" if url == api.get("base_url") else "docs"


def verdict(kind, st):
    """ok | root-info (base URL 4xx, expected without a path) | blocked (403 on docs, maybe a bot wall) | FAIL."""
    if isinstance(st, int) and st < 400:
        return "ok"
    if kind == "base" and st in ROOT_INFO:
        return "root-info"
    if kind == "docs" and st == 403:
        return "blocked"
    return "FAIL"


def http_fetch(url, fetcher=None):
    """HEAD, falling back to GET when HEAD is refused (via irlpls.safefetch: SSRF-safe, robots, rate limit).
    Returns an int status or an error string."""
    fetch = fetcher or safefetch.fetch
    for method in ("HEAD", "GET"):
        try:
            st = fetch(url, method=method, timeout=TIMEOUT, read_body=False).status
        except safefetch.SafeFetchError as e:
            return f"error: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001 - report, never crash a batch
            return f"error: {type(e).__name__}"
        if method == "HEAD" and st in (403, 405, 501):
            continue
        return st
    return "error"


def probe(cards, fetch=http_fetch, sleep=time.sleep, clock=time.monotonic):
    """{id: [(url, status)]}; at most one request per second per host."""
    last, res = {}, {}
    for f, c in cards:
        out = []
        for u in card_urls(c):
            host = urlparse(u).netloc
            wait = MIN_GAP - (clock() - last[host]) if host in last else 0
            if wait > 0:
                sleep(wait)
            st = fetch(u)
            last[host] = clock()
            out.append((u, st, url_kind(c, u)))
        res[c.get("id", f.stem)] = out
    return res


def probe_ok(results):
    return bool(results) and all(verdict(k, s) in ("ok", "root-info") for _, s, k in results)


def propose(path, today):
    """(old_text, new_text) with updated/expires rewritten in place; formatting untouched."""
    old = Path(path).read_text()
    new = re.sub(r'^(  "updated": )"[^"]*"', rf'\1"{today.isoformat()}"', old, count=1, flags=re.M)
    new = re.sub(r'^(  "expires": )"[^"]*"', rf'\1"{(today + timedelta(days=365)).isoformat()}"', new, count=1, flags=re.M)
    return old, new


def main(argv=None, fetch=http_fetch, today=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="limit --probe to these card ids")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--bump", nargs="+", metavar="ID")
    ap.add_argument("--write", action="store_true", help="with --bump: actually write the cards")
    ap.add_argument("--cards-dir", default=str(CARDS))
    a = ap.parse_args(argv)
    today = today or date.today()
    cards = load_cards(a.cards_dir)

    if a.bump:
        known = {c.get("id", f.stem): (f, c) for f, c in cards}
        missing = [i for i in a.bump if i not in known]
        if missing:
            print("unknown card id: " + ", ".join(missing), file=sys.stderr)
            return 2
        rc = 0
        for i in a.bump:
            f, c = known[i]
            res = probe([(f, c)], fetch=fetch)[i]
            if not probe_ok(res):
                print(f"REFUSED {i}: probe failed " + "; ".join(f"{u} -> {s}" for u, s, k in res if verdict(k, s) not in ("ok", "root-info")) if res else f"REFUSED {i}: no URL to probe")
                rc = 1
                continue
            old, new = propose(f, today)
            if old == new:
                print(f"{i}: dates already {today.isoformat()}; nothing to change")
                continue
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/cards/{f.name}", f"b/cards/{f.name}", n=0))
            if a.write:
                f.write_text(new)
                print(f"wrote {f.name}")
        if not a.write:
            print("(proposal only; add --write to apply)")
        return rc

    if a.probe:
        sel = [(f, c) for f, c in cards if not a.ids or c.get("id", f.stem) in a.ids]
        bad = 0
        for i, res in probe(sel, fetch=fetch).items():
            for u, s, k in res:
                v = verdict(k, s)
                tag = {"ok": "ok  ", "root-info": "root-info", "blocked": "blocked (bot?)", "FAIL": "FAIL"}[v]
                print(f"{tag} {i} {s} {k} {u}")
                bad += v == "FAIL"
        return 1 if bad else 0

    rows = by_expiry(cards)
    for e, i in rows:
        print(f"{e or 'none':10}  {i}")
    print("\nper month:")
    for m, n in month_counts(rows):
        print(f"  {m}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
