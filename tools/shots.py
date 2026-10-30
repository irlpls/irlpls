#!/usr/bin/env python3
"""Standard screenshot kit for design reviews: python3 tools/shots.py <outdir>
Landing (all 9 states on a 390x844 phone, 3 on a 1440x900 laptop, full below-the-fold), finder, and a card page."""
import os
import pathlib
import subprocess
import sys
import urllib.parse

# Preview URL: argv, else $IRLPLS_BASE, else the first line of the gitignored .preview-url at the repo root, else localhost.
_PREVIEW = os.environ.get("IRLPLS_BASE") or (_PU.read_text().strip() if (_PU := pathlib.Path(__file__).resolve().parent.parent / ".preview-url").exists() else "") or "http://localhost:8430/"
BASE = _PREVIEW.rstrip("/")
CH = pathlib.Path.home() / "Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell"
FLAGS = ["--headless", "--hide-scrollbars", "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--virtual-time-budget=7000"]


def shot(out, name, url, w, h):
    subprocess.run([str(CH), *FLAGS, f"--window-size={w},{h}", f"--screenshot={out / name}", url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    print(name)


def main():
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for e in range(3):
        for s in range(3):
            shot(out, f"landing-mobile-ex{e+1}-stage{s+1}.png", f"{BASE}/?ex={e}&step={s}&still=1", 390, 844)
    for e in range(3):
        shot(out, f"landing-desktop-ex{e+1}-stage3.png", f"{BASE}/?ex={e}&step=2&still=1", 1440, 900)
    shot(out, "landing-mobile-fullpage.png", f"{BASE}/?ex=0&step=2&still=1", 390, 2600)
    shot(out, "landing-desktop-fullpage.png", f"{BASE}/?ex=0&step=2&still=1", 1440, 2400)
    q = urllib.parse.quote("is the Guadalupe at Comfort, TX good for tubing right now")
    shot(out, "finder-mobile.png", f"{BASE}/find.html?q={q}", 390, 844)
    shot(out, "finder-desktop.png", f"{BASE}/find.html?q={q}", 1440, 900)
    shot(out, "card-mobile.png", f"{BASE}/c/usgs-water.html", 390, 1800)
    shot(out, "card-desktop.png", f"{BASE}/c/usgs-water.html", 1440, 1400)


if __name__ == "__main__":
    main()
