"""Capture what a visitor sees on the landing, on a phone, as a numbered series of screenshots.

    uv run --with playwright python tools/persona_view.py OUTDIR [URL]

Writes OUTDIR/01-chat-0s.png ... in reading order: each screen at a few moments so animations are seen as a person would
(first moments, then settled). URL defaults to the preview. Also follows the three links a visitor most likely taps and
saves their first screen (agents, benchmark, spec). Read the PNGs; do not read the repository."""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
# Preview URL: argv, else $IRLPLS_BASE, else the first line of the gitignored .preview-url at the repo root, else localhost.
_PREVIEW = os.environ.get("IRLPLS_BASE") or (_PU.read_text().strip() if (_PU := pathlib.Path(__file__).resolve().parent.parent / ".preview-url").exists() else "") or "http://localhost:8430/"
base = (sys.argv[2] if len(sys.argv) > 2 else _PREVIEW).rstrip("/") + "/"
SCREENS = [("chat", "s1", [0.6, 1.8, 3.5, 6]), ("chain", "how", [1]), ("try", "try", [1])]  # the landing's three <section>s
with sync_playwright() as pw:
    b = pw.webkit.launch()
    pg = b.new_page(viewport={"width": 390, "height": 709}, device_scale_factor=2)
    pg.goto(base)
    pg.wait_for_timeout(500)
    n = 0
    for name, sid, times in SCREENS:
        pg.evaluate(f"document.getElementById('{sid}').scrollIntoView()")
        t0 = 0
        for t in times:
            pg.wait_for_timeout(int((t - t0) * 1000)); t0 = t
            n += 1
            pg.screenshot(path=str(out / f"{n:02d}-{name}-{int(t)}s.png"))
    for label, path in [("agents", "agents.html"), ("benchmark", "find.html"), ("spec", "standard.html")]:
        p2 = b.new_page(viewport={"width": 390, "height": 709}, device_scale_factor=2)
        p2.goto(base + path); p2.wait_for_timeout(1500)
        n += 1
        p2.screenshot(path=str(out / f"{n:02d}-link-{label}.png"))
        p2.close()
    b.close()
print("wrote", n, "screenshots to", out)
