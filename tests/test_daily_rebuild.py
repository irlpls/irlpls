"""The daily rebuild (the owner, 2026-11-02): `build.py --public` reads providers' published cards and runs the evidence
checks itself (never breaking the build), and a GitHub Action asks Cloudflare Pages to rebuild every day."""
import pathlib
import subprocess

import pytest
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


def test_the_public_build_fetches_and_never_breaks(monkeypatch, capsys):
    monkeypatch.delenv("IRLPLS_NO_FETCH", raising=False)
    seen = []

    def boom(cmd, **kw):
        seen.append((cmd, kw))
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(build.subprocess, "run", boom)
    assert build.fresh_published() is False   # a timeout: reported, not raised
    cmd, kw = seen[0]
    assert cmd[1].endswith("tools/fetch_published.py") and "--no-board" in cmd and kw["timeout"] == build.FETCH_BUDGET <= 300
    assert "building from the last state" in capsys.readouterr().out

    monkeypatch.setattr(build.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "network down"))
    assert build.fresh_published() is False
    monkeypatch.setattr(build.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "x published cards: 3 live\n", ""))
    assert build.fresh_published() is True
    monkeypatch.setenv("IRLPLS_NO_FETCH", "1")
    monkeypatch.setattr(build.subprocess, "run", boom)
    assert build.fresh_published() is False and len(seen) == 1   # skipped: nothing run


def test_daily_workflow_asks_cloudflare_and_skips_without_the_secret():
    wf = (ROOT / ".github" / "workflows" / "daily-rebuild.yml").read_text()
    assert 'cron: "30 6 * * *"' in wf and "workflow_dispatch:" in wf
    assert "secrets.CF_DEPLOY_HOOK" in wf and 'if [ -z "$HOOK" ]' in wf and "exit 0" in wf and "curl -fsS -X POST" in wf
    if not (ROOT / "tools" / "stage_public.py").exists():
        pytest.skip("stage_public.py is private; not in the public repo")
    stage = (ROOT / "tools" / "stage_public.py").read_text()
    assert ".github" not in stage.split("EXCLUDE_DIRS", 1)[1].split("}", 1)[0]   # kept in the public export
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert 'IRLPLS_NO_FETCH: "1"' in ci
    assert "Deploy hooks" in (ROOT / "docs" / "GO-LIVE.md").read_text()
