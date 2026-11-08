"""Contributor docs cannot go stale: every family id is listed, and 'the gate' is one definition in both docs."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls.catalog import FAMILY_DEFS  # noqa: E402

ADD = (ROOT / "docs" / "ADD-A-CARD.md").read_text()
CONTRIB = (ROOT / "CONTRIBUTING.md").read_text()
GATE_CMDS = ("python3 tools/gate.py cards/<id>.json", "python3 tools/check_cards.py", "python3 build.py")


def test_every_family_id_in_add_a_card():
    missing = [d[0] for d in FAMILY_DEFS if f"`{d[0]}`" not in ADD]
    assert not missing, f"docs/ADD-A-CARD.md lacks family ids: {missing}"


def test_gate_defined_identically():
    for cmd in GATE_CMDS:
        assert cmd in ADD and cmd in CONTRIB, cmd


def test_no_stale_phrases():
    assert "may need a new row" not in CONTRIB
    assert "FAMILY_DEFS" not in ADD and "FAMILY_DEFS" not in CONTRIB
    for t in (ADD, CONTRIB):
        assert "github.com" not in t and not re.search(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.", t)
    assert "release scan" in ADD and "release check" in CONTRIB
