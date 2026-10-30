"""Render all nine landing stages as a phone visitor sees them: iPhone 15, WebKit, only the area above Safari 26's floating bar.

    uv run --with playwright python tools/ios_check.py OUTDIR [BASE_URL]
Prints, per stage: tabs bottom, viewport height, pane overflow (<= 0 is good), visible words in the pane.
"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
# Preview URL: argv, else $IRLPLS_BASE, else the first line of the gitignored .preview-url at the repo root, else localhost.
_PREVIEW = os.environ.get("IRLPLS_BASE") or (_PU.read_text().strip() if (_PU := pathlib.Path(__file__).resolve().parent.parent / ".preview-url").exists() else "") or "http://localhost:8430/"
BASE = sys.argv[2] if len(sys.argv) > 2 else _PREVIEW

with sync_playwright() as p:
    b = p.webkit.launch()
    d = dict(p.devices["iPhone 15"])
    d["viewport"] = {"width": 390, "height": 709}  # the part of the screen above Safari's floating bottom bar
    pg = b.new_context(**d).new_page()
    for ex in range(3):
        for st in range(3):
            pg.goto(f"{BASE}?ex={ex}&step={st}&still=1")
            pg.wait_for_timeout(900)
            r = pg.evaluate("""()=>{const p=document.getElementById('pane'),s=document.getElementById('stage');
              return [Math.round(document.getElementById('tabs').getBoundingClientRect().bottom), innerHeight,
                      Math.round(p.scrollHeight - s.clientHeight + 72), p.innerText.split(/\\s+/).filter(Boolean).length]}""")
            print(ex, st, r, "OK" if r[0] <= r[1] else "TABS HIDDEN")
            pg.screenshot(path=f"{OUT}/ios-{ex}{st}.png")
    b.close()
