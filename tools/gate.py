#!/usr/bin/env python3
"""Offline card gate CLI (no network). Exit 1 on any error.

    python3 tools/gate.py cards/x.json ...      python3 tools/gate.py --batch cards/      add --json for JSON
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # works from a plain checkout; no install needed
from irlpls.gate import HINTS, gate_files  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--batch", metavar="DIR", help="gate every *.json in DIR")
    ap.add_argument("--json", action="store_true", help="JSON output")
    a = ap.parse_args(argv)
    paths = list(a.files)
    if a.batch:
        paths += sorted(str(p) for p in Path(a.batch).glob("*.json"))
    if not paths:
        ap.error("give card files or --batch DIR")
    res = gate_files(paths)
    bad = sum(1 for r in res.values() if not r.ok)
    if a.json:
        print(json.dumps({"ok": bad == 0, "checked": len(res), "failed": bad,
                          "results": [r.as_dict() for r in res.values()]}, indent=2))
    else:
        for p, r in res.items():
            for e in r.errors:
                print(f"{p}: error {e['code']} {e['pointer'] or '/'}: {e['message']}")
                if e["code"] in HINTS:
                    print(f"    fix: {HINTS[e['code']]}")
            for w in r.warnings:
                print(f"{p}: warn {w['code']} {w['pointer'] or '/'}: {w['message']}")
        print(f"{len(res)} checked, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
