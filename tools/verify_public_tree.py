#!/usr/bin/env python3
"""Run the test suite against the tree that would be published (what public CI sees), before `publish_public.py`.

    python3 tools/verify_public_tree.py [PYTHON]      # PYTHON: an interpreter that has pytest, playwright, cryptography, jsonschema

Stages HEAD into a temp dir with tools/stage_public.py --head, builds the public site there and runs pytest. Private files
(docs/OPS.md, docs/research/, ...) are absent, so a test that depends on one fails here, not in CI. Takes about nine minutes.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    py = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    with tempfile.TemporaryDirectory(prefix="irlpls-pubtree-") as tmp:
        dest = pathlib.Path(tmp) / "tree"
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "stage_public.py"), str(dest), "--head"])
        if r.returncode:
            sys.exit("stage_public did not finish clean")
        env = {**__import__("os").environ, "IRLPLS_NO_FETCH": "1"}
        subprocess.run([py, "build.py", "--public"], cwd=dest, env=env, stdout=subprocess.DEVNULL, check=True)
        sys.exit(subprocess.run([py, "-m", "pytest", "-q", "tests", "-x"], cwd=dest, env=env).returncode)


if __name__ == "__main__":
    main()
