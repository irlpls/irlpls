"""Copy the data a pip-installed irlpls needs into the package (irlpls/data/), so the wheel works without the repo.

    python3 tools/sync_package_data.py          # copy
    python3 tools/sync_package_data.py --check  # exit 1 if a copy is missing or differs (tests/test_packaging.py)

Copied: schema/**/*.schema.json -> irlpls/data/schema/ (validate, conformance, protocol models), and
src/places.json -> irlpls/data/places.json (the offline gazetteer, irlpls/places.py). cards.json and the public suffix
list already live in irlpls/data/. In a checkout the code still prefers the repo copies (irlpls/paths.py).
"""
import filecmp
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "irlpls" / "data"


def pairs():
    for f in sorted((ROOT / "schema").rglob("*.schema.json")):
        yield f, DATA / "schema" / f.relative_to(ROOT / "schema")
    yield ROOT / "src" / "places.json", DATA / "places.json"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    bad = []
    for src, dst in pairs():
        if "--check" in argv:
            if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
                bad.append(str(dst.relative_to(ROOT)))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    want = {d for _, d in pairs()}
    stale = [p for p in (DATA / "schema").rglob("*.json") if p not in want] if (DATA / "schema").exists() else []
    if "--check" in argv:
        bad += [str(p.relative_to(ROOT)) + " (no source)" for p in stale]
        if bad:
            print("package data out of date (run python3 tools/sync_package_data.py):\n  " + "\n  ".join(bad))
            return 1
        print("package data in sync")
        return 0
    for p in stale:
        p.unlink()
    print(f"synced {len(want)} files into irlpls/data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
