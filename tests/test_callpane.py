"""The call pane (src/callpane.js), as Find data shows it for a top match this browser can't run: three flat steps (the
irlpls call as understood, who it matched, the provider call as one curl block), the key only ever named. Nothing is sent.

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
    p.wait_for_selector("#bcall [data-ircp] pre.call", state="attached", timeout=6000)
    # three flat steps: the irlpls call, who it matched, the provider call folded to one line: unfold it
    assert "irlpls_request(" in p.inner_text("#bcall .ircp-ask")
    p.click("#bcall .ircp-x")
    return p, sent


@pytest.mark.parametrize("q", [STORE, SAT])
def test_pane_renders_three_flat_steps_and_never_sends(browser, base, q):
    p, sent = finder(browser, base, q)
    pane = p.locator("#bcall [data-ircp]")
    assert pane.locator(".ircp-tl > li").count() == 3
    assert [t.strip() for t in pane.locator(".ircp-k").all_inner_texts()] == ["Your agent → irlpls", "irlpls → matched", "irlpls → provider"]
    # flat: no tabs, no nested cards, no request log, no intro or plan sentence
    assert pane.locator("[role=tab], .ircp-plan, .ircp-a, .ircp-b, .cls").count() == 0
    assert pane.locator(".ircp-n").inner_text().strip()
    assert pane.locator(".ircp-r").inner_text() in ("Ready now", "One sign-up", "Account first", "Contract")
    # step 3: method pill, host, "not sent from this page"; opened, one curl block with one Copy
    assert pane.locator(".ircp-x .m").inner_text() in ("GET", "POST", "PUT", "PATCH", "DELETE")
    assert "not sent from this page" in pane.locator(".ircp-x").inner_text()
    assert pane.locator("pre.ircp-code").count() == 1 and pane.locator(".ircp-cp").count() == 1
    curl = pane.locator("pre.call").inner_text()
    assert curl.startswith("curl "), curl
    host = urllib.parse.urlsplit(re.search(r"https?://[^\s'\"]+", curl).group(0)).hostname
    phs = pane.locator("pre.call .t-ph").all_inner_texts()
    assert any(re.search(r"_(API_)?KEY\}$|_TOKEN\}$", t) for t in phs), phs  # the key, only named
    for t in phs:
        assert re.fullmatch(r"\$?\{[A-Z0-9_]+\}", t), t
    # no header value carries anything that looks like a real secret
    for line in curl.split("\n"):
        if line.strip().startswith("-H"):
            assert not SECRETISH.search(re.sub(r"\$?\{[A-Z0-9_]+\}", "", line)), line
    assert pane.get_by_role("link", name="Get a key").count() == 1
    assert "needs your key" in pane.inner_text()
    assert not [u for u in sent if host in u], sent


def test_each_copy_copies_its_own_step(browser, base):
    p, _ = finder(browser, base, STORE)
    pane = p.locator("#bcall [data-ircp]")
    ask = pane.locator(".ircp-cp1")
    ask.click()
    p.wait_for_function("document.querySelector('#bcall .ircp-cp1').classList.contains('done')", timeout=2000)
    got = p.evaluate("navigator.clipboard.readText()")
    assert got.startswith('irlpls_request(\n  need="photo of the storefront at 50 Elm St, Springfield, IL"') and got.endswith("\n)"), got
    assert 'place="50 Elm St, Springfield, IL"' in got and "lat=" not in got, got
    pane.locator(".ircp-cp").click()
    p.wait_for_function("document.querySelector('#bcall .ircp-cp').getAttribute('aria-label') === 'Copied'", timeout=2000)
    t = pane.locator("pre.call").inner_text()
    assert t.startswith("curl -X POST 'https://api.proxypics.com/") and '"x-api-key: ${PROXYPICS_API_KEY}"' in t, t
    assert '"address": "50 Elm St, Springfield, IL"' in t
    assert p.evaluate("navigator.clipboard.readText()") == t


def test_step_one_shows_what_irlpls_understood(browser, base):
    """The owner (2026-10-30): step 1 is the structured request the parser derives, only the fields present, and step 2
    checks the policy against the matched card."""
    p, _ = finder(browser, base, "Drone photos of my roof at 45 Oak Ave, Austin, TX within 3 days, under $600")
    ask = p.inner_text("#bcall .ircp-ask")
    assert 'place="45 Oak Ave, Austin, TX"' in ask and "within_minutes=4320" in ask and '"max_order_usd": 600' in ask, ask
    assert "quantity" not in ask and "need_ai_training" not in ask, ask
    assert re.search(r"(Within|Over) your \$600 budget|No published price", p.inner_text("#bcall .ircp-cks"))
    v = p.evaluate("""IRLCall.html({text: '500 first-person clips of a hand opening a door, with AI-training rights, under $2,000, within 7 days',
        req: IRLMatch.parse('500 first-person clips of a hand opening a door, with AI-training rights, under $2,000, within 7 days', []), provider: 'X',
        card: {pricing: {from_usd: 0}, access: {cost: 'paid'}, licensing: {}}})""")
    assert "quantity</span>" in v and ">500<" in v and ">10080<" in v and "need_ai_training" in v and ">2000<" in v
    assert "No published price: irlpls asks before ordering" in v and "AI-training rights not stated" in v
    # a plain ask with nothing parsed stays one line
    one = p.evaluate("IRLCall.html({text: 'hello', provider: 'X'})")
    assert 'irlpls_request</span><span class="t-p">(</span><span class="t-key">need' in one and "policy" not in one


@pytest.mark.parametrize("w", [375, 390])
def test_code_scrolls_inside_never_the_page(browser, base, w):
    for q in (STORE, SAT):
        p, _ = finder(browser, base, q, w=w)
        assert p.evaluate("document.documentElement.scrollWidth") <= w, q
        r = p.evaluate("""(() => { const e = document.querySelector('#bcall pre.ircp-code'), cs = getComputedStyle(e), b = e.getBoundingClientRect();
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
