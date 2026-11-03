"""See irlpls work, locally, before anything is published.

    python3 examples/try_locally.py            # from a checkout, after `uv build` (wheels in dist/)

Installs the built wheels into a throwaway virtualenv (your system Python is untouched), then runs what a stranger's
agent would run: the CLI, then the MCP server over stdio with a real MCP client. Needs internet for the live reads
(public, keyless services). Nothing is published, ordered or paid. Removes its venv when done (--keep to look around).
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def run(py_bin, *args, show=True, limit=18):
    r = subprocess.run([str(py_bin), *args], capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip().splitlines()
    if show:
        for line in out[:limit]:
            print("   " + line[:150])
        if len(out) > limit:
            print(f"   ... ({len(out) - limit} more lines)")
    return r.returncode


def step(title):
    print(f"\n== {title}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the virtualenv and print its path")
    keep = ap.parse_args().keep
    wheels = sorted(DIST.glob("*.whl"))
    if len(wheels) < 2:
        sys.exit("no wheels in dist/: run `uv build` in the repo root and in packages/irlpls-mcp (--out-dir ../../dist)")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="irlpls-try-"))
    try:
        step(f"1. install the built wheels into a clean virtualenv ({tmp})")
        subprocess.run([sys.executable, "-m", "venv", str(tmp / "v")], check=True)
        vpy, cli = tmp / "v" / "bin" / "python", tmp / "v" / "bin" / "irlpls"
        r = subprocess.run([str(vpy), "-m", "pip", "install", "-q", *map(str, wheels)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stderr[-800:])
        print("   installed:", ", ".join(w.name for w in wheels))

        step("2. the command line: version")
        run(cli, "--version")

        step("3. which providers fit 'river level in Bristol'? (ranked, no network)")
        run(cli, "find", "river level in Bristol", limit=10)

        step("4. a live answer: what is reporting now near Comfort, TX (USGS water)")
        run(cli, "live", "usgs-water", "--place", "Comfort, TX", limit=10)

        step("5. 'London cameras': should plan the free TfL cameras, not a paid job")
        run(cli, "request", "traffic camera in London", limit=14)

        step("6. an ask irlpls cannot do: 'weather on Mars' must say so, not guess a place")
        run(cli, "request", "weather on Mars", limit=8)

        step("7. a garbled ask: a clear message, no traceback")
        run(cli, "request", "", limit=6)

        step("8. the MCP server over stdio, driven by a real MCP client (what Claude or an IDE does)")
        rc = run(vpy, "-m", "pip", "install", "-q", "mcp>=2", show=False)
        if rc:
            print("   could not install the `mcp` client package (offline?); skipping")
        else:
            run(vpy, str(ROOT / "deploy" / "mcp_smoke.py"), "--stdio", str(tmp / "v" / "bin" / "irlpls-mcp"), limit=16)

        print("\nDone. Nothing was published, ordered or paid.")
        if keep:
            print("virtualenv kept at", tmp, "(use", cli, "...)")
            return
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


main()
