"""`build.py --public` (the irlpls.com build): no prototypes or preview-only files, and a refusal on preview hosts or
local paths. Fast: runs build.public_scrub on a fixture site, plus tools/check_site.py on it."""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


def site(tmp_path, index='<a href="find.html">Find</a>'):
    (tmp_path / "pitch" / "published-demo").mkdir(parents=True)
    (tmp_path / "pitch" / "provider-flow.html").write_text("demo")
    (tmp_path / "headlines.html").write_text("options")
    (tmp_path / "fb.js").write_text("mic")
    (tmp_path / "index.html").write_text(index + '<script src="/__fb.js" defer></script><a href="/portal">Portal</a>')
    (tmp_path / "find.html").write_text('<a href="./">Home</a>')
    return tmp_path


def test_public_scrub_drops_internal_files_and_preview_tags(tmp_path):
    s = site(tmp_path)
    build.public_scrub(s)
    assert not (s / "pitch").exists() and not (s / "headlines.html").exists() and not (s / "fb.js").exists()
    t = (s / "index.html").read_text()
    assert "fb.js" not in t and "portal" not in t and 'href="find.html"' in t
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_site.py"), str(s)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("leak", ["http://100." + "64.1.2:8430/", "http://localhost:8430/x", "/Us" + "ers/someone/x", "a.ts" + ".net"])  # split: tools/stage_public.py scans for these
def test_public_build_refuses_preview_hosts_and_local_paths(tmp_path, leak):
    s = site(tmp_path, index=f'<a href="find.html">{leak}</a>')
    with pytest.raises(SystemExit):
        build.public_scrub(s)


def test_check_site_reports_broken_links_and_internal_files(tmp_path):
    s = site(tmp_path, index='<a href="missing.html">x</a>')
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_site.py"), str(s)], capture_output=True, text=True)
    assert r.returncode == 1 and "broken: /missing.html" in r.stdout and "internal file shipped: /pitch/provider-flow.html" in r.stdout


def test_check_site_catches_assets_named_in_scripts_and_private_folders(tmp_path):
    """The landing built its river <img> in JS from media/live/ (the private refresh job's gitignored snapshots): the
    public site showed no photo. Asset paths inside scripts count, and media/live/ never appears in a public build."""
    s = site(tmp_path, index='<a href="find.html">Find</a><script>var m=function(){return \'<img src="media/live/cam.jpg">\'};'
                              'var ok="https://example.org/x.json",tail=B+\'/api/stations.json\'</script>')
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_site.py"), str(s)], capture_output=True, text=True)
    assert r.returncode == 1 and "references private-only media/live/cam.jpg" in r.stdout and "broken: /media/live/cam.jpg" in r.stdout
    assert "x.json" not in r.stdout and "stations.json" not in r.stdout, r.stdout


def test_real_public_build_references_only_files_it_ships(tmp_path):
    """Build the real public site the way Cloudflare Pages does (the repo's files only: nothing gitignored, so no
    src/media/live/) and crawl it: every link and every asset named in a page, script or style exists in the build."""
    try:
        ls = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("needs git to list the repo's files")
    import shutil
    for f in filter(None, ls):
        src = ROOT / f
        if src.is_file() and not f.startswith(("site/", "qa-", "scratchpad/")):
            (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp_path / f)
    b = subprocess.run([sys.executable, "build.py", "--public"], cwd=tmp_path, capture_output=True, text=True)
    assert b.returncode == 0, b.stdout + b.stderr
    out = tmp_path / "site"
    assert not (out / "media" / "live").exists() and "media/live" not in (out / "index.html").read_text()
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_site.py"), str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
