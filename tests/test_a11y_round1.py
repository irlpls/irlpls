"""A11y round 1 (docs/research/launch/A11Y-PERF-2026-11.md findings 2, 3, 5, 6-8), pinned at 390 px.
Builds nothing: run `python3 build.py` first."""
import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")
SITE = pathlib.Path(__file__).resolve().parent.parent / "site"


@pytest.fixture(scope="module")
def base():
    if not (SITE / "index.html").exists():
        pytest.skip("site/ not built")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    pg = ctx.new_page()
    yield pg
    ctx.close()


def heights(pg, sel):
    return pg.eval_on_selector_all(sel, "els=>els.filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect();return [e.textContent.trim().slice(0,30),r.height]})")


TARGETS = [
    ("index.html", ".site-foot a.fw"),
    ("standard.html", ".site-foot a.fw"),
    ("standard.html", ".toc a"),
    ("agents.html", ".toc a"),
    ("c/usgs-water.html", ".site-foot a.fw"),
    ("c/usgs-water.html", ".api dd a"),
    ("c/usgs-water.html", ".stg-l a"),
    ("c/usgs-water.html", "a.fix"),
]


@pytest.mark.parametrize("path,sel", TARGETS)
def test_touch_targets_44(base, page, path, sel):
    page.goto(f"{base}/{path}")
    if "toc" in sel:
        page.evaluate("document.querySelectorAll('details.toc').forEach(d=>d.open=true)")
    hs = heights(page, sel)
    assert hs, f"{sel} not found on {path}"
    small = [h for h in hs if h[1] < 43.5]
    assert not small, small


@pytest.mark.parametrize("path", ["index.html", "docs.html", "standard.html", "c/usgs-water.html"])
def test_no_reading_text_under_12px(base, page, path):
    page.goto(f"{base}/{path}")
    page.evaluate("document.querySelectorAll('details').forEach(d=>d.open=true)")
    bad = page.evaluate("""()=>{const out=[];const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
      while(w.nextNode()){const n=w.currentNode;if(!n.textContent.trim())continue;const e=n.parentElement;
        if(e.closest('script,style,svg,.smp,[aria-hidden=true]'))continue;
        const cs=getComputedStyle(e);if(cs.display==='none'||cs.visibility==='hidden')continue;
        const r=e.getBoundingClientRect();if(!r.width||!r.height)continue;
        if(parseFloat(cs.fontSize)<11.99)out.push([e.tagName,n.textContent.trim().slice(0,24),cs.fontSize])}
      return out}""")
    assert not bad, bad[:10]


def test_index_svgs_decorative_hidden(base, page):
    page.goto(f"{base}/index.html")
    bad = page.evaluate("""()=>[...document.querySelectorAll('svg')].filter(s=>!s.closest('[aria-hidden=true]')&&s.getAttribute('aria-hidden')!=='true'&&!s.getAttribute('aria-label')&&!s.querySelector('title')).length""")
    assert bad == 0


def test_index_images_sized_and_lazy(base, page):
    page.goto(f"{base}/index.html")
    imgs = page.evaluate("[...document.querySelectorAll('img')].map(i=>[i.getAttribute('src'),i.getAttribute('width'),i.getAttribute('height'),i.getAttribute('loading')])") if False else None
    # imgs are rendered by script: check the template source for every media image instead
    src = (SITE / "index.html").read_text()
    import re
    tags = re.findall(r"<img [^>]*src=\"media/[^>]*>", src)
    assert len(tags) >= 4
    for t in tags:
        assert 'width="' in t and 'height="' in t and 'decoding="async"' in t, t
        if "guadalupe" not in t:
            assert 'loading="lazy"' in t, t
        else:
            assert "lazy" not in t


def test_forced_colors_search_ring():
    css = (SITE / "style.css").read_text() if (SITE / "style.css").exists() else ""
    assert "forced-colors:active){.search:focus-within{outline:2px solid Highlight}" in css
