#!/usr/bin/env python3
"""Repo-level card gate for CI; stdlib + jsonschema (already in CI).

Catches what the schema step cannot: duplicate ids, id != filename, promo/ranking keys, evidenced without evidence_url
(via irlpls.validate.problems errors), and cards with no or an unknown "family" field (build fails anyway, but say it plainly; Fix: add "family": "<id>" to the card).
Exit 1 on any finding.   python tools/check_cards.py
"""
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls import validate  # noqa: E402

PROMO_KEYS = {"sponsored", "promoted", "featured", "rank", "ranking", "boost", "paid", "ad", "ads", "priority", "score"}


def main():
    bad = validate.repo_problems(ROOT / "cards")  # not JSON, id != file name, duplicate id, no family (same text as build.py)
    seen = set()
    for f in sorted(glob.glob(str(ROOT / "cards" / "*.json"))):
        name = Path(f).stem
        try:
            c = json.loads(Path(f).read_text())
        except ValueError:
            continue
        seen.add(name)
        hit = PROMO_KEYS & set(c)
        if hit:
            bad.append(f"{name}: promo/ranking keys {sorted(hit)} (CONTRIBUTING: no ranking for money)")
        for sev, where, msg in validate.problems(c):
            if sev == "error":
                bad.append(f"{name}: {where}: {msg}")
    tpl = ROOT / "examples" / "card-template.json"  # the starter card must stay valid
    if tpl.exists():
        for sev, where, msg in validate.problems(json.loads(tpl.read_text())):
            if sev == "error":
                bad.append(f"examples/card-template.json: {where}: {msg}")
    print("\n".join(bad) or f"all {len(seen)} cards pass the repo gate")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
