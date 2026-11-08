"""Contributor round 1: plain build/gate failures, validate warnings, the starter card, the localhost rehearsal."""
import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from irlpls import validate  # noqa: E402

TEMPLATE = ROOT / "examples" / "card-template.json"


def card():
    return json.loads(TEMPLATE.read_text())


def msgs(c, sev=None, **kw):
    return [(w, m) for s, w, m in validate.problems(c, **kw) if sev in (None, s)]


def test_template_is_clean_and_passes_gate():
    assert not msgs(card(), "error") and not msgs(card(), "warn")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_cards.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert (ROOT / "examples" / "card-template.md").exists()
    assert "Example Weather Co" in TEMPLATE.read_text()


def test_unknown_key_typo_warns_with_suggestion():
    c = card()
    c["licencing"] = {}
    w = [m for _, m in msgs(c, "warn")]
    assert any("did you mean 'licensing'" in m for m in w)
    assert not msgs(c, "error")


def test_bad_date_empty_unknown_and_local_provider_maintained_warn():
    c = card()
    c["updated"] = "06/11/2026"
    c["verification"]["human"]["unknown"] = ""
    c["status"] = "provider-maintained"
    ws = msgs(c, "warn")
    assert any(w == "updated" and "YYYY-MM-DD" in m for w, m in ws)
    assert any(w == "verification.human.unknown" for w, m in ws)
    assert any(w == "status" and "own domain" in m for w, m in ws)
    # the same card served from its own domain does not warn about status
    assert not any(w == "status" for w, m in msgs(c, "warn", fetched_from="https://example.com/.well-known/irlpls"))


@pytest.mark.parametrize("key", ["short", "access", "api"])
def test_hidden_requirements_are_reported(key):
    c = card()
    del c[key]
    assert any(w == key and "needed to be listed" in m for w, m in msgs(c, "warn"))


def test_errors_are_short_and_never_echo_regex_or_huge_values():
    c = card()
    c["id"] = "Bad_Id"
    c["provider_url"] = "http://x.example/" + "a" * 5000
    c["summary"] = "s" * 9000
    del c["pricing"]
    errs = msgs(c, "error")
    assert errs
    for w, m in errs:
        assert len(m) < 260 and "^" not in m and "\\" not in m, (w, m[:120])
    assert any(w == "pricing" and "from_usd" in m for w, m in errs)
    assert any(w == "id" and "lowercase" in m for w, m in errs)
    assert len({(w, m) for w, m in errs}) == len(errs)


def _tree(tmp_path, ids):
    d = tmp_path / "cards"
    d.mkdir()
    for name, cid in ids:
        c = card()
        c["id"] = cid
        (d / f"{name}.json").write_text(json.dumps(c))
    return d


def test_repo_problems_family_duplicate_and_filename(tmp_path):
    d = _tree(tmp_path, [("example-weather-co", "example-weather-co"), ("other", "example-weather-co")])
    out = "\n".join(validate.repo_problems(d))
    assert "example-weather-co: not in any family" in out and '"family": "<id>"' in out
    assert "other: id 'example-weather-co' must equal the file name" in out
    assert "duplicate id" in out


def test_current_catalog_has_no_repo_problems():
    assert validate.repo_problems(ROOT / "cards") == []


def _repo_copy(tmp_path):
    """A private copy of what build.py and tools/check_cards.py read, so nothing is written into the real repo."""
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache", "tests"))
    c = json.loads((ROOT / "cards" / "airnow.json").read_text())
    c["id"] = "example-weather-co"
    c.pop("family", None)   # a card with no family
    (dst / "cards" / "example-weather-co.json").write_text(json.dumps(c))
    return dst


def test_build_fails_plainly_without_traceback(tmp_path):
    dst = _repo_copy(tmp_path)
    r = subprocess.run([sys.executable, str(dst / "build.py")], capture_output=True, text=True, cwd=dst,
                       env={**__import__("os").environ, "IRLPLS_NO_FETCH": "1"})
    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert "example-weather-co: not in any family" in r.stderr and '"family": "<id>"' in r.stderr


def test_check_cards_says_the_same(tmp_path):
    dst = _repo_copy(tmp_path)
    r = subprocess.run([sys.executable, str(dst / "tools" / "check_cards.py")], capture_output=True, text=True, cwd=dst)
    assert r.returncode == 1 and "example-weather-co: not in any family" in r.stdout and '"family": "<id>"' in r.stdout
