"""The provider experience on a fixture site: build.py run against a fake day of reads (irlpls/published.py with a fake
fetch, no network) into a temporary site, then the pages checked as HTML and in WebKit at 390x709.

Pins: the badge and sections on a live provider page, real ranks, the badge SVG, the Find data strip order
(evidenced first, then newest), the '✓ Published' mark, the stale fallback (grey badge, compiled card), the claim line
on compiled pages, check.html's going-live flow (Fetch, ?id=, ?d=), and the hostile cases in the browser.
"""
import datetime
import functools
import http.server
import json
import os
import pathlib
import re
import socketserver
import subprocess
import sys
import threading

import pytest

from irlpls import published, standing

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPILED = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
BY_ID = {c["id"]: c for c in COMPILED}
NOW = published.utcnow()
TODAY = NOW.date()
H = {"Content-Type": "application/json", "Cache-Control": "public, max-age=3600", "ETag": '"v1"'}


def pub_card(cid, host=None, **over):
    c = json.loads(json.dumps(BY_ID[cid]))
    host = host or published.host_of(c["provider_url"])
    c.update(status="provider-maintained", provider_url=f"https://{host}", updated=TODAY.isoformat(),
             expires=(TODAY + datetime.timedelta(days=300)).isoformat())
    c.update(over)
    return c


def improved_proxypics(host=None):
    c = pub_card("proxypics", host)
    c["pricing"].update(from_usd=12, unit="photo request")
    c["licensing"].update(ai_training="forbidden", resale="forbidden")
    return c


