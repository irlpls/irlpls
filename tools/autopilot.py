#!/usr/bin/env python3
"""Autopilot: one command that checks everything that can break after launch and says only what needs action.

    python3 tools/autopilot.py                 # full local run: prints markdown, writes ops/status.json + ops/status.md
    python3 tools/autopilot.py --public        # public-safe subset (what the GitHub Action runs; no local files, no keys)
    python3 tools/autopilot.py --notify        # also message the owner (local only, once per distinct failure set)
    python3 tools/autopilot.py --skip-health   # do not re-run the provider read check, just read the last health.json

Checks (status ok | warn | fail | skip). Exit 1 only if something is `fail` (needs action); warn is informational.
  site        irlpls.com answers and its catalog.json was generated within SITE_MAX_AGE_DAYS (the daily rebuild works)
  www         www.irlpls.com redirects to the apex
  ci          latest CI run on the public repo's default branch passed (read-only gh api)
  rebuild     the daily-rebuild workflow ran successfully within 2 days
  providers   health.json: providers failing 2+ days (local only; --public skips)
  due         docs/REVISIT.md items overdue (fail) or due within 14 days (warn); cards expiring within 30 days
  mcp         $IRLPLS_MCP_URL/healthz (skip when unset)
  pypi        published versions of irlpls and irlpls-mcp (skip when 404 = not published)
Never writes to GitHub, never sends mail, never spends money. The only side effect of --notify is one iMessage.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = os.environ.get("IRLPLS_SITE", "https://irlpls.com")
REPO = os.environ.get("IRLPLS_PUBLIC_REPO", "irlpls/irlpls")
SITE_MAX_AGE_DAYS = 2
UA = "irlpls-autopilot/1 (+https://irlpls.com)"


def _get(url, timeout=15, follow=True):
    """-> (status, headers, body_bytes); status 0 on network error."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""
    except Exception as e:  # noqa: BLE001
        return 0, {}, str(e).encode()


