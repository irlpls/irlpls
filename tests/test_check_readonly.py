"""check.html (the owner, 2026-11-02: 'weird that I was able to edit the text there directly'): ?id=<card> shows the listed
card read-only with its result; 'Try changes' makes an editable test copy with a persistent note and a Reset. Paste mode
(no ?id) stays editable and carries the same note.

    uv run --with pytest --with playwright python -m pytest -q tests/test_check_readonly.py
"""
import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")
SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
NOTE = "A test copy — nothing here is saved or published. To publish, put the file at"


@pytest.fixture(scope="module")
def base():
    if not (SITE / "check.html").exists():
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


def page(browser, base, qs):
    p = browser.new_page(viewport={"width": 390, "height": 844})
    p.route("https://**/*", lambda r: r.abort())
    p.goto(base + "/check.html" + qs)
    return p


def test_a_listed_card_is_read_only_until_try_changes(browser, base):
    p = page(browser, base, "?id=usgs-water")
    p.wait_for_selector("#ro:not([hidden])")
    p.wait_for_function("document.getElementById('out').innerText.trim().length > 0", timeout=8000)  # its result shows
    assert p.is_hidden("#json") and p.is_hidden("#tnote") and p.is_hidden("#go")
    assert p.evaluate("document.getElementById('rojson').isContentEditable") is False
    assert p.locator("#rojson .k").count() > 5 and '"id": "usgs-water"' in p.text_content("#rojson")
    p.click(".rod summary")  # the JSON opens, still read-only
    assert p.is_visible("#rojson") and '"id": "usgs-water"' in p.inner_text("#rojson")
    assert p.inner_text("#roid") == "usgs-water"
    p.click("#try")
    assert p.is_visible("#json") and p.is_hidden("#ro") and p.is_visible("#reset")
    note = p.inner_text("#tnote")
    assert note.startswith(NOTE) and "/.well-known/irlpls" in note and "<your domain>" not in note, note  # the card's own host
    p.fill("#json", '{"id": "changed"}')
    p.click("#reset")
    assert '"id": "usgs-water"' in p.input_value("#json")


def test_paste_mode_is_editable_and_says_it_is_a_test_copy(browser, base):
    p = page(browser, base, "")
    assert p.is_visible("#json") and p.is_hidden("#ro") and p.is_hidden("#reset")
    assert p.inner_text("#tnote").startswith(NOTE) and "https://<your domain>/.well-known/irlpls" in p.inner_text("#tnote")
    p.fill("#dom", "provider.example")
    assert "https://provider.example/.well-known/irlpls" in p.inner_text("#tnote")


def test_a_valid_unlisted_card_offers_ask_to_be_listed(browser, base):
    import json
    import re
    import urllib.parse
    card = json.loads((SITE / "cards" / "usgs-water.json").read_text())
    card.update(id="newco-test", name="NewCo Test", provider_url="https://newco.example")
    p = page(browser, base, "")
    p.fill("#json", json.dumps(card))
    p.click("#go")
    p.wait_for_selector("#askl", timeout=8000)
    assert p.inner_text("#askl") == "Ask to be listed" and "We review new providers before they appear." in p.inner_text("#live")
    href = p.get_attribute("#askl", "href")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    assert href.startswith("mailto:hello@irlpls.com?") and q["subject"] == ["Listing request: newco.example"]
    body = q["body"][0]
    for want in ("Domain: newco.example", "Card: NewCo Test (newco-test)", "https://newco.example/.well-known/irlpls", "Check result: valid"):
        assert want in body, body
    # a listed card's domain gets no listing request
    p2 = page(browser, base, "?id=usgs-water")
    p2.wait_for_function("document.getElementById('live').innerText.trim().length > 0", timeout=8000)
    assert p2.locator("#askl").count() == 0


def test_card_pages_offer_a_quiet_correction_mail():
    import urllib.parse
    h = (SITE / "c" / "usgs-water.html").read_text()
    import re
    m = re.search(r'<a class="fix" href="([^"]+)">Report a correction</a>', h)
    assert m, "no correction link"
    href = m.group(1).replace("&amp;", "&")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    assert href.startswith("mailto:hello@irlpls.com?") and q["subject"] == ["Correction: usgs-water"]
    assert "https://irlpls.com/c/usgs-water.html" in q["body"][0]
