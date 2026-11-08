#!/usr/bin/env python3
"""Crawl /.well-known/irlpls.json for a list of domains; accepted cards go to a QUARANTINE dir, never cards/.

  python tools/crawl_wellknown.py --domains domains.txt --state state/ --out quarantine/ [--log registry.ndjson] [--dry-run]

domains.txt: one domain per line, '#' comments. State (ETag, backoff) is kept per domain in --state. --dry-run fetches
and reports but writes no state, no cards and no events. Exit 0 unless the log chain is broken.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls import crawl, events  # noqa: E402


def run(domains, state_dir, out_dir, log, dry=False, fetcher=None, now=None):
    state_dir, out_dir = Path(state_dir), Path(out_dir)
    if (ROOT / "cards").resolve() in (out_dir.resolve(), *out_dir.resolve().parents):
        raise SystemExit("refusing: --out must not be inside cards/")
    summary = []
    for dom in domains:
        sf = state_dir / (dom.replace("/", "_") + ".json")
        st = json.loads(sf.read_text()) if sf.exists() else None
        r = crawl.crawl_domain(dom, fetcher=fetcher, state=st, now=now)
        summary.append({"domain": r.domain, "status": r.status, "accepted": [c.id for c in r.accepted],
                        "errors": r.errors + [e for c in r.cards for e in c.errors]})
        if dry:
            continue
        ev = lambda a, **k: events.append(log, a, domain=r.domain, **k)  # noqa: E731
        ev("crawled", evidence=[{"status": r.status, "http": r.http_status, "errors": r.errors}])
        for c in r.cards:
            ev("gated", card_id=c.id, version_sha256=c.sha256, evidence=[{"ok": c.status == "accepted", "errors": c.errors}])
            if c.status == "accepted":
                d = out_dir / r.domain
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{c.id}.json").write_text(json.dumps(c.card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            ev(c.status, card_id=c.id, version_sha256=c.sha256, evidence=[{"errors": c.errors}])
        state_dir.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(r.state), encoding="utf-8")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--domains", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", default=None, help="event log (default: <out>/registry.ndjson)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    doms = [l.split("#")[0].strip() for l in Path(a.domains).read_text().splitlines()]
    doms = [d for d in doms if d]
    summary = run(doms, a.state, a.out, a.log or str(Path(a.out) / "registry.ndjson"), a.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