def R(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def check_site(today):
    st, _, body = _get(SITE + "/catalog.json")
    if st != 200:
        return R("site", "fail", f"{SITE}/catalog.json answered {st or 'no connection'}")
    try:
        gen = datetime.date.fromisoformat(json.loads(body)["generated"])
    except Exception:  # noqa: BLE001
        return R("site", "fail", "catalog.json has no readable 'generated' date")
    age = (today - gen).days
    if age > SITE_MAX_AGE_DAYS:
        return R("site", "fail", f"served build is {age} days old (generated {gen}); the daily rebuild is not landing")
    return R("site", "ok", f"up, build generated {gen}")


def check_www():
    st, h, _ = _get("https://www." + SITE.split("://", 1)[1], follow=False)
    loc = (h.get("Location") or "") if h else ""
    if st in (301, 302, 307, 308) and SITE.split("://", 1)[1] in loc:
        return R("www", "ok", f"redirects ({st}) to {loc}")
    return R("www", "fail", f"www answered {st} with Location '{loc}', expected a redirect to the apex")


def _gh_api(path):
    """Read-only GitHub API: GH_TOKEN/GITHUB_TOKEN if set, else the project gh account, else anonymous."""
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        try:
            tok = subprocess.run(["gh", "auth", "token", "--user", "irlpls-maintainer"], capture_output=True,
                                 text=True, timeout=10).stdout.strip() or None
        except Exception:  # noqa: BLE001
            tok = None
    hdr = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    try:
        with urllib.request.urlopen(urllib.request.Request("https://api.github.com/" + path, headers=hdr), timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def check_ci():
    st, d = _gh_api(f"repos/{REPO}/actions/workflows/ci.yml/runs?per_page=1&status=completed")
    if st == 404:
        return R("ci", "skip", "public repo or ci workflow not there yet")
    if st != 200 or d is None:
        return R("ci", "warn", f"could not read CI status (HTTP {st})")
    runs = d.get("workflow_runs") or []
    if not runs:
        return R("ci", "skip", "no completed CI runs yet")
    r = runs[0]
    if r["conclusion"] == "success":
        return R("ci", "ok", f"last CI run passed ({r['head_branch']}, {r['updated_at'][:10]})")
    return R("ci", "fail", f"last CI run {r['conclusion']}: {r['html_url']}")


def check_rebuild(now):
    st, d = _gh_api(f"repos/{REPO}/actions/workflows/daily-rebuild.yml/runs?per_page=1&event=schedule")
    if st == 404:
        return R("rebuild", "skip", "daily-rebuild workflow not on the public repo yet")
    if st != 200 or d is None:
        return R("rebuild", "warn", f"could not read daily-rebuild runs (HTTP {st})")
    runs = d.get("workflow_runs") or []
    if not runs:
        return R("rebuild", "warn", "daily-rebuild has not run on schedule yet")
    r = runs[0]
    when = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
    age_h = (now - when).total_seconds() / 3600
    if r["conclusion"] not in (None, "success"):
        return R("rebuild", "fail", f"last daily-rebuild {r['conclusion']}: {r['html_url']}")
    if age_h > 48:
        return R("rebuild", "fail", f"last scheduled daily-rebuild was {age_h / 24:.1f} days ago")
    return R("rebuild", "ok", f"last scheduled run {when:%Y-%m-%d %H:%M}Z {r['conclusion'] or 'running'}")


def check_providers(skip_run):
    if not skip_run:
        try:
            subprocess.run([sys.executable, str(ROOT / "tools" / "healthcheck.py"), "--no-board"],
                           cwd=ROOT, capture_output=True, timeout=240)
        except Exception:  # noqa: BLE001
            pass
    f = ROOT / "irlpls" / "data" / "health.json"
    if not f.exists():
        return R("providers", "skip", "no health.json (healthcheck has not run here)")
    try:
        d = json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return R("providers", "warn", "health.json unreadable")
    provs = d.get("providers", d)
    bad, today = [], datetime.date.today()
    for cid, p in provs.items():
        if not isinstance(p, dict) or p.get("ok", True):
            continue
        days = {h.get("day") or h.get("date") for h in p.get("history", []) if not h.get("ok", False)}
        yest = (today - datetime.timedelta(days=1)).isoformat()
        if today.isoformat() in days and yest in days:
            bad.append(f"{cid} ({p.get('reason', 'failed')})")
    if bad:
        return R("providers", "fail", f"{len(bad)} provider read(s) failing 2+ days: " + "; ".join(sorted(bad)[:8]))
    n_fail = sum(1 for p in provs.values() if isinstance(p, dict) and not p.get("ok", True))
    return R("providers", "ok" if not n_fail else "warn", f"{len(provs)} checked, {n_fail} failing today (not yet 2 days)")


def check_due(today, warn_days=14):
    out, late, soon = [], [], []
    rv = ROOT / "docs" / "REVISIT.md"
    if rv.exists():
        for line in rv.read_text().splitlines():
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*open\s*\|\s*(.*?)\s*\|", line)
            if m:
                gap = (datetime.date.fromisoformat(m.group(1)) - today).days
                label = f"{m.group(1)} {m.group(2)[:70]}"
                if gap < 0:
                    late.append(label)
                elif gap <= warn_days:
                    soon.append(label)
    exp = []
    for c in (ROOT / "cards").glob("*.json"):
        try:
            e = json.loads(c.read_text()).get("expires")
            if e and (datetime.date.fromisoformat(e) - today).days <= 30:
                exp.append(f"{c.stem} expires {e}")
        except Exception:  # noqa: BLE001
            pass
    if not rv.exists() and not (ROOT / "cards").exists():
        return R("due", "skip", "no REVISIT.md or cards here")
    if late or exp:
        return R("due", "fail", "; ".join([f"overdue: {x}" for x in late] + exp[:5]))
    if soon:
        return R("due", "warn", "due soon: " + "; ".join(soon))
    return R("due", "ok", "nothing overdue or due within %d days" % warn_days)


def check_mcp():
    url = os.environ.get("IRLPLS_MCP_URL", "").rstrip("/")
    if not url:
        return R("mcp", "skip", "IRLPLS_MCP_URL not set (MCP endpoint not deployed)")
    st, _, _ = _get(url + "/healthz")
    return R("mcp", "ok" if st == 200 else "fail", f"{url}/healthz answered {st or 'no connection'}")


def check_pypi():
    found, parts = False, []
    for pkg in ("irlpls", "irlpls-mcp"):
        st, _, body = _get(f"https://pypi.org/pypi/{pkg}/json")
        if st == 200:
            found = True
            parts.append(f"{pkg} {json.loads(body)['info']['version']}")
        elif st != 404:
            return R("pypi", "warn", f"PyPI answered {st} for {pkg}")
    return R("pypi", "ok", ", ".join(parts)) if found else R("pypi", "skip", "not published")


def run(public, skip_health, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    checks = [check_site(today), check_www(), check_ci(), check_rebuild(now), check_due(today), check_mcp(), check_pypi()]
    if not public:
        checks.insert(4, check_providers(skip_health))
    return {"generated": now.isoformat(timespec="seconds"), "scope": "public" if public else "full",
            "needs_action": any(c["status"] == "fail" for c in checks), "checks": checks}


def to_markdown(s):
    mark = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
    head = "NEEDS ACTION" if s["needs_action"] else "all clear"
    lines = [f"# irlpls autopilot: {head}", f"{s['generated']} ({s['scope']})", ""]
    lines += [f"- [{mark[c['status']]}] {c['name']}: {c['detail']}" for c in s["checks"]]
    return "\n".join(lines) + "\n"


def notify(s):
    """One local message to the owner via a user-supplied script, only when the set of failures changed since the
    last notification (state in ops/last_notified)."""
    fails = sorted(c["name"] for c in s["checks"] if c["status"] == "fail")
    state = ROOT / "ops" / "last_notified"
    prev = state.read_text().strip() if state.exists() else ""
    cur = ",".join(fails)
    if cur == prev:
        return "unchanged"
    state.parent.mkdir(exist_ok=True)
    state.write_text(cur)
    im = Path.home() / "me" / "input" / "imessage"
    try:
        handle = json.loads((im / "config.json").read_text())["allowed_senders"][0]
        text = ("irlpls: all clear again" if not fails else "irlpls needs you: " + "; ".join(
            f"{c['name']} - {c['detail'][:80]}" for c in s["checks"] if c["status"] == "fail")
                + " (see docs/OPS.md)")
        subprocess.run([str(im / "lib" / "reply.sh"), text, handle], timeout=30, check=True, capture_output=True)
        return "sent"
    except Exception as e:  # noqa: BLE001
        return f"notify failed: {e}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public", action="store_true", help="public-safe subset; writes nothing unless --json/--md given")
    ap.add_argument("--json", help="write status JSON here")
    ap.add_argument("--md", help="write markdown here")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--skip-health", action="store_true")
    a = ap.parse_args(argv)
    s = run(a.public, a.skip_health)
    md = to_markdown(s)
    print(md)
    jp, mp = a.json, a.md
    if not a.public:
        (ROOT / "ops").mkdir(exist_ok=True)
        jp = jp or str(ROOT / "ops" / "status.json")
        mp = mp or str(ROOT / "ops" / "status.md")
    if jp:
        Path(jp).write_text(json.dumps(s, indent=2))
    if mp:
        Path(mp).write_text(md)
    if a.notify and not a.public:
        print("notify:", notify(s))
    return 1 if s["needs_action"] else 0


if __name__ == "__main__":
    sys.exit(main())
