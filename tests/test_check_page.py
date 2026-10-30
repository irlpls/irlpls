"""Browser test for the 'Check your card' page. Loads the built site/check.html; skipped if absent.

    uv run --with pytest --with playwright python -m pytest tests/test_check_page.py
"""
import functools
import http.server
import json
import pathlib
import socketserver
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CARD = json.loads((ROOT / "cards" / "airnow.json").read_text())


@pytest.fixture(scope="module")
def page():
    if not (SITE / "check.html").exists():
        pytest.skip("site/check.html not built")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pw.sync_playwright() as p:
        try:
            b = p.webkit.launch()
        except Exception as e:
            srv.shutdown()
            pytest.skip(f"no WebKit for Playwright: {e}")
        pg = b.new_page(viewport={"width": 390, "height": 709})
        pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/check.html")
        yield pg
        b.close()
    srv.shutdown()


def check(pg, card):
    pg.fill("#json", json.dumps(card) if not isinstance(card, str) else card)
    pg.click("#go")
    pg.wait_for_selector("#out section")
    return pg.inner_text("#out")


def test_bad_card_is_flagged_without_a_score(page):
    card = json.loads(json.dumps(CARD))
    card["verification"]["time"] = {"level": "evidenced", "unknown": "x"}
    card["verification"]["camera"]["level"] = "evidenced"
    card["verification"]["camera"]["method"] = "m"
    card["verification"]["camera"]["evidence_url"] = "https://localhost/p"
    text = check(page, card)
    assert "verification.time" in text and "method" in text
    assert "private, loopback" in text
    assert "no overall score" in text.lower()


def test_expiry_over_a_year(page):
    card = json.loads(json.dumps(CARD))
    card["updated"], card["expires"] = "2026-01-01", "2028-01-01"
    assert "at most a year" in check(page, card)


def test_not_json(page):
    assert "not valid JSON" in check(page, "{nope")


def test_no_horizontal_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_buttons_are_centred(page):
    """'Check card' had its label 9px high: the finder row's global .main rule (flex column) hit .btn.main (2026-10-29)."""
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    from centre_probe import CENTRE
    page.goto(page.url.split("?")[0])
    assert page.evaluate(CENTRE, {"tol": 1.5}) == []
