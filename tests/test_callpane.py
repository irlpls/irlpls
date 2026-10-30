"""The ready-call pane (src/callpane.js), as Find data shows it for a top match this browser can't run: provider, readiness
pill, price, the call as HTTP / curl / Python with Copy, the key only ever named. Nothing is sent to the provider.

    uv run --with pytest --with playwright python -m pytest -q tests/test_callpane.py
"""
import functools
import http.server
import pathlib
import re
import socketserver
import threading
import urllib.parse

import pytest

pw = pytest.importorskip("playwright.sync_api")
SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
STORE = "photo of the storefront at 50 Elm St, Springfield, IL"
SAT = "satellite image of Austin, TX"
SECRETISH = re.compile(r"[A-Za-z0-9_\-]{20,}")


@pytest.fixture(scope="module")
def base():
    if not (SITE / "find.html").exists() or not (SITE / "callpane.js").exists():
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


def finder(browser, base, q, w=390, h=844):
    ctx = browser.new_context(viewport={"width": w, "height": h})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
    p = ctx.new_page()
    sent = []
    p.on("request", lambda r: sent.append(r.url))
    p.route("https://**/*", lambda r: r.abort())  # nothing leaves for a provider
    p.goto(base + "/find.html?q=" + urllib.parse.quote(q))
    p.wait_for_selector("#bcall [data-ircp] pre.call", timeout=6000)
    return p, sent


@pytest.mark.parametrize("q", [STORE, SAT])
def test_pane_renders_highlighted_and_never_sends(browser, base, q):
    p, sent = finder(browser, base, q)
    pane = p.locator("#bcall [data-ircp]")
    assert pane.locator(".ircp-n").inner_text().strip()
    assert pane.locator(".ircp-r").inner_text() in ("Ready now", "One sign-up", "Account first", "Contract")
    assert pane.locator(".ircp-p").inner_text().strip()
    http = pane.locator("pre.call").inner_text()
    assert re.match(r"^(GET|POST|PUT|PATCH|DELETE) \S+", http), http
    # the method is a coloured pill, the key a highlighted placeholder token
    assert pane.locator("pre.call .m").first.inner_text() in ("GET", "POST", "PUT", "PATCH", "DELETE")
    host = urllib.parse.urlsplit(http.split()[1]).hostname
    phs = pane.locator("pre.call .t-ph").all_inner_texts()
    assert any(re.search(r"_(API_)?KEY\}$|_TOKEN\}$", t) for t in phs), phs  # the key, only named
    for t in phs:
        assert re.fullmatch(r"\{[A-Z0-9_]+\}", t), t
    # no header value carries anything that looks like a real secret
    for line in http.split("\n\n")[0].split("\n")[1:]:
        k, _, v = line.partition(": ")
        assert not SECRETISH.search(re.sub(r"\{[A-Z0-9_]+\}", "", v)), line
    # JSON body highlighted: keys and strings
    assert pane.locator("pre.call .t-key").count() > 0
    assert pane.get_by_role("link", name="Get a key").count() == 1
    assert "Nothing is sent from this page." in pane.inner_text()
    assert not [u for u in sent if host in u], sent


def test_tabs_switch_and_copy_copies_the_visible_view(browser, base):
    p, _ = finder(browser, base, STORE)
    pane = p.locator("#bcall [data-ircp]")
    pre = lambda: pane.locator("pre.ircp-code:not([hidden])")
    assert pre().count() == 1 and pre().inner_text().startswith("POST ")
    pane.get_by_role("tab", name="curl").click()
    t = pre().inner_text()
    assert t.startswith("curl -X POST 'https://api.proxypics.com/") and '"x-api-key: ${PROXYPICS_API_KEY}"' in t, t
    assert pane.get_by_role("tab", name="curl").get_attribute("aria-selected") == "true"
    pane.locator(".ircp-cp").click()
    p.wait_for_function("document.querySelector('.ircp-cp b').textContent === 'Copied'", timeout=2000)
    assert p.evaluate("navigator.clipboard.readText()") == t
    pane.get_by_role("tab", name="Python").click()
    t = pre().inner_text()
    assert t.startswith("import os\nimport requests") and 'os.environ["PROXYPICS_API_KEY"]' in t and "requests.post(" in t, t
    assert '"address": "50 Elm St, Springfield, IL"' in t
    pane.locator(".ircp-cp").click()
    p.wait_for_timeout(200)
    assert p.evaluate("navigator.clipboard.readText()") == t
    # keyboard: arrows move between tabs
    pane.get_by_role("tab", name="Python").press("ArrowRight")
    assert pane.get_by_role("tab", name="HTTP").get_attribute("aria-selected") == "true"


@pytest.mark.parametrize("w", [375, 390])
def test_code_scrolls_inside_never_the_page(browser, base, w):
    for q in (STORE, SAT):
        p, _ = finder(browser, base, q, w=w)
        for tab in ("HTTP", "curl", "Python"):
            p.locator("#bcall").get_by_role("tab", name=tab).click()
            assert p.evaluate("document.documentElement.scrollWidth") <= w, (q, tab)
            r = p.evaluate("""(() => { const e = document.querySelector('#bcall pre.ircp-code:not([hidden])'), cs = getComputedStyle(e), b = e.getBoundingClientRect();
              return {sw: e.scrollWidth, cw: e.clientWidth, ox: cs.overflowX, ws: cs.whiteSpace, right: b.right, fs: parseFloat(cs.fontSize)} })()""")
            assert r["ox"] == "auto" and r["ws"] == "pre" and r["right"] <= w and r["fs"] >= 12, r
        # the long lines are there, scrolled inside the block
        assert r["sw"] > r["cw"]
        # every tap target in the pane is at least 44px
        small = p.evaluate("""[...document.querySelectorAll('#bcall [data-ircp] button, #bcall [data-ircp] a')].filter(e => e.getClientRects().length)
          .map(e => e.getBoundingClientRect()).filter(b => b.height < 43.5 || b.width < 43.5).length""")
        assert small == 0


def test_variants_fill_the_ask_and_name_the_key_only(browser, base):
    p, _ = finder(browser, base, STORE)
    v = p.evaluate("""IRLCall.variants({rc: {method: 'POST', url: 'https://x.example/v1/{ACCT_ID}/orders?q={NEED}', headers: {Authorization: 'Bearer {X_KEY}'},
        body: {lat: '{LAT}', note: '{NEED}', n: 2, ok: true, none: null}, auth: {key_name: 'X_KEY'}},
        text: 'say "hi"', req: {}, place: {lat: 1.5, lon: 2.25, name: 'Here'}})""")
    assert v["http"].splitlines()[0] == "POST https://x.example/v1/{ACCT_ID}/orders?q=say%20%22hi%22"
    assert "Authorization: Bearer {X_KEY}" in v["http"] and '"lat": 1.5' in v["http"] and '"note": "say \\"hi\\""' in v["http"]
    assert '-H "Authorization: Bearer ${X_KEY}"' in v["curl"]
    assert '"Authorization": "Bearer " + os.environ["X_KEY"]' in v["python"] and '"ok": True' in v["python"] and '"none": None' in v["python"]
