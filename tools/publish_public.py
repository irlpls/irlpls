"""Publish the clean public copy to github.com/irlpls/irlpls as the project account. The owner approves each use.

usage: python3 tools/publish_public.py "commit message"

1. Re-stage from HEAD (tools/stage_public.py --head): fresh copy, privacy scan, stops on any leak.
2. Commit that tree on top of the public history (fetch origin/main, reset --soft), authored as the project identity.
3. Push with the gh account `irlpls-maintainer`, then switch gh back to whichever account was active before.
Never pushes with the owner's personal account. Cloudflare Pages rebuilds irlpls.com from the push.
"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAGE = ROOT.parent / "irlpls-public-staging"
REMOTE = "https://github.com/irlpls/irlpls.git"
ACCOUNT = "irlpls-maintainer"
IDENT = ["-c", "user.name=irlpls maintainers", "-c", "user.email=hello@irlpls.com"]
CRED = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


def run(*cmd, cwd=STAGE, capture=False):
    r = subprocess.run(list(cmd), cwd=cwd, check=True, text=True, capture_output=capture)
    return r.stdout if capture else ""


def active_account():
    out = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True).stdout + \
        subprocess.run(["gh", "auth", "status"], capture_output=True, text=True).stderr
    m = re.search(r"account (\S+)[^\n]*\n\s*- Active account: true", out)
    return m.group(1) if m else None


def main():
    msg = sys.argv[1] if len(sys.argv) > 1 else "Update"
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "stage_public.py"), "--head"], cwd=ROOT,
                         capture_output=True, text=True)
    print(out.stdout[-400:])
    if out.returncode or "scan: clean" not in out.stdout:
        sys.exit("stage_public did not finish clean; nothing pushed")
    before = active_account()
    run("gh", "auth", "switch", "--user", ACCOUNT)
    try:
        run("git", "remote", "add", "origin", REMOTE)
        run("git", *CRED, "fetch", "-q", "origin", "main")
        run("git", "reset", "-q", "--soft", "origin/main")
        if not run("git", "status", "--porcelain", capture=True).strip():
            print("nothing changed since the last public commit; nothing pushed")
            return
        run("git", *IDENT, "commit", "-q", "-m", msg)
        run("git", *CRED, "push", "-q", "origin", "HEAD:main")
        print(run("git", "log", "--format=%h %an | %s", "-1", capture=True).strip())
    finally:
        if before and before != ACCOUNT:
            run("gh", "auth", "switch", "--user", before)


main()