def url(cid):
    return f"https://{published.host_of(BY_ID[cid]['provider_url'])}/.well-known/irlpls"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("provider-site")
    d, out = tmp / "published", tmp / "site"

    def fake(table):
        return lambda u, h: table.get(u, (404, {}, b""))
    earlier = NOW - datetime.timedelta(days=3)
    # day 1: GetterDone and SkyFi publish; day 3 (today): ProxyPics publishes an improved card and SkyFi breaks its file
    state, *_ = published.run(COMPILED, None, fetch=fake({url("getterdone"): (200, H, json.dumps(pub_card("getterdone")).encode()),
                                                          url("skyfi"): (200, H, json.dumps(pub_card("skyfi")).encode())}), now=earlier, d=d)
    state, *_ = published.run(COMPILED, state, fetch=fake({url("proxypics"): (200, H, json.dumps(improved_proxypics()).encode()),
                                                           url("getterdone"): (304, {}, b""),
                                                           url("skyfi"): (200, H, b"{broken")}), now=NOW, d=d)
    published.save(state, d=d)
    env = dict(os.environ, IRLPLS_SITE=str(out), IRLPLS_PUBLISHED=str(d))
    r = subprocess.run([sys.executable, str(ROOT / "build.py")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return out


def page(site, rel):
    import html
    return html.unescape((site / rel).read_text()) if rel.endswith(".html") else (site / rel).read_text()


def test_live_provider_page_has_the_badge_and_the_computed_sections(site):
    h = page(site, "c/proxypics.html")
    day = datetime.datetime.now(datetime.timezone.utc).date().strftime("%-d %b")  # the page shows the UTC check date
    assert re.search(rf'class="pubb"><svg.*?<b>Published by ProxyPics</b> from www\.proxypics\.com · checked {day}', h)
    for sec in ("What an agent says", "Where it ranks", "Changed from the compiled card", "Badge for your site"):
        assert f'<h2 class="sec">{sec}</h2>' in h
    cards = json.loads(page(site, "cards.json"))["cards"]
    pp = next(c for c in cards if c["id"] == "proxypics")
    assert pp["status"] == "provider-maintained" and pp["pricing"]["from_usd"] == 12
    for r in standing.ranks("proxypics", cards):   # the numbers on the page are the matcher's
        want = f'#{r["pos"]} <small>of {r["of"]}</small>' if r["pos"] else ("Not covered" if not r["covered"] else "Not a match")
        assert f'<span>{r["label"]}</span><b>{want}</b>' in h
    assert "<b>Price</b> · not stated → $12 / photo request" in h
    assert "Read from" in h and "sha256" in h and "Is this your company?" not in h
    assert 'https://irlpls.com/badge/proxypics.svg' in h and "github" not in h.lower()


def test_badges(site):
    live = page(site, "badge/proxypics.svg")
    assert "Capture Card · checked" in live and 'fill="#fff"' in live
    assert not re.search(r"score|rank|verified|utm_|\?", live, re.I)
    assert "Card not valid" in page(site, "badge/skyfi.svg")


def test_a_broken_publish_shows_the_compiled_card_with_a_grey_badge(site):
    h = page(site, "c/skyfi.html")
    assert 'class="pubb old"' in h and "didn't validate" in h and "showing the compiled card" in h
    assert "What an agent says" not in h
    assert json.loads(page(site, "cards/skyfi.json"))["status"] == BY_ID["skyfi"]["status"]


def test_compiled_pages_invite_the_provider(site):
    h = page(site, "c/nws.html")
    assert 'href="../check.html?id=nws">Publish your card' in h and 'class="pubb' not in h


def test_find_strip_order_and_mark(site):
    h = page(site, "find.html")
    strip = h[h.index('<section class="pubs"'):h.index("</section>", h.index('<section class="pubs"'))]
    names = re.findall(r'<span class="nm">([^<]+)</span>', strip)
    assert names == ["ProxyPics", "GetterDone"]          # newest material change first; SkyFi is not live
    assert 'href="providers.html">Publish yours' in strip
    # never above a user's task: the strip comes after the search, the best match and the whole list
    at = h.index('<section class="pubs"')
    assert all(h.index(m) < at for m in ('id="q"', 'id="filters"', 'id="best"', 'id="grid"', 'id="more"'))
    land = page(site, "index.html")   # the landing's line is the last thing in its footer
    if 'class="publ"' in land:
        f = land[land.index('<footer class="site-foot">'):]
        assert f.index('class="publ"') > f.index('class="fm"') and land.index('class="publ"') > land.index('id="try"')
    assert re.search(r'<span class="nm">ProxyPics<span class="pmk">', h)
    pub = json.loads(page(site, "published.json"))
    assert set(pub["cards"]) == {"proxypics", "getterdone", "skyfi"} and "domains" not in pub


def test_evidenced_goes_first_in_the_strip(tmp_path):
    import build
    old = dict(build.PUB)
    try:
        build.PUB.update(state={"cards": {
            "proxypics": {"state": "live", "listed_at": "2026-10-29T06:10:00Z", "evidence": []},
            "getterdone": {"state": "live", "listed_at": "2026-10-01T06:10:00Z",
                           "evidence": [{"where": "provenance.receipt", "verdict": "verified", "checked_at": "2026-10-20T06:10:00Z"}]}}},
            eff={"proxypics": "live", "getterdone": "live"})
        h = build.pub_strip([BY_ID["proxypics"], BY_ID["getterdone"]])
    finally:
        build.PUB.clear()
        build.PUB.update(old)
    assert re.findall(r'<span class="nm">([^<]+)</span>', h) == ["GetterDone", "ProxyPics"] and "<em>Evidenced</em>" in h


def test_no_strip_when_nobody_has_published():
    import build
    assert build.pub_strip(COMPILED) == "" or build.PUB["state"].get("cards")


# ---------------------------------------------------------------- in the browser
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser_page(site):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a: None
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    with pw.sync_playwright() as p:
        try:
            b = p.webkit.launch()
        except Exception as e:
            srv.shutdown()
            pytest.skip(f"no WebKit for Playwright: {e}")
        ctx = b.new_context(viewport={"width": 390, "height": 709})
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg, base, errors
        b.close()
    srv.shutdown()


def no_sideways(pg):
    return pg.evaluate("document.documentElement.scrollWidth") <= 390


def test_provider_page_fits_a_phone(browser_page):
    pg, base, errors = browser_page
    for rel in ("c/proxypics.html", "c/skyfi.html", "find.html"):
        pg.goto(base + rel)
        assert no_sideways(pg), rel
    pg.goto(base + "find.html")
    assert pg.is_visible("section.pubs") and pg.inner_text("section.pubs h2").lower() == "recently published by providers"
    # never above a user's task: after the whole list while browsing, gone once there is a query or a filter
    assert pg.evaluate("""() => { const g = document.getElementById('grid'), p = document.querySelector('section.pubs');
        return !!(g.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING)
            && p.getBoundingClientRect().top >= g.getBoundingClientRect().bottom }""")
    pg.fill("#q", "storefront photos in Atlanta")
    pg.wait_for_function("document.querySelector('section.pubs').hidden")
    assert not pg.is_visible("section.pubs")
    pg.fill("#q", "")
    pg.wait_for_function("!document.querySelector('section.pubs').hidden")
    pg.click("#filters button[data-f=free]")
    assert not pg.is_visible("section.pubs")
    pg.click("#filters button[data-f=free]")
    assert pg.is_visible("section.pubs")
    pg.goto(base + "find.html?q=drone+survey")
    pg.wait_for_selector("#grid .fit", state="attached")
    assert not pg.is_visible("section.pubs")
    assert not errors


def serve_card(pg, host, card, status=200, headers=None):
    def handle(route):
        route.fulfill(status=status, body=card if isinstance(card, str) else json.dumps(card),
                      headers={"Content-Type": "application/json", "Cache-Control": "public, max-age=300",
                               "Access-Control-Allow-Origin": "*", **(headers or {})})
    pg.route(f"https://{host}/.well-known/irlpls", handle)


def fetch(pg, base, host):
    pg.goto(base + "check.html")
    pg.fill("#dom", host)
    pg.click("#domf button")
    pg.wait_for_selector("#live .golive", timeout=15000)
    return pg.inner_text("#live")


def test_check_fetch_goes_live_with_ranks_and_a_preview(browser_page, site):
    pg, base, errors = browser_page
    serve_card(pg, "www.proxypics.com", improved_proxypics())
    t = fetch(pg, base, "www.proxypics.com")
    assert "Valid · www.proxypics.com" in t and "Live on irlpls" in t and "Nothing else to do." in t   # live on this site already
    assert "Found at /.well-known/irlpls" in t and "Next read by irlpls" in t
    assert "We log nothing per request" in t
    pg.wait_for_selector("#live .ranks")
    cards = json.loads((site / "cards.json").read_text())["cards"]
    rows = standing.ranks("proxypics", cards)
    got = pg.eval_on_selector_all("#live .ranks li", "ls=>ls.map(l=>l.innerText.replace(/\\s+/g,' ').trim())")
    want = [f'{r["label"]} #{r["pos"]} of {r["of"]}' if r["pos"] else f'{r["label"]} {"Not covered" if not r["covered"] else "Not a match"}' for r in rows]
    assert got == want
    pg.goto(base + "check.html")   # the preview, from a pasted copy (the live page links to itself instead)
    pg.fill("#json", json.dumps(improved_proxypics()))
    pg.click("#go")
    pg.wait_for_selector("#pvb")
    pg.click("#pvb")
    assert pg.is_visible("#pvw .pubb") and "Published by ProxyPics" in pg.inner_text("#pvw")
    nws = pub_card("nws")
    nws["licensing"]["resale"] = "forbidden"
    pg.fill("#json", json.dumps(nws))
    pg.click("#go")
    pg.wait_for_selector("#pvb")
    pg.click("#pvb")
    assert "changed from the compiled card" in pg.inner_text("#pvw").lower() and "Resale · not stated → not allowed" in pg.inner_text("#pvw")
    assert no_sideways(pg) and not errors
    serve_card(pg, "www.weather.gov", pub_card("nws"))   # not published yet: it will be within a day
    t = fetch(pg, base, "www.weather.gov")
    assert "Live on irlpls within a day" in t and "Preview your page" in t


def test_check_hostile_and_new_and_invalid(browser_page):
    pg, base, errors = browser_page
    serve_card(pg, "proxypics-photos.com", improved_proxypics("proxypics-photos.com"))
    t = fetch(pg, base, "proxypics-photos.com")
    assert "Not yours to publish" in t and "belongs to www.proxypics.com" in t
    serve_card(pg, "cards.proxypics.com", improved_proxypics("cards.proxypics.com"))
    assert "Not yours to publish" in fetch(pg, base, "cards.proxypics.com")
    new = improved_proxypics("newco.example")
    new.update(id="newco", name="NewCo")
    serve_card(pg, "newco.example", new)
    t = fetch(pg, base, "newco.example")
    assert "New to irlpls" in t and pg.get_attribute("#live a.btn", "href").startswith("mailto:hello@irlpls.com")
    serve_card(pg, "www.proxypics.com", "{nope")
    pg.goto(base + "check.html")
    pg.fill("#dom", "www.proxypics.com")
    pg.click("#domf button")
    pg.wait_for_selector("#out section")
    assert "not valid JSON" in pg.inner_text("#out")
    bad = improved_proxypics()
    bad["verification"]["time"] = {"level": "evidenced", "unknown": "x"}
    serve_card(pg, "www.proxypics.com", bad)
    assert re.search(r"Fix \d+ things? first", fetch(pg, base, "www.proxypics.com"))
    assert not errors


def test_check_id_and_d_links(browser_page):
    pg, base, errors = browser_page
    pg.goto(base + "check.html?id=nws")
    pg.wait_for_selector("#live .golive", timeout=15000)
    assert "Ready to publish" in pg.inner_text("#live") and pg.input_value("#dom") == "www.weather.gov"
    pg.goto(base + "check.html?id=proxypics")   # already live on this fixture site
    pg.wait_for_selector("#live .golive", timeout=15000)
    assert "Live on irlpls" in pg.inner_text("#live") and pg.get_attribute("#live a.btn", "href") == "c/proxypics.html"
    pg.goto(base + "check.html?d=www.proxypics.com")
    pg.wait_for_selector("#live .golive")
    t = pg.inner_text("#live")
    assert "Live since" in t and pg.get_attribute("#live a.btn", "href") == "c/proxypics.html"
    pg.goto(base + "check.html?d=nobody.example")
    pg.wait_for_selector("#live .golive")
    assert "Not read yet" in pg.inner_text("#live")
    assert not errors
