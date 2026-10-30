#!/usr/bin/env python3
"""Daily read of provider-published Capture Cards (irlpls/published.py has the rules).

    python3 tools/fetch_published.py              # read every card domain once, write state + site/published.json
    python3 tools/fetch_published.py --only www.proxypics.com --no-board
    python3 tools/fetch_published.py --no-evidence

Runs once a day from tools/healthcheck.py (so from the com.mo.irlpls-healthcheck LaunchAgent, 06:10); the next
30-minute refresh build shows the result, so a valid card is live on irlpls within a day of being published.
One conditional GET per domain (If-None-Match / If-Modified-Since), 10 s timeout, 256 KB cap, no redirects, https only,
User-Agent "irlpls-cardreader/0.1 (+https://irlpls.com/providers.html; hello@irlpls.com)". Never sends anything else.

Writes irlpls/data/published/{state.json,<id>.json,rejects.log} (gitignored) and site/published.json (public part).
A domain with no compiled card, or a card claiming an id its domain does not own, goes to docs/research/BOARD.json as one
'info' update (deduped) for the owner to review. Nothing is ever added to the registry automatically.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from irlpls import published  # noqa: E402

BOARD = ROOT / "docs" / "research" / "BOARD.json"


def compiled_cards():
    return [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]


def sample_pack_evidence(card):
    """Daily evidence check, sample packs only (irlpls.evidence; never changes a level). [] when the card has none."""
    from irlpls import validate
    packs = [(g, k) for g, ks in (("verification", validate.GUARANTEES), ("provenance", validate.PROVENANCE)) for k in ks
             if isinstance((card.get(g) or {}).get(k), dict) and validate.is_sample_pack_url(card[g][k].get("evidence_url"))]
    if not packs:
        return []
    from irlpls import evidence
    return [evidence.check_entry(card, f"{g}.{k}", card[g][k]) for g, k in packs]   # only the packs are fetched


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated hosts to read (the others keep their last state)")
    ap.add_argument("--no-board", action="store_true", help="never touch docs/research/BOARD.json")
    ap.add_argument("--no-evidence", action="store_true", help="skip the sample-pack evidence check")
    ap.add_argument("--site", default=str(ROOT / "site"), help="where published.json goes (skipped if the folder is missing)")
    a = ap.parse_args(argv)
    cards = compiled_cards()
    only = {x.strip().lower() for x in a.only.split(",") if x.strip()}
    extra = published.read_domains_txt()
    if only:
        cards = [c for c in cards if published.host_of(c.get("provider_url")) in only]
        extra = [x for x in extra if x.lower() in only]
    state = published.load_state()
    state, rejects, board = published.run(cards, state, extra=extra,
                                          evidence=None if a.no_evidence else sample_pack_evidence)
    published.save(state, site=a.site)
    published.append_rejects(rejects)
    added = [] if a.no_board else published.board_add(board, BOARD)
    live = sum(1 for e in state["cards"].values() if e.get("state") == "live")
    print(f"{state['checked_at']} published cards: {len(state['domains'])} domains read, {live} live, "
          f"{len(state['cards'])} listed, {len(rejects)} refused, {len(added)} board notes -> {published.state_dir()}")
    for i in added:
        print("  board: added", i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
