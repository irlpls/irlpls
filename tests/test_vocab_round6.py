"""Round 6: the public pages use the current proof vocabulary (eight links; claimed / evidenced / not stated; no "audited"
level). The standard's normative text about the seven `verification` keys and the changelog's history are exempt."""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
STALE = re.compile(r"seven guarantees|audited card|compiled, audited", re.I)
EXEMPT = {"standard.html", "changelog.html"}


@pytest.mark.skipif(not (SITE / "providers.html").exists(), reason="site not built (python3 build.py)")
def test_public_pages_have_no_stale_proof_words():
    bad = []
    for f in sorted(SITE.glob("*.html")) + sorted(SITE.glob("*.md")) + [SITE / "llms.txt"]:
        if f.name in EXEMPT or not f.exists():
            continue
        text = re.sub(r"<[^>]+>", " ", f.read_text(encoding="utf-8"))
        bad += [f"{f.name}: {m.group(0)}" for m in STALE.finditer(text)]
    assert not bad, bad


def test_providers_doc_names_the_eight_links():
    md = (ROOT / "docs" / "PROVIDERS.md").read_text(encoding="utf-8")
    assert "**The eight links**" in md and not STALE.search(md)
    assert "only checks a pack's signature" in md
