"""Find data's search and filters behave as everyone expects (the owner, 2026-11-02): filters intersect with the typed
query; the box's × clears only the text; the reset reads 'Clear filters' (filters only) or 'Clear all' (text too) and is
hidden otherwise; an empty query+filter result says so with two one-tap ways out; the state lives in the URL (back,
forward, sharing); Esc in the box clears the text.

    uv run --with pytest --with playwright python -m pytest -q tests/test_finder_search.py
"""
import functools
import http.server
import pathlib
import socketserver
import threading
import urllib.parse

import pytest

pw = pytest.importorskip("playwright.sync_api")
SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
NOFREE = "500 egocentric clips"  # 23 matches, none free


@pytest.fixture(scope="module")
def base():
    if not (SITE / "find.html").exists():
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


def finder(browser, base, qs="", rows=True):
    p = browser.new_page(viewport={"width": 390, "height": 844})
    p.route("https://**/*", lambda r: r.abort())
    p.goto(base + "/find.html" + qs)
    p.wait_for_selector("#grid .row" if rows else "#meta", state="attached")
    return p


def typed(p, text):
    p.fill("#q", text)
    p.wait_for_function("t => new URLSearchParams(location.search).get('q') === t", arg=text, timeout=3000)


def free_on(p):
    return p.get_attribute('#filters [data-f="free"]', "aria-pressed") == "true"


def ids(p):
    return p.evaluate("[...document.querySelectorAll('#grid .row')].map(a => a.dataset.id)")


def test_filters_intersect_with_the_query(browser, base):
    p = finder(browser, base)
    typed(p, "river level in Texas right now")
    all_ = ids(p)
    p.click('#filters [data-f="free"]')
    got = ids(p)
    assert got and set(got) <= set(all_) and len(got) <= len(all_)
    free = p.evaluate("""async ids => { const d = await (await fetch('cards.json')).json(), cs = d.cards || d;
      return ids.every(id => IRLMatch.isFree(cs.find(c => c.id === id))) }""", got)
    assert free, got


def test_the_x_clears_only_the_text_and_keeps_focus(browser, base):
    p = finder(browser, base, "?q=river&free=1")
    assert p.input_value("#q") == "river" and free_on(p)
    p.click("#clr")
    assert p.input_value("#q") == "" and free_on(p) and p.evaluate("document.activeElement.id") == "q"
    assert "free=1" in p.url and "q=" not in p.url


def test_reset_reads_clear_filters_or_clear_all(browser, base):
    p = finder(browser, base)
    assert p.is_hidden("#rst")
    typed(p, "river")
    assert p.is_hidden("#rst")  # text alone: the × clears it
    p.fill("#q", "")
    p.click('#filters [data-f="free"]')
    assert p.inner_text("#rst") == "Clear filters" and p.is_visible("#rst")
    typed(p, "river")
    assert p.inner_text("#rst") == "Clear all"
    p.click("#rst")
    assert p.input_value("#q") == "" and not free_on(p) and p.is_hidden("#rst")
    assert "q=" not in p.url and "free=" not in p.url


def test_empty_query_and_filter_offers_two_ways_out(browser, base):
    p = finder(browser, base, "?q=" + urllib.parse.quote(NOFREE) + "&free=1", rows=False)
    p.wait_for_selector("#allf")
    t = p.inner_text("#meta")
    assert t.startswith(f"No free providers match “{NOFREE}”."), t
    assert p.inner_text("#allf") == "Show all free providers" and p.inner_text("#allq") == f"Show all matches for “{NOFREE}”"
    p.click("#allq")
    assert p.input_value("#q") == NOFREE and not free_on(p) and ids(p)
    p.go_back()
    p.wait_for_selector("#allf")
    p.click("#allf")
    assert p.input_value("#q") == "" and free_on(p) and ids(p)


def test_state_is_in_the_url_and_back_forward_work(browser, base):
    p = finder(browser, base)
    typed(p, "river")
    p.click('#filters [data-f="free"]')
    assert "q=river" in p.url and "free=1" in p.url
    p.go_back()
    p.wait_for_function("document.querySelector('#filters [data-f=\"free\"]').getAttribute('aria-pressed') === 'false'")
    assert p.input_value("#q") == "river"
    p.go_forward()
    p.wait_for_function("document.querySelector('#filters [data-f=\"free\"]').getAttribute('aria-pressed') === 'true'")
    # a shared link opens the same view
    s = finder(browser, base, "?q=river&free=1")
    assert s.input_value("#q") == "river" and free_on(s) and s.inner_text("#rst") == "Clear all"


def test_esc_in_the_box_clears_the_text(browser, base):
    p = finder(browser, base, "?q=river&free=1")
    p.focus("#q")
    p.keyboard.press("Escape")
    assert p.input_value("#q") == "" and free_on(p) and "q=" not in p.url
