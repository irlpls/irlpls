"""Browser tests for the site: the bugs design reviews found, pinned so they stay fixed.

    uv run --with pytest --with playwright python -m pytest tests/test_ui.py
Skipped automatically if Playwright or its Chromium isn't installed. Builds nothing: run `python3 build.py` first.
"""
import functools
import json
import http.server
import pathlib
import re
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


def page(browser, w=390, h=844, **kw):
    ctx = browser.new_context(viewport={"width": w, "height": h}, **kw)
    return ctx.new_page()


# ---------- finder ----------

def test_finder_dialog_opens_and_closes(browser, base):
    p = page(browser)
    p.goto(base + "/find.html?q=river%20level%20in%20Texas%20right%20now")
    row = p.locator("a.row").first
    row.click()
    assert p.locator("#sheet").is_visible()
    assert p.evaluate("document.activeElement.classList.contains('sheet-x')")
    # opened by a tap or click: focused, but no ring (sweep nit 12); Tab brings the ring back
    assert p.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "none"
    p.keyboard.press("Escape")
    assert not p.locator("#sheet").is_visible()
    assert p.evaluate("document.activeElement.classList.contains('row')")
    row.click()
    p.locator(".sheet-x").click()
    assert not p.locator("#sheet").is_visible()
    assert not p.evaluate("document.body.hasAttribute('inert')")


def test_finder_hash_deep_link_opens_card(browser, base):
    p = page(browser)
    p.goto(base + "/find.html#nws")
    assert p.locator("#sheet").is_visible()
    assert "NWS" in p.locator("#card-h").inner_text()


def test_free_filter_means_free(browser, base):
    p = page(browser, 1280, 900)
    p.goto(base + "/find.html")
    p.locator("#filters button[data-f=free]").click()
    names = p.locator(".row .nm").all_inner_texts()
    assert names and not any("Windy" in n for n in names)
    assert "free=1" in p.url


def test_texas_river_ranks_usgs_first_and_no_california_cameras(browser, base):
    p = page(browser, 1280, 900)
    p.goto(base + "/find.html?q=is%20the%20Guadalupe%20at%20Comfort%2C%20TX%20good%20for%20tubing%20right%20now")
    names = p.locator(".row .nm").all_inner_texts()
    assert names[0].startswith("USGS")
    assert not any("Caltrans" in n for n in names)


def test_live_and_commission_filters_are_exclusive(browser, base):
    p = page(browser)
    p.goto(base + "/find.html")
    p.locator("#filters button[data-f=live]").click()
    p.locator("#filters button[data-f=commission]").click()
    assert p.locator("#filters button[data-f=live]").get_attribute("aria-pressed") == "false"
    assert "made=1" in p.url and "live=1" not in p.url
    assert p.locator(".row").count() > 0


def test_rows_use_the_landings_eight_stages(browser, base):
    """Round 5: one proof vocabulary. Each Sources row shows the landing's eight stage dots and "n claimed · n evidenced",
    with and without a request; the fit is a word (Good fit / Partial fit) with the number in its title."""
    p = page(browser)
    p.goto(base + "/find.html")
    row = p.locator(".row").first
    assert row.locator(".dots i").count() == 8 and row.locator(".pf").count() == 0
    assert __import__("re").fullmatch(r"\d claimed · \d evidenced", row.locator(".tl").inner_text())
    p.goto(base + "/find.html?q=" + "photos of a roof taken by a verified human")
    row = p.locator(".row").first
    assert row.locator(".dots i").count() == 8
    fit = row.locator(".fit")
    assert fit.inner_text() in ("Good fit", "Partial fit")
    assert __import__("re").fullmatch(r"match \d+/100 for this request", fit.get_attribute("title"))


def test_sources_row_tally_equals_the_card_page_for_every_card(browser, base):
    """The Sources row (app.js, IRLMatch.stageLevels) and each card page (build.py stage_levels) count the same stages."""
    import json
    import re
    p = page(browser)
    p.goto(base + "/find.html")
    p.wait_for_load_state("networkidle")
    p.fill("#q", "river")
    p.fill("#q", "")  # rows re-rendered by app.js, not the built list
    p.wait_for_timeout(300)
    rows = p.evaluate("Object.fromEntries([...document.querySelectorAll('#grid a.row')].map(a=>[a.dataset.id,[a.querySelector('.tl').textContent,[...a.querySelectorAll('.dots i')].map(i=>i.className).join(',')]]))")
    assert len(rows) > 50
    for cid, (tl, dots) in rows.items():
        html = (SITE / "c" / f"{cid}.html").read_text()
        m = re.search(r'<p class="stg-t">(<span class="dots"[^>]*>.*?</span>)<span>(.*?)</span></p>', html)
        assert m, cid
        m = type("M", (), {"group": lambda self, i, m=m: m.group(1) if i == 1 else re.sub(r"<[^>]+>", "", m.group(2))})()
        # the row's short tally ("1 claimed · 0 evidenced") counts what the page's long one does ("1 of 8 links claimed · 0 evidenced")
        assert m.group(2) == re.sub(r"^(\d+) claimed", r"\1 of 8 links claimed", tl), (cid, tl, m.group(2))
        assert ",".join(re.findall(r'<i class="([a-z]*)"></i>', m.group(1))) == dots, cid


def test_captured_locally_is_the_weakest_of_camera_and_place():
    """Round 5: DroneDeploy says Camera: none, so Captured locally is not stated (it was shown as claimed)."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("build", SITE.parent / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    for cid in ("dronedeploy-flight-services", "usgs-water"):
        c = json.loads((SITE.parent / "cards" / f"{cid}.json").read_text())
        assert c["verification"]["camera"]["level"] == "none"
        assert b.stage_levels(c)[1] == "none", cid


def test_finder_rows_stay_short(browser, base):
    p = page(browser)
    p.goto(base + "/find.html?q=river%20level%20in%20Texas%20right%20now")
    counts = p.evaluate("""[...document.querySelectorAll('.row')].map(r=>{const c=r.cloneNode(true);c.querySelectorAll('.sr').forEach(x=>x.remove());
        return c.textContent.replace(/([a-z])([A-Z0-9])/g,'$1 $2').split(/\\s+/).filter(w=>/\\w/.test(w)).length})""")
    assert counts and max(counts) <= 16, counts  # round 5: the stage tally replaced three proof chips


def test_no_match_and_filtered_empty_states_say_why(browser, base):
    p = page(browser)
    p.goto(base + "/find.html?q=xyzzy%20plugh")
    assert p.locator(".row").count() == 0 and "No provider covers this" in p.inner_text("#meta")
    p.goto(base + "/find.html?q=river%20level%20in%20Texas%20right%20now&made=1")
    p.locator("#clrf").click()
    assert p.locator(".row").count() > 0 and "made=1" not in p.url


def test_match_count_is_a_left_sentence_not_a_column_header(browser, base):
    p = page(browser)
    p.goto(base + "/find.html?q=river%20level%20in%20Texas%20right%20now")
    p.wait_for_selector(".row .fit")
    assert p.get_attribute("#meta", "aria-live") == "polite"
    assert p.inner_text("#meta").endswith(("providers match", "provider matches"))  # "sources" means citations only
    m, fit = p.evaluate("[document.getElementById('meta').getBoundingClientRect(), document.querySelector('.row .fit').getBoundingClientRect()].map(r=>({l:r.left,r:r.right,b:r.bottom,t:r.top}))")
    lst = p.evaluate("document.getElementById('grid').getBoundingClientRect().left")
    assert m["l"] - lst <= 4  # left-aligned with the list
    key = p.evaluate("document.querySelector('.lhead .key').getBoundingClientRect().top")
    assert key >= m["b"] - 1  # the key is below the sentence, not beside it


def test_sheet_is_opaque_and_full_screen(browser, base):
    p = page(browser)
    p.goto(base + "/find.html#usgs-water")
    p.wait_for_selector("#sheet")
    bg = p.evaluate("getComputedStyle(document.getElementById('sheet')).backgroundColor")
    assert bg.startswith("rgb(") , bg  # no alpha: nothing shows through
    box = p.locator("#sheet").bounding_box()
    assert box["x"] == 0 and box["y"] == 0 and box["width"] == 390


# ---------- capture card ----------

def test_card_is_a_spec_sheet(browser, base):
    p = page(browser)
    p.goto(base + "/c/proxypics.html")
    assert p.locator(".spec > div").count() == 6
    assert p.locator(".gua").count() == 0 and p.locator(".stg-o > li").count() == 8  # one proof vocabulary: the eight stages only
    assert p.locator(".api .eps li").count() >= 1
    assert p.locator("details.dt").count() >= 2  # the stages' card fields and the sources stay folded
    assert all(not d for d in p.evaluate("[...document.querySelectorAll('details.dt')].map(d=>d.open)"))


def test_card_without_api_says_so(browser, base):
    p = page(browser)
    p.goto(base + "/c/kled.html")  # zeitview turned out to have a partners API (card audit 2026-10-26)
    assert "No public API" in p.inner_text(".cardv")


@pytest.mark.parametrize("path", ["/find.html", "/c/usgs-water.html", "/compare.html?ids=nws,usgs-water,proxypics"])
def test_no_sideways_scroll_on_a_phone(browser, base, path):
    p = page(browser)
    p.goto(base + path)
    p.wait_for_timeout(200)
    assert p.evaluate("document.documentElement.scrollWidth") <= 390


# ---------- compare ----------

def test_compare_fits_a_phone_without_scrolling(browser, base):
    p = page(browser)
    p.goto(base + "/compare.html?ids=nws,usgs-water,proxypics,skyfi")
    assert p.locator("#cmp thead th").count() == 4  # the fourth is dropped on a phone
    assert p.evaluate("(()=>{const s=document.getElementById('cmp');return s.scrollWidth<=s.clientWidth+1})()")


def test_compare_add_remove_and_empty(browser, base):
    p = page(browser)
    p.goto(base + "/compare.html")
    assert p.locator("#cmp a[data-ids]").count() >= 1  # one-tap starting points, no filler copy
    p.select_option("#addp", "nws")
    p.select_option("#addp", "usgs-water")
    assert p.locator("#cmp thead th").count() == 3 and "ids=nws,usgs-water" in p.url
    p.locator("#cmp .rm").first.click()
    assert p.locator("#cmp thead th").count() == 2 and "ids=usgs-water" in p.url


# ---------- landing: three screens (#s1 chat, #how the chain, #try type anything) ----------

LINE = "Real-world data on demand, with provenance. An open protocol and router for agents."
HEAD = "Real-world data on demand, with provenance."


def landing(browser, base, w=390, h=709, q="", usgs=None, cards=None, **kw):
    """The landing with USGS answered by `usgs` (None: never answers) and cards.json by `cards` (None: the real file)."""
    p = page(browser, w, h, **kw)
    if usgs is None:
        p.route("**/*usgs.gov/**", lambda r: None)
    else:
        p.route("**/*usgs.gov/**", lambda r: r.fulfill(status=200, body=usgs, headers={"content-type": "application/json", "access-control-allow-origin": "*"}))
    if cards is not None:
        p.route("**/cards.json", cards)
    p.goto(base + "/" + q)
    p.wait_for_timeout(400)
    return p


def test_landing_is_three_screens_with_mos_line(browser, base):
    p = landing(browser, base, q="?still=1")
    assert p.evaluate("[...document.querySelectorAll('main > section')].map(s=>s.id)") == ["s1", "how", "try"]
    assert " ".join(p.inner_text("#h1").split()) == HEAD
    assert p.inner_text("#s1 .sub").strip() == "An open protocol and router for agents."
    assert p.get_attribute('meta[name="description"]', "content") == LINE
    assert p.get_attribute('meta[property="og:description"]', "content") == LINE
    # the coloured headline: two gradient phrases, the promise up top and the mechanism in the subline
    assert p.locator("#h1 [class^=k]").count() == 3  # real-world data, on demand, provenance light in turn
    assert p.locator("#h1 .ln").all_inner_texts() == ["Real-world data on demand,", "with provenance."]
    # the centred header: Sources | irlpls | For agents
    xs = p.evaluate("[...document.querySelectorAll('.top a')].map(a=>{const r=a.getBoundingClientRect();return [a.textContent.trim(), r.left+r.width/2]})")
    assert [x[0] for x in xs] == ["Find data", "irlpls", "For agents"] and abs(xs[1][1] - 195) <= 2


@pytest.mark.parametrize("w", [360, 375, 390])
def test_headline_lines_stay_whole_on_phones(browser, base, w):
    p = landing(browser, base, w, 700, q="?still=1")
    r = p.evaluate("""[...document.querySelectorAll('#h1 .ln')].map(l=>{const g=document.createRange();g.selectNodeContents(l);
      return [g.getBoundingClientRect().width, l.clientWidth, l.getClientRects().length, g.getClientRects().length > 0 && new Set([...g.getClientRects()].map(x=>Math.round(x.top))).size]})""")
    for text_w, box_w, _, tops in r:
        assert text_w <= box_w + 0.5 and tops == 1, (w, r)


def test_screen_one_is_self_contained(browser, base):
    """Alternative screen-1 designs are swapped in whole: its style and script live inside the section."""
    html = (SITE / "index.html").read_text()
    s1 = html[html.index('<section class="scr" id="s1"'):]
    s1 = s1[:s1.index("</section>")]
    assert "<style>" in s1 and "<script>" in s1
    p = landing(browser, base, q="?still=1")
    assert p.evaluate("document.querySelectorAll('#s1 #track, #s1 #pd, #s1 #prev, #s1 #next, #s1 .cta').length") == 5
    assert p.locator("#track .chat").count() == 5


def cur(p):
    return p.evaluate("[...document.querySelectorAll('#pd button')].findIndex(b=>b.getAttribute('aria-current')==='true')")


def test_five_example_chats_each_linked_to_a_real_card(browser, base):
    p = landing(browser, base, q="?still=1")
    seen = []
    for i in range(5):
        c = p.locator(f"#ex{i}")
        assert c.locator(".you").inner_text().strip() and c.locator(".ans").inner_text().strip()
        assert c.locator(".media img, .media video").count() == 1
        assert c.locator("figcaption").count() == 0  # no caption line under the image; the source chip says where and when
        assert c.locator(".chip .dots i").count() == 8 and " · " in c.locator(".chip .nm").inner_text()
        href = c.locator(".pv a").get_attribute("href")
        assert p.request.get(base + "/" + href).ok, href
        seen.append(href)
    assert len(set(seen)) == 5
    assert p.inner_text("#ex0 .chip .nm") == "USGS · stream gauge"
    # all five examples are samples (the owner, 2026-10-30: the river too, a saved USGS frame and reading): each says
    # "Sample" small, inside the photo's corner; the chip is the normal one (source and what it is), never a live-looking age
    for i in range(0, 5):
        nm = p.inner_text(f"#ex{i} .chip .nm")
        assert "Sample" not in nm and " · " in nm, nm
        assert not any(w in nm for w in ("ago", "today", "within", "morning", "new pass", "first clips")), nm
        lab = p.locator(f"#ex{i} .media .smp")
        assert lab.inner_text() == "Sample"
        m, b = p.locator(f"#ex{i} .media").bounding_box(), lab.bounding_box()
        # the owner: technically there, barely visible: inside the photo's bottom-right corner, tiny, faint, no pill
        assert b["x"] >= m["x"] + m["width"] * 2 / 3 and b["x"] + b["width"] <= m["x"] + m["width"] and b["y"] >= m["y"] + m["height"] - 30 and b["y"] + b["height"] <= m["y"] + m["height"], (m, b)
        st = p.evaluate(f"(()=>{{const c=getComputedStyle(document.querySelector('#ex{i} .smp'));return [parseFloat(c.fontSize),c.backgroundColor,c.color]}})()")
        assert st[0] <= 10 and st[1] in ("rgba(0, 0, 0, 0)", "transparent") and st[2].startswith("rgba("), st


TITLES = ["Real person", "Captured locally", "Genuine device", "Sealed capture", "Known timestamp", "Tracked in transit", "Independently verified", "Delivered to you"]


def test_source_chip_opens_the_eight_stages_in_short_words(browser, base):
    p = landing(browser, base, q="?still=1")
    p.click("#ex0 .cite")
    assert p.get_attribute("#ex0 .cite", "aria-expanded") == "true"
    rows = p.locator("#pv0 li")
    assert rows.count() == 8
    assert rows.locator("span").all_inner_texts() == TITLES  # one vocabulary: the chip, screen 2 and chain.html
    # the owner's pick (docs/research/pv-design/icons.md): a line icon per stage, its state the level, one short level word; no grey
    # subtext rows and no tally line. The honest wording stays for screen readers: each row's aria-label.
    labels = [rows.nth(k).get_attribute("aria-label") for k in range(8)]
    assert [x.split(": ", 1)[0] for x in labels] == TITLES, labels
    says = [x.split(": ", 1)[1] for x in labels]
    assert all(s in ("Claimed", "Evidenced", "Not stated", "Reported (a time is stated, not attested)",
                     "Claimed (the provider says it was checked; no public evidence)") for s in says), says
    # round 4 (security reviewer): a USGS gauge never lights a C2PA stage from a plain "content: claimed"; its reported time
    # is not an attested one; self-reported cross-checks say so; a compiled card is never "claimed by the provider"
    assert says[3] == "Not stated" and says[4] == "Reported (a time is stated, not attested)", says
    assert says[6] in ("Not stated", "Claimed (the provider says it was checked; no public evidence)"), says
    words = rows.locator("em").all_inner_texts()
    assert words == [x.split(" (")[0] for x in says], words  # the level word leads its full wording
    assert rows.locator("i svg").count() == 8  # one line icon per stage
    assert p.locator("#pv0 small, #pv0 .age, #pv0 .pn, #pv0 .px").count() == 0  # no subtext, no tally line, no X
    assert not any(w in p.inner_text("#pv0") for w in ("links claimed", "· as stated on its card"))
    # who stated it labels the list (and the chip)
    assert "stated on its card (compiled from public sources)" in p.get_attribute("#pv0 ol", "aria-label")
    assert "stated on its card (compiled from public sources)" in p.get_attribute("#ex0 .cite", "aria-label")
    assert "claimed by the provider" not in p.inner_text("#pv0")
    # the rows' levels match the card's stage dots, in order (IRLMatch.stageMark; a grey ring = n)
    dots = p.evaluate("[...document.querySelectorAll('#ex0 .chip .dots i')].map(i=>i.className||'n')")
    marks = p.evaluate("[...document.querySelectorAll('#pv0 li')].map(i=>i.className)")
    assert dots == marks
    # evidenced is the only colour: a filled green disc; claimed an ink outline; reported its own dashed ring; not stated faint
    st = p.evaluate("""[...document.querySelectorAll('#pv0 li')].map(l=>{const i=getComputedStyle(l.querySelector('i'));
      return [l.className,i.backgroundColor,i.borderTopStyle,i.boxShadow]})""")
    for cls, bg, border, sh in st:
        if cls == "r":
            assert border == "dashed", st
        elif cls == "c":
            assert "16, 20, 24" in sh and bg == "rgb(255, 255, 255)", st
        elif cls == "n":
            assert "223, 227, 233" in sh, st
    p.click("#ex0 .pxb")  # the sheet lies over the reply; close it (the chevron at its foot) to reach the tool row
    p.wait_for_timeout(500)
    p.click("#ex0 .tl")
    assert "USGS stream gauges" in p.inner_text("#tp0") and "irlpls_request" in p.inner_text("#tp0")


def test_source_chip_opens_in_place_without_scrolling(browser, base):
    """Opening the chip never scrolls the page or moves the headline under the header; the card keeps its height."""
    for w, h in ((390, 709), (375, 553)):
        p = landing(browser, base, w, h, q="?still=1")
        before = p.evaluate("[scrollY, document.getElementById('ex0').getBoundingClientRect().height, document.querySelector('#ex0 .cite').getBoundingClientRect().top]")
        p.click("#ex0 .cite")
        p.wait_for_timeout(500)
        after = p.evaluate("[scrollY, document.getElementById('ex0').getBoundingClientRect().height, document.querySelector('#ex0 .cite').getBoundingClientRect().top]")
        assert after[0] == before[0] and abs(after[1] - before[1]) < 1 and abs(after[2] - before[2]) < 1, (w, before, after)
        top, hb = p.evaluate("[document.getElementById('h1').getBoundingClientRect().top, document.querySelector('.top').getBoundingClientRect().bottom]")
        assert top >= hb, (top, hb)
        assert p.is_visible("#pv0 li >> nth=0")


SHEET_FITS = """(i)=>{const v=document.getElementById('pv'+i),r=v.getBoundingClientRect(),c=document.getElementById('ex'+i).getBoundingClientRect();
  const out=[...v.querySelectorAll('li, a, .ph, p')].filter(e=>e.getClientRects().length).filter(e=>{const b=e.getBoundingClientRect();return b.top<r.top-1||b.bottom>r.bottom+1||b.left<r.left-1||b.right>r.right+1});
  return {scroll: v.scrollHeight - v.clientHeight, overflowY: getComputedStyle(v).overflowY, out: out.map(e=>e.textContent.slice(0,30)),
          inCard: r.top>=c.top-1&&r.bottom<=c.bottom+1, rows: v.querySelectorAll('li').length}}"""


@pytest.mark.parametrize("w,h", [(390, 709), (375, 553), (360, 640), (390, 664), (390, 844)])
def test_provenance_sheet_shows_all_eight_stages_without_scrolling(browser, base, w, h):
    """Owner review (VOICE-1028 #3): the open chip showed a scroll view that hid stages. Every stage and the card link fit, on every card."""
    p = landing(browser, base, w, h, q="?still=1")
    for i in range(5):
        p.evaluate(f"document.getElementById('track').scrollLeft=document.getElementById('ex{i}').offsetLeft-document.getElementById('ex0').offsetLeft")
        p.click(f"#ex{i} .cite")
        p.wait_for_timeout(700)
        r = p.evaluate(SHEET_FITS, i)
        assert r["rows"] == 8 and r["scroll"] <= 1 and r["overflowY"] != "auto" and not r["out"] and r["inCard"], (w, h, i, r)
        p.click(f"#ex{i} .pxb")
        p.wait_for_timeout(500)


def test_provenance_sheet_grows_from_the_chip_and_closes_back(browser, base):
    """Owner review (VOICE-1028 #4): a calm transition, nothing jumps. The sheet's clip starts on the chip, the card never moves,
    and it closes with the chevron at its foot (or Escape), giving focus back to the chip. No X button."""
    p = landing(browser, base, q="?still=1")
    p.evaluate("""window.__r=[];(function f(){const c=document.getElementById('ex0').getBoundingClientRect(),h=document.getElementById('h1').getBoundingClientRect();
      __r.push([Math.round(c.top),Math.round(c.height),Math.round(h.top),scrollY]);if(__r.length<400)requestAnimationFrame(f)})()""")
    p.click("#ex0 .cite")
    clip = p.evaluate("getComputedStyle(document.getElementById('pv0')).clipPath")
    assert clip != "none" and "inset(0px" not in clip.replace("inset(0px 0px 0px 0px", "x"), clip  # mid-way, not already full
    p.wait_for_timeout(700)
    assert p.evaluate("getComputedStyle(document.getElementById('pv0')).clipPath").startswith("inset(0px")
    assert p.evaluate("document.activeElement.classList.contains('pxb')") and p.locator("#pv0 .px").count() == 0
    assert p.evaluate("+getComputedStyle(document.querySelector('#ex0 .turn')).opacity") < .05
    p.click("#ex0 .pxb")
    p.wait_for_timeout(700)
    assert p.get_attribute("#ex0 .cite", "aria-expanded") == "false"
    assert p.evaluate("document.activeElement.classList.contains('cite')")
    assert p.evaluate("getComputedStyle(document.getElementById('pv0')).visibility") == "hidden"
    p.click("#ex0 .cite")
    p.wait_for_timeout(600)
    p.keyboard.press("Escape")
    p.wait_for_timeout(600)
    assert p.get_attribute("#ex0 .cite", "aria-expanded") == "false"
    states = {tuple(x) for x in p.evaluate("__r")}
    assert len(states) == 1, states  # the card, the headline and the page never moved


def test_swipe_nudge_plays_once_when_screen_one_is_in_view(browser, base):
    """Owner review (VOICE-1028 #5): the one-time hint was not visible. After the first reply arrives the row slides ~32px and back, once."""
    p = landing(browser, base)
    p.evaluate("""window.__tx=[];(function f(){const m=getComputedStyle(document.getElementById('ex0')).transform;
      __tx.push(m==='none'?0:new DOMMatrix(m).m41);requestAnimationFrame(f)})()""")
    p.wait_for_timeout(7500)  # the reply plays on load (up to ~4.1 s), then the nudge (0.95 s later, 1.4 s long)
    tx = p.evaluate("__tx")
    assert min(tx) <= -28, min(tx)
    # never overlapping: the nudge starts only after the reply has fully arrived
    assert tx[-1] == 0 and p.evaluate("document.getElementById('ex1').getBoundingClientRect().left") < 390  # settled; the next card peeks
    moves = sum(1 for a, b in zip(tx, tx[1:]) if a == 0 and b != 0)
    assert moves == 1, moves
    r = landing(browser, base, reduced_motion="reduce")
    r.wait_for_timeout(3500)
    assert not r.evaluate("document.getElementById('track').classList.contains('nudge')")


def test_carousel_shows_no_focus_ring_after_a_swipe(browser, base):
    p = landing(browser, base, q="?still=1", has_touch=True, is_mobile=True)
    p.tap("#ex0 .you")
    p.evaluate("document.getElementById('track').focus()")
    assert p.evaluate("getComputedStyle(document.getElementById('track')).outlineStyle") == "none"
    p.keyboard.press("Tab")
    p.evaluate("document.getElementById('track').focus()")
    assert p.evaluate("document.getElementById('track').classList.contains('ptr')") is False


def test_dots_and_arrows_move_between_examples(browser, base):
    p = landing(browser, base, 1440, 900, q="?still=1")  # a pointer device: the arrows show
    assert cur(p) == 0 and p.is_disabled("#prev")
    p.click("#next")
    p.wait_for_function("document.querySelector('#pd button:nth-child(2)').getAttribute('aria-current')==='true'", timeout=3000)
    p.click("#pd button:nth-child(5)")
    p.wait_for_function("document.querySelector('#pd button:nth-child(5)').getAttribute('aria-current')==='true'", timeout=3000)
    assert p.is_disabled("#next")
    p.click("#prev")
    p.wait_for_function("document.querySelector('#pd button:nth-child(4)').getAttribute('aria-current')==='true'", timeout=3000)
    # a touch phone: no arrows, swipe or dots; the next card peeks in
    t = landing(browser, base, q="?still=1", has_touch=True, is_mobile=True)
    assert not t.is_visible("#next")
    left = t.evaluate("document.getElementById('ex1').getBoundingClientRect().left")
    assert 300 < left < 390, left
    t.evaluate("document.getElementById('track').scrollTo({left:document.getElementById('ex1').offsetLeft-document.getElementById('ex0').offsetLeft})")
    t.wait_for_function("document.querySelector('#pd button:nth-child(2)').getAttribute('aria-current')==='true'", timeout=3000)
    # ?ex=N opens example N
    e = landing(browser, base, q="?still=1&ex=3")
    assert cur(e) == 3


def test_every_card_plays_its_reply_the_first_time_it_comes_into_view(browser, base):
    """the owner: no card starts complete. Every card waits prepared (question only, photo hidden, not blurred) until it first
    comes into view, then plays its reply once: status, the answer word by word, then the photo, then the source chip."""
    p = landing(browser, base)
    hidden = "c=>c.querySelector('.att').classList.contains('hide')"
    assert p.locator("#ex0").evaluate(hidden) and p.locator("#ex0 .ans").evaluate("a=>a.classList.contains('wait')")
    # the reply evolves like a chat: nothing of the photo shows until it has arrived, and it is hidden, not blurred.
    # its place is held at the photo's size, plain white (the owner, 2026-10-30: no grey placeholder)
    assert p.evaluate("getComputedStyle(document.querySelector('#ex0 .media')).backgroundColor") in ("rgba(0, 0, 0, 0)", "transparent")
    assert p.evaluate("+getComputedStyle(document.querySelector('#ex0 .media img')).opacity") < 0.1
    assert p.evaluate("+getComputedStyle(document.querySelector('#ex0 .att')).opacity") > 0.9
    assert "blur" not in p.evaluate("getComputedStyle(document.querySelector('#ex0 .media img')).filter + getComputedStyle(document.querySelector('#ex0 .att')).filter")
    for i in range(1, 5):  # off-screen cards start prepared, not complete
        c = p.locator(f"#ex{i}")
        assert c.evaluate(hidden) and c.evaluate("c=>c.querySelector('.cites').classList.contains('hide')"), i
        assert c.locator(".you").inner_text().strip(), i
    p.wait_for_function("!document.querySelector('#ex0 .att').classList.contains('hide') && !document.querySelector('#ex0 .cites').classList.contains('hide')", timeout=14000)
    p.click("#pd button:nth-child(3)")
    p.wait_for_function("document.querySelector('#pd button:nth-child(3)').getAttribute('aria-current')==='true'", timeout=3000)
    # it plays on arrival (not already complete), then completes
    assert p.locator("#ex2").evaluate("c=>c.querySelectorAll('.hide,.wait').length") > 0
    p.wait_for_function("document.querySelectorAll('#ex2 .hide, #ex2 .wait, #ex2 .run').length===0", timeout=6000)
    assert p.locator("#ex2 .att").evaluate("a=>+getComputedStyle(a).opacity") > .9


def test_each_card_shows_something_useful_fast_and_arrives_within_3_2s(browser, base):
    """No blank card: 0.8 s after its turn the question and a status line show; within 4.8 s everything has arrived (answer at
    reading pace, 120 ms a word, at most 3 s, then the photo, then the chip). The first card's turn is on load (the owner,
    2026-10-30: never waiting for the headline); a swiped-to card's at once."""
    p = landing(browser, base)  # landing() waits 0.4 s
    p.wait_for_timeout(400)
    vis = "s=>{const e=document.querySelector(s);return +getComputedStyle(e).opacity>.5&&e.getBoundingClientRect().height>0}"
    assert p.evaluate(vis, "#ex0 .you") and p.evaluate(vis, "#ex0 .tool")
    assert p.inner_text("#ex0 .tt").strip()
    p.wait_for_timeout(4400)
    assert p.evaluate("[...document.querySelectorAll('#ex0 .hide, #ex0 .wait')].length") == 0
    # a swiped-to card: same budget, counted from when it is in view
    p.click("#pd button:nth-child(2)")
    p.wait_for_function("document.querySelector('#pd button:nth-child(2)').getAttribute('aria-current')==='true'", timeout=3000)
    p.wait_for_timeout(800)
    assert p.evaluate(vis, "#ex1 .you") and p.evaluate(vis, "#ex1 .tool") and p.inner_text("#ex1 .tt").strip()
    p.wait_for_timeout(4400)
    assert p.evaluate("[...document.querySelectorAll('#ex1 .hide, #ex1 .wait')].length") == 0


def test_reduced_motion_shows_everything_at_once(browser, base):
    p = landing(browser, base, reduced_motion="reduce")
    assert p.evaluate("[...document.querySelectorAll('#track .hide, #track .wait, #track .run')].length") == 0


def test_cards_come_from_cards_json_with_the_embedded_copy_as_fallback(browser, base):
    import json
    full = json.loads((SITE / "cards.json").read_text())
    few = {"cards": [c for c in full["cards"] if c["id"] in ("usgs-water", "rentahuman", "proxypics", "skyfi", "kled")]}
    p = landing(browser, base, q="?still=1", cards=lambda r: r.fulfill(status=200, body=json.dumps(few), headers={"content-type": "application/json"}))
    p.wait_for_function("IRL3.cards.length===5", timeout=4000)
    p.wait_for_function("document.getElementById('bm').textContent.indexOf('Of 5 providers')===0", timeout=2000)  # the counts re-ran on them
    p = landing(browser, base, q="?still=1", cards=lambda r: r.abort())
    n = p.evaluate("IRL3.cards.length")
    assert n >= 50, n  # the snapshot build.py embeds
    assert p.inner_text("#bm").startswith(f"Of {n} providers, ")


def test_screen2_links_are_static_rows_with_one_benchmark_line(browser, base):
    """Screen 2 (owner's decisions, 2026-10-29): eight static rows, each a line icon, its title and the technical term;
    no chevrons, nothing to tap, no sheet. Under them one quiet benchmark line from the cards, then two quiet links."""
    p = landing(browser, base, q="?still=1")
    p.click("#s1 .cta")
    p.wait_for_timeout(1000)
    top, hb = p.evaluate("[document.querySelector('#h2').getBoundingClientRect().top, document.querySelector('.top').getBoundingClientRect().bottom]")
    assert top >= hb - 1, (top, hb)  # the header does not cover the screen
    assert p.inner_text("#h2") == "A standard of provenance."
    assert p.inner_text("#how .lede") == "Humans and machines need an authentic view of the world, from source to receipt."
    assert p.locator("#chain b").all_inner_texts() == TITLES
    terms = p.locator("#chain small").all_inner_texts()
    assert terms == [s[1] for s in p.evaluate("IRL3.STAGES")] and terms[0] == "ID + liveness" and terms[3] == "C2PA manifest"
    assert p.locator("#chain li i svg").count() == 8
    # static: no button, chevron, sheet or tap target in the rows
    assert p.locator("#chain button, #chain a, #chain s, #lks, #how dialog, #how [aria-haspopup]").count() == 0
    assert p.evaluate("getComputedStyle(document.querySelector('#chain li')).cursor") != "pointer"
    # the old capsule and the "checks none" line are gone from screen 2
    assert p.locator("#gap, #how .cap, #how .std").count() == 0
    body = p.inner_text("#how")
    assert "checks none" not in body and "prove any link" not in body
    # the benchmark: from the cards (IRL3.on), same counting as IRL3.bench
    n, c, e = p.evaluate("""(()=>{let c=0,e=0;IRL3.cards.forEach(x=>{const l=IRL3.levels(x);
      if(l.includes('evidenced')){e++;c++}else if(l.includes('claimed'))c++});return [IRL3.cards.length,c,e]})()""")
    tail = " None prove one yet." if e == 0 else f" {e} prove at least one."
    assert " ".join(p.inner_text("#bm").split()) == f"Of {n} providers, {c} claim at least one link.{tail}"
    # the owner, 2026-10-30: no 'See where each stands'; 'What each link means' is a quiet info icon beside the line;
    # a quiet arrow down to screen 3 on its own row
    assert p.locator("#bmf").count() == 0 and "See where each stands" not in body
    assert p.get_attribute("#bmc", "aria-label") == "What each link means" and p.get_attribute("#bmc", "href") == "chain.html" and not p.inner_text("#bmc").strip()
    assert p.get_attribute("#to3", "href") == "#try" and "Try it live" in p.inner_text("#to3")


def test_s2_benchmark_line_wording_with_fake_cards(browser, base):
    """The benchmark's general form: 'Of N providers, C claim at least one link.' then ' None prove one yet.' when E=0,
    else ' E prove at least one.' Fake cards carry their levels directly."""
    p = landing(browser, base, q="?still=1")
    run = """L=>{const keep=IRL3.levels;IRL3.levels=c=>c.L;try{return IRL3.bench(L.map(l=>({L:l})))}finally{IRL3.levels=keep}}"""
    none, cl, ev = ["none"] * 8, ["claimed"] + ["none"] * 7, ["evidenced"] + ["claimed"] * 7
    rep = ["none"] * 4 + ["reported"] + ["none"] * 3  # a time reported, not attested: not a claim of the link
    assert p.evaluate(run, [cl, cl, none, rep]) == "Of 4 providers, 2 claim at least one link. None prove one yet."
    assert p.evaluate(run, [cl, ev, ev, none, none]) == "Of 5 providers, 3 claim at least one link. 2 prove at least one."
    assert p.evaluate(run, []) == ""


def test_chain_page_defines_each_technical_term_first(browser, base):
    """chain.html: each link opens with a plain-English definition of its technical term, before the can/can't-prove detail;
    the page keeps its 'irlpls checks none of it' honesty."""
    c = page(browser)
    c.goto(base + "/chain.html")
    assert c.locator(".chain h2").all_inner_texts() == TITLES
    body = c.inner_text("main")
    assert "Each can prove" in body and "only if the position is authenticated" in body and "Custody: who held it" in body
    assert "Eight links from person to buyer" in body and "checks none of it" in body
    assert "unforgeable" not in body and "Untouched" not in body
    want = [("ID + liveness", "A government ID matched to a live face, so a real person, not a bot or a photo of one, is behind the capture."),
            ("C2PA Conforming Products List", "The official list of cameras and apps certified to sign where and when a photo was taken."),
            ("Key attestation (App Attest)", "Apple's and Google's way for an app to prove it's running, unmodified, on a real phone."),
            ("C2PA manifest", "A tamper-evident label the camera signs at capture, recording how the image was made. Any edit breaks the seal."),
            ("RFC 3161 token", "An independent timestamping service signs that this exact file existed at this moment."),
            ("Transparency log", "A public, append-only record of every hand-off. Entries can't be quietly changed later."),
            ("Published policy", "An outside party's published rules for checking captures, and a record of applying them."),
            ("Signed receipt", "The provider's signature over exactly what you received, so you can check nothing changed on the way.")]
    got = c.evaluate("""[...document.querySelectorAll('.chain .card')].map(k=>{const d=k.querySelector('.df'),pc=k.querySelector('.pc');
      return [d.querySelector('b').textContent, d.textContent.slice(d.querySelector('b').textContent.length).trim(),
        !!(d.compareDocumentPosition(pc)&Node.DOCUMENT_POSITION_FOLLOWING), d===k.querySelector('h2').nextElementSibling]})""")
    assert [g[:2] for g in got] == [list(w) for w in want]
    assert all(g[2] and g[3] for g in got), got
    # the terms match screen 2's
    p = landing(browser, base, q="?still=1")
    assert p.locator("#chain small").all_inner_texts() == [w[0] for w in want]


@pytest.mark.parametrize("w,h", [(390, 844), (375, 667)])
def test_screen_two_fits(browser, base, w, h):
    """390x844 shows the whole screen; 375x667 the heading and at least six links."""
    p = landing(browser, base, w, h, q="?still=1")
    p.evaluate("document.getElementById('how').scrollIntoView({block:'start',behavior:'instant'})")
    p.wait_for_timeout(300)
    r = p.evaluate("""(()=>{const b=e=>e.getBoundingClientRect(),L=[...document.querySelectorAll('#chain li')];
      return {end:b(document.getElementById('to3')).bottom, h2:b(document.getElementById('h2')).top, hb:b(document.querySelector('.top')).bottom,
        seen:L.filter(x=>b(x).bottom<=innerHeight).length, min:Math.min(...L.map(x=>b(x).height)), sw:document.documentElement.scrollWidth}})()""")
    assert r["h2"] >= r["hb"] - 1 and r["sw"] <= w, r
    assert r["min"] >= 40, r
    if h >= 844:
        assert r["end"] <= h, r
    assert r["seen"] >= (8 if h >= 844 else 6), r


def test_screen_two_rail_draws_once_and_is_still_when_asked(browser, base):
    """The rail draws down link by link when screen 2 is half in view; ?still=1 and reduced motion show it whole at once."""
    p = landing(browser, base, q="?still=1")
    assert not p.evaluate("document.documentElement.classList.contains('rail')")
    p = landing(browser, base, reduced_motion="reduce")
    assert not p.evaluate("document.documentElement.classList.contains('rail')")
    p = landing(browser, base)
    assert p.evaluate("document.documentElement.classList.contains('rail')")
    assert p.locator("#chain li.on").count() == 0
    p.evaluate("document.getElementById('how').scrollIntoView({block:'start'})")
    p.wait_for_timeout(4600)
    assert p.locator("#chain li.on").count() == 8


@pytest.mark.parametrize("w", [375, 390])
def test_sample_asks_are_one_quiet_line(browser, base, w):
    p = page(browser, w, 800)
    p.goto(base + "/find.html")
    p.wait_for_timeout(300)
    box = p.evaluate("""(()=>{const e=document.querySelector('#ex'),bs=[...e.querySelectorAll('button')];
      return {h:e.getBoundingClientRect().height,bh:bs[0].getBoundingClientRect().height,tops:[...new Set(bs.map(b=>Math.round(b.offsetTop)))].length,
        n:bs.length,pill:bs.some(b=>parseFloat(getComputedStyle(b).borderTopWidth)>0),sw:document.documentElement.scrollWidth}})()""")
    assert box["n"] >= 3 and box["tops"] == 1 and box["h"] <= box["bh"] + 1, box  # one row, never two
    assert box["bh"] >= 44 and not box["pill"] and box["sw"] <= w, box  # tappable, plain text, no sideways page scroll
    assert p.inner_text("#ex").startswith("Try:")


@pytest.mark.parametrize("q,want", [("river level Texas", "rows"), ("air quality in Delhi now", "rows"), ("xyzzy plugh", "empty"),
                                    ("follow my ex home tonight", "stop"), ("where is my neighbour right now", "stop")])
def test_finder_answers_or_says_why(browser, base, q, want):
    """Find data reads every ask (screen 3 used to): matches, an honest empty state, or a refusal with nothing listed."""
    p = finder(browser, base, q)
    rows = p.locator("#grid .row").count()
    if want == "rows":
        assert rows >= 1 and p.locator("#grid .row").first.locator(".dots i").count() == 8
    elif want == "empty":
        assert rows == 0 and "No provider covers this" in p.inner_text("#meta")
    else:
        assert rows == 0 and "won’t route this" in p.inner_text("#notes") and not p.locator("#best").is_visible()


@pytest.mark.parametrize("q,note", [
    ("photo proving a protest happened at the town hall in Kharkiv, Ukraine yesterday", "can’t capture the past"),
    ("storefront photo, Lisbon, last week", "can’t capture the past"),
    ("C2PA-signed photo of a polling station in Berlin, Germany with authenticated location", "None of these evidence that"),
])
def test_finder_says_plainly_what_it_cannot_do(browser, base, q, note):
    p = finder(browser, base, q)
    assert note in p.inner_text("#notes"), p.inner_text("#notes")
    assert p.evaluate("!!(document.getElementById('notes').compareDocumentPosition(document.getElementById('grid')) & Node.DOCUMENT_POSITION_FOLLOWING)")  # above the list


def test_finder_drops_providers_that_exclude_the_named_country(browser, base):
    p = finder(browser, base, "photo of the town hall in Kharkiv, Ukraine")
    ids = p.evaluate("[...document.querySelectorAll('#grid .row')].map(a=>a.dataset.id)")
    assert ids and "proxypics" not in ids, ids  # ProxyPics covers US addresses only


def test_no_match_offers_a_next_step(browser, base):
    p = finder(browser, base, "xyzzy plugh")
    assert "Try a broader ask" in p.inner_text("#meta")


def test_go_keeps_the_answer_in_view_and_says_it(browser, base):
    p = landing(browser, base, 390, 709, q="?still=1#try")
    fake_usgs(p, [(200, usgs_iv(), {})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .ans")
    p.wait_for_timeout(900)
    top, bot, hb = p.evaluate("[document.getElementById('lv').getBoundingClientRect().top, document.getElementById('lv').getBoundingClientRect().bottom, document.querySelector('.top').getBoundingClientRect().bottom]")
    assert top >= hb - 1 and bot <= 709 + 1, (top, bot, hb)
    assert "from USGS" in p.inner_text("#ann")


def test_long_asks_wrap_inside_the_field(browser, base):
    p = landing(browser, base, 375, 667, q="?still=1#try")
    q = "egocentric video of people loading a dishwasher, 200 hours, with AI training rights"
    p.fill("#q", q)
    box = p.evaluate("(()=>{const e=document.getElementById('q');return [e.scrollHeight,e.clientHeight,e.scrollWidth,e.clientWidth]})()")
    assert box[1] > 44 and box[0] <= box[1] + 1 and box[2] <= box[3] + 1, box  # grows to show it all
    assert p.input_value("#q") == q


def test_screen_three_ends_on_see_the_docs(browser, base):
    """The owner, 2026-10-30: the last call to action at the bottom of the landing reads 'See the docs' (docs.html)."""
    p = landing(browser, base, q="?still=1#try")
    assert p.inner_text("#try #docs").strip() == "See the docs" and p.get_attribute("#docs", "href") == "docs.html"
    assert p.request.get(base + "/docs.html").ok and p.locator("#dev").count() == 0
    assert p.evaluate("document.querySelector('#standard') === null")


def test_live_ask_sends_nothing_typed(browser, base):
    """Only a map point (or a station id) leaves the browser, never the words."""
    p = landing(browser, base, q="?still=1#try")
    fake_usgs(p, [(200, usgs_iv(), {})])
    sent = []
    p.on("request", lambda r: sent.append(r.url + " " + (r.post_data or "")))
    p.click('#svc .sv[data-id="usgs-water"]')
    p.fill("#q", "")
    p.type("#q", "river zanzibarqq at Austin, Texas", delay=5)
    p.press("#q", "Enter")
    p.wait_for_selector("#lv .ans, #lv .lvm")
    assert not any("zanzibarqq" in s for s in sent), sent


def test_query_deep_link(browser, base):
    p = landing(browser, base, q="?still=1&svc=nws&q=weather%20in%20Denver%20now#try")
    assert p.input_value("#q") == "weather in Denver now"
    assert p.get_attribute('#svc .sv[data-id="nws"]', "aria-pressed") == "true" and p.locator("#lv").count() == 0  # filled, not asked
    p = landing(browser, base, q="?still=1&svc=usgs-water&q=river%2C%20Texas&go=1#try")
    fake_usgs(p, [(200, usgs_iv(), {})])
    p.reload()
    p.wait_for_selector("#lv .ans", timeout=8000)
    assert "76.3 ft³/s" in p.inner_text("#lv .ans")


@pytest.mark.parametrize("w,h", [(320, 568), (375, 553), (390, 664), (568, 320), (1440, 900)])
def test_landing_has_no_sideways_scroll(browser, base, w, h):
    p = landing(browser, base, w, h, q="?still=1")
    assert p.evaluate("document.documentElement.scrollWidth") <= w


def test_landing_links_resolve_and_dark_variant_is_gone(browser, base):
    p = landing(browser, base, q="?still=1")
    hrefs = p.evaluate("[...document.querySelectorAll('.top a, .site-foot a, #how a, #try #docs')].map(a=>a.getAttribute('href')).filter(h=>!h.startsWith('mailto:')&&!h.startsWith('#'))")
    assert "chain.html" in hrefs and "standard.html" in hrefs
    for h in set(hrefs):
        assert p.request.get(base + "/" + h).ok, h
    assert p.request.get(base + "/dark.html").status == 404


def test_link_preview_assets_exist(browser, base):
    p = page(browser)
    for path in ("/media/og.png", "/robots.txt", "/sitemap.xml", "/favicon.svg"):
        assert p.request.get(base + path).ok, path


def test_agents_page_has_the_manual_and_provider_index(base, browser):
    pg = page(browser)
    pg.goto(base + "/agents.html")
    assert pg.locator("h1").inner_text() == "irlpls for agents"
    assert "your agent" not in pg.locator(".agents-head").inner_text()  # the page is the manual; the copy box stands on its own
    assert pg.locator('[id="13-rules-for-you"]').count() == 1
    assert pg.locator("#main .tbl").last.locator("tbody tr").count() >= 10
    md = (SITE / "agents.md").read_text()
    assert md.startswith("# irlpls for agents") and "## 14. Providers listed today" in md




# ---------- round 4 ----------

def test_stage_mapping_is_shared_with_the_card_pages(browser, base):
    """One stage mapping: the landing's IRL3.levels (chip, screen 3 dots) and build.py's stage_levels (card pages) agree on
    every card; Sealed capture only with a stated signing mechanism; Known timestamp only when attested."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("build", SITE.parent / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    cards = json.loads((SITE / "cards.json").read_text())["cards"]
    p = landing(browser, base, q="?still=1")
    p.wait_for_function("IRL3.cards.length > 50")
    js = p.evaluate("cs => cs.map(c => IRL3.levels(c))", cards)
    assert js == [b.stage_levels(c) for c in cards]
    # the embedded snapshot carries what the mapping reads (no fetch): the same answer
    q = landing(browser, base, q="?still=1", cards=lambda r: r.abort())
    snap = q.evaluate("Object.fromEntries(IRL3.cards.map(c => [c.id, IRL3.levels(c)]))")
    assert all(snap[c["id"]] == b.stage_levels(c) for c in cards if c["id"] in snap)
    by = {c["id"]: c for c in cards}
    assert b.stage_levels(by["usgs-water"])[3] == "none"           # "content: claimed" is not a seal
    assert b.stage_levels(by["usgs-water"])[4] == "reported"       # a reported time, not attested
    assert b.stage_levels(by["vangrid"])[4] == "claimed"           # on-chain Merkle anchor, but the leaf encoding is unpublished
    assert b.stage_levels(by["precisely-ground-level-images"])[4] == "none"  # a negative time.unknown never lights "reported"
    for c in cards:  # Tracked in transit / Delivered to you: the card's provenance block, nothing else
        pv = c.get("provenance") or {}
        assert b.stage_levels(c)[5] == (pv.get("custody") or {}).get("level", "none"), c["id"]
        assert b.stage_levels(c)[7] == (pv.get("receipt") or {}).get("level", "none"), c["id"]
    for c in cards:  # a plain content claim never lights Sealed capture
        v = c["verification"]
        if v["content"]["level"] != "none" and v["camera"].get("standard") != "c2pa" and not b.SEAL_RX.search(
                (v["camera"].get("method") or "") + " " + (v["content"].get("method") or "")):
            assert b.stage_levels(c)[3] == "none", c["id"]


def test_card_page_explains_seven_guarantees_as_eight_stages(browser, base):
    p = page(browser)
    p.goto(base + "/c/usgs-water.html")
    t = " ".join(p.inner_text("#label").split())
    assert "Compiled from public sources, unclaimed. What they mean" in t
    # full pass 2026-10-29: one proof vocabulary. The eight stages only; the seven guarantees are folded into the stage rows
    # that read them, so there is never a second tally ("3 of 7 claimed") beside "1 of 8 links claimed"
    heads = p.evaluate("[...document.querySelectorAll('.cardv h2.sec')].map(h=>h.textContent)")
    assert "Eight links" in heads and not [h for h in heads if "guarantee" in h.lower() or "of 7" in h], heads
    assert "of 7" not in t and "None" not in t.split("Sources")[0]
    # sweep 2026-10-29: "1 claimed" beside three "Claimed" badges read as a contradiction; each count names its unit
    assert p.inner_text(".stg-t") == "1 of 8 links claimed · 0 evidenced"
    p.click(".stg summary")  # each stage, with the guarantees it draws on, one tap away
    assert p.get_attribute(".stg-l a", "href") == "../chain.html"
    rows = p.locator(".stg-o > li")
    assert rows.count() == 8 and "Sealed capture" in rows.nth(3).inner_text() and rows.nth(3).locator("b").first.inner_text() == "Not stated"
    assert rows.nth(4).locator("b").first.inner_text() == "Reported"
    # the guarantee behind each stage, with its own level, is the row's detail: Time is Claimed, the stage Reported, and the rule says why
    d4 = rows.nth(4).locator(".sd").inner_text()
    assert d4.startswith("Time · ") and "Reported" in d4, d4
    assert all(rows.nth(i).locator("b").first.inner_text() in ("Evidenced", "Claimed", "Reported", "Not stated") for i in range(8))
    c = page(browser)
    c.goto(base + "/chain.html#guarantees")
    assert "guarantees" in c.inner_text("#guarantees") and "Not stated" in c.inner_text("#guarantees")
    # the mapping paragraph sits below the eight stages, not before stage 1
    assert c.evaluate("document.getElementById('guarantees').getBoundingClientRect().top > document.getElementById('s8').getBoundingClientRect().top")


def test_chain_heading_is_not_under_the_header_when_scrolled_to(browser, base):
    p = landing(browser, base, q="?still=1")
    # round 6: screens 2 and 3 are page-aligned (the header lies over their own top padding), so scrolled to, their first
    # line sits at least 16px under the header
    for sel, first, gap in (("#h2", "#h2", 0), ("#how", "#h2", 16), ("#h3", "#h3", 0), ("#try", "#h3", 16)):
        p.evaluate(f"document.querySelector('{sel}').scrollIntoView({{block:'start'}})")
        p.wait_for_timeout(200)
        top, hb = p.evaluate(f"[document.querySelector('{first}').getBoundingClientRect().top, document.querySelector('.top').getBoundingClientRect().bottom]")
        assert top >= hb + gap - 1, (sel, top, hb)
    p = landing(browser, base, q="?still=1#h2")
    p.wait_for_timeout(800)
    top, hb = p.evaluate("[document.getElementById('h2').getBoundingClientRect().top, document.querySelector('.top').getBoundingClientRect().bottom]")
    assert top >= hb - 1


@pytest.mark.parametrize("q,want", [("someone to check on my mom's house in Tampa, Florida after the storm", "rows"),
                                    ("photo of my dad's farm roof after the hail", "rows"),
                                    ("where is my mom right now", "stop"), ("follow my ex", "stop"), ("photograph my neighbour", "stop")])
def test_a_family_members_house_is_a_place(browser, base, q, want):
    p = finder(browser, base, q)
    assert ("won’t route this" in p.inner_text("#notes")) == (want == "stop")
    if want == "rows":
        assert p.locator("#grid .row").count() >= 1


@pytest.mark.parametrize("q", ["verify a photo of a flooded street in Lagos, Nigeria", "is this video real?", "check this footage from the protest",
                               "is the dashcam footage of the hit-and-run on Market St real?", "authenticate a picture of the crash"])
def test_existing_media_asks_get_the_plain_note(browser, base, q):
    p = finder(browser, base, q)
    note = "irlpls can’t check a photo or video you already have; it finds who can capture something new, and shows what each can prove."
    assert note in " ".join(p.inner_text("#notes").split()), p.inner_text("#notes")


@pytest.mark.parametrize("q", ["storefront photo, Lisbon", "photo of a burned building in Kharkiv, Ukraine taken today", "500 egocentric clips"])
def test_new_capture_asks_get_no_existing_media_note(browser, base, q):
    p = finder(browser, base, q)
    assert "already have" not in p.inner_text("#notes")


def test_single_photo_ranks_on_site_capture_first(browser, base):
    p = finder(browser, base, "photo of a burned building in Kharkiv, Ukraine taken today")
    ids = p.evaluate("[...document.querySelectorAll('#grid .row')].map(a=>a.dataset.id)")
    assert ids[0] == "rentahuman", ids


def test_golden_gate_ask_leads_with_caltrans(browser, base):
    p = finder(browser, base, "live webcam view of traffic on the Golden Gate Bridge right now")
    ids = p.evaluate("[...document.querySelectorAll('#grid .row')].map(a=>a.dataset.id)")
    assert ids[0] == "caltrans-cctv" and "fintraffic-weathercams" not in ids[:3] and "tfl-jamcams" not in ids[:3], ids




# ---------- card checker: Check the proof ----------

def _proof_fixture():
    """A fixture card, a sample pack with a valid ES256 receipt, one with a tampered receipt, a CORS-blocked pack, and an
    evidence link that is not a sample pack (listed in a quiet line, never fetched).
    Everything is made up here and served by Playwright routes; nothing touches the network."""
    import copy
    import hashlib
    import json
    import sys
    sys.path.insert(0, str(SITE.parent))
    from irlpls.protocol.receipts import ReceiptSigner
    art = "irlpls fixture artefact: gauge photo stand-in, café ✓\n".encode()
    signer = ReceiptSigner.generate(kid="fixture-2026-10")
    payload = {"iss": "https://provider.example", "order_id": "ord_1", "item_id": "itm_1", "nonce": "n-fixture",
               "request_sha256": "ab" * 32, "phenomenon_time": "2026-10-28T12:00:00Z", "issued_at": "2026-10-28T12:05:00Z",
               "lat": 38.5, "lon": -122.25, "precision_m": 10,
               "media": {"sha256": hashlib.sha256(art).hexdigest(), "media_type": "text/plain", "bytes": len(art)}}
    good = signer.sign(payload)
    bad = copy.deepcopy(good)
    bad["payload"]["nonce"] = "n-tampered"
    pack = lambda proof: {"irlpls_sample_pack": "2026-10-26", "stage": "receipt", "type": "jws-receipt",
                          "artefact": "../artefact.txt", "proof": proof, "trust_anchor": "jwks.json", "readme": "README.md"}
    files = {"/artefact.txt": art, "/good/jwks.json": {"keys": [signer.public_jwk()]},
             "/bad/jwks.json": {"keys": [signer.public_jwk()]},
             "/good/receipt.json": good, "/bad/receipt.json": bad,
             "/good/irlpls-sample.json": pack("receipt.json"), "/bad/irlpls-sample.json": pack("receipt.json")}
    files = {k: v if isinstance(v, bytes) else json.dumps(v).encode() for k, v in files.items()}
    card = json.loads((SITE.parent / "cards" / "human-for-ai.json").read_text())
    for g in ("verification", "provenance"):
        for e in (card.get(g) or {}).values():
            if isinstance(e, dict):
                e.pop("evidence_url", None)
    card.update(id="fixture-provider", provider_url="https://provider.example")
    card.pop("protocol", None)
    card["provenance"]["receipt"].update(evidence_url="https://provider.example/good/irlpls-sample.json",
                                         method="Signs each delivery with ES256 over the file hash.")
    card["provenance"]["custody"] = {"level": "claimed", "method": "Logs each hand-off with a hash.",
                                     "unknown": "Who held it between logs.",
                                     "evidence_url": "https://provider.example/bad/irlpls-sample.json"}
    card["verification"]["human"]["evidence_url"] = "https://blocked.example/irlpls-sample.json"
    card["verification"]["time"]["evidence_url"] = "https://provider.example/docs/how-we-sign"
    return card, files


def _proof_page(browser, base):
    card, files = _proof_fixture()
    p = page(browser)
    p.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)

    seen = []

    def serve(route):
        u = route.request.url
        seen.append(u)
        if u.startswith("https://provider.example/"):
            path = "/" + u.split("/", 3)[3]
            body = files.get(path)
            return route.fulfill(status=200 if body else 404, body=body or b"",
                                 headers={"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"})
        # anything else, blocked.example included, fails the way a CORS refusal does: fetch() rejects with no detail
        # (Playwright's fulfill skips CORS checks, so a missing Access-Control-Allow-Origin cannot be shown that way)
        return route.abort("accessdenied")
    p.route("https://**/*", serve)
    p.goto(base + "/check.html")
    p.fill("#json", __import__("json").dumps(card))
    p.click("#go")
    p.wait_for_function("document.querySelectorAll('#proofs li.pv').length===3 && "
                        "[...document.querySelectorAll('#proofs li.pv')].every(l=>l.dataset.verdict)")
    p.seen = seen
    return p, {li.get_attribute("data-where"): li for li in p.locator("#proofs li.pv").all()}


def test_check_a_sample_pack_verifies_tamper_fails_and_cors_is_not_checked(browser, base):
    p, items = _proof_page(browser, base)
    assert p.locator("#proof").is_visible() and p.inner_text("#proof-h") == "Check a sample pack"
    note = p.inner_text("#proof")
    assert "Your browser checks the pack's signature against its stated key. Nothing else." in note
    assert p.get_attribute("#proof .def a", "href") == "providers.html#sample-packs"
    ok, bad, cors = items["provenance.receipt"], items["provenance.custody"], items["verification.human"]
    assert ok.get_attribute("data-verdict") == "verified", ok.inner_text()
    assert "Signs each delivery with ES256" in ok.inner_text()
    assert [a.inner_text().split("\n")[0].split()[0] for a in ok.locator(".parts a").all()][:3] == ["File", "Proof", "Key"]
    assert bad.get_attribute("data-verdict") == "failed" and "does not match" in bad.inner_text(), bad.inner_text()
    assert cors.get_attribute("data-verdict") == "not checkable"
    assert "Not checked: its host blocks browser reads." in cors.inner_text()
    assert p.locator("#proof .cli").count() == 0  # no command line for a tool that is not published
    # the card's own findings are untouched: levels are reported, never changed
    assert "evidenced" not in p.locator("#proofs li.pv[data-where='verification.human'] code").first.inner_text()
    # a failed or unreadable proof never leaves a "nothing to fix" line standing
    assert "Nothing to fix" not in p.inner_text("#out")
    assert " link link" not in p.inner_text("#main")
    # an evidence link that is not a sample pack: one quiet line after the packs, never fetched
    np = p.locator("#proofs li.np")
    assert np.count() == 1 and np.get_attribute("data-where") == "verification.time"
    assert np.inner_text() == "time · Not a sample pack · not checked here", np.inner_text()
    assert np.locator("a").get_attribute("href") == "https://provider.example/docs/how-we-sign"
    assert p.evaluate("document.querySelector('#proofs').lastElementChild.classList.contains('np')")
    assert not [u for u in p.seen if "how-we-sign" in u], p.seen


def test_check_page_with_no_sample_pack_says_so(browser, base):
    import json
    card = json.loads((SITE.parent / "cards" / "usgs-water.json").read_text())
    p = page(browser)
    p.route("https://**/*", lambda r: r.abort("accessdenied"))
    p.goto(base + "/check.html")
    p.click("#go")
    assert "Paste a card first" in p.inner_text("#out") and "JSON Parse" not in p.inner_text("#out")
    for g in ("verification", "provenance"):
        for e in (card.get(g) or {}).values():
            if isinstance(e, dict):
                e.pop("evidence_url", None)
    p.fill("#json", json.dumps(card))
    p.click("#go")
    p.wait_for_selector("#proof:not([hidden])")
    assert p.locator("#nopack").is_visible() and p.inner_text("#nopack") == "No sample packs linked."
    assert p.locator("#proofs li").count() == 0
    out = p.inner_text("#out")
    assert "Nothing to fix in what could be checked." not in out  # a pasted card cannot show its serving headers


def test_check_page_lists_a_non_pack_evidence_link_quietly(browser, base):
    """Sweep nit 15: Copernicus links a custody trace, not a sample pack. It is listed, marked, and not fetched."""
    import json
    card = json.loads((SITE.parent / "cards" / "copernicus-sentinel.json").read_text())
    seen = []
    p = page(browser)
    p.route("https://**/*", lambda r: (seen.append(r.request.url), r.abort("accessdenied")))
    p.goto(base + "/check.html")
    p.fill("#json", json.dumps(card))
    p.click("#go")
    p.wait_for_selector("#proof:not([hidden])")
    assert p.inner_text("#nopack") == "No sample packs linked." and p.locator("#proofs li.pv").count() == 0
    line = p.locator("#proofs li.np")
    assert line.count() == 1 and line.inner_text().endswith(" · Not a sample pack · not checked here")
    assert line.inner_text().startswith("custody · ") and "trace.dataspace.copernicus.eu" in line.locator("a").get_attribute("href")
    assert line.evaluate("e=>e.getClientRects().length===1&&e.getBoundingClientRect().height<30"), "one quiet line on a phone"
    assert "your server" not in p.inner_text("#proof").lower()
    assert not [u for u in seen if "trace.dataspace" in u], seen
    assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_check_the_proof_fits_a_phone_and_clears(browser, base):
    p, _ = _proof_page(browser, base)
    assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
    over = p.evaluate("[...document.querySelectorAll('#proof *')].filter(e=>e.getBoundingClientRect().right>innerWidth-15.5"
                      "&&e.getClientRects().length).map(e=>e.className||e.tagName)")
    assert not over, over
    p.click("#clear")
    assert not p.locator("#proof").is_visible()


# ---------- screen 3: Free, no key, and "Get it now" from the visitor's browser (src/live.js); providers are faked, no network ----------

USGS_FIX = pathlib.Path(__file__).parent / "fixtures" / "adapters" / "usgs-water"


def usgs_lc(rows, minutes_ago=12):
    """A latest-continuous reply (api.waterdata.usgs.gov OGC API, the shape of fixtures/adapters/usgs-water/latest_continuous_*.json)
    for rows of (site number, parameter code, value), all at Austin, `minutes_ago` minutes old."""
    import datetime as dt
    t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat()
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-97.694, 30.244]},
         "properties": {"monitoring_location_id": "USGS-" + site, "parameter_code": code, "value": v, "time": t}} for site, code, v in rows]})


def usgs_iv(value="76.3", minutes_ago=12, site="08158000"):
    return usgs_lc([] if value is None else [(site, "00060", value)], minutes_ago)


def usgs_location(name="Colorado Rv at Austin, TX", site="08158000"):
    """monitoring-locations/items/USGS-<site>, the recorded reply with its name and id swapped."""
    d = json.loads((USGS_FIX / "monitoring_location.json").read_text())
    d["id"] = d["properties"]["id"] = "USGS-" + site
    d["properties"]["monitoring_location_name"] = name
    return json.dumps(d)


def fake_usgs(p, replies, name="Colorado Rv at Austin, TX"):
    """Serve USGS latest-continuous from `replies` in turn (status, body, headers) and the gauge's monitoring-location
    (its name); returns the list of latest-continuous URLs asked for."""
    seen = []
    hd0 = {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-expose-headers": "Retry-After"}

    def h(route):
        url = route.request.url
        if "/monitoring-locations/" in url:
            return route.fulfill(status=200, body=usgs_location(name, url.split("USGS-")[1].split("?")[0]), headers=hd0)
        seen.append(url)
        st, body, hd = replies[min(len(seen), len(replies)) - 1]
        route.fulfill(status=st, body=body, headers={**hd0, **hd})
    p.route("**/api.waterdata.usgs.gov/ogcapi/**", h)
    return seen


def try_(p, svc, q):
    """Screen 3: pick a free service (its capsule; a route that is not one of the six examples, by IRLTry.use, as a
    ?svc= link does), ask it this question, Go."""
    if p.locator(f'#svc .sv[data-id="{svc}"]').count():
        p.click(f'#svc .sv[data-id="{svc}"]')
    else:
        assert p.evaluate("id=>IRLTry.use(id)", svc)
    p.fill("#q", q)
    p.click("#ask button")


def finder(browser, base, q, w=390, h=844):
    """Find data with an ask in its address; no request leaves for a provider (nothing is run until Enter or a tap)."""
    from urllib.parse import quote
    p = page(browser, w, h)
    p.route("https://**/*", lambda r: r.abort())
    p.goto(base + "/find.html?q=" + quote(q))
    p.wait_for_function("document.querySelector('#grid.scored')||document.querySelector('#notes .stop')", timeout=5000)
    return p



def test_screen_three_is_services_a_field_and_one_line(browser, base):
    """The owner (2026-10-30): screen 3 is free services to pick from, a field, Go. Heading, capsules, field, one quiet line."""
    p = landing(browser, base, q="?still=1#try")
    kids = p.evaluate("[...document.querySelectorAll('#try > *')].filter(e=>e.tagName!=='STYLE'&&e.tagName!=='SCRIPT'&&!e.classList.contains('sr')&&getComputedStyle(e).display!=='none').map(e=>e.id||e.className)")
    assert kids[:4] == ["h3", "svc", "ask", "hint"], kids
    ids = p.evaluate("[...document.querySelectorAll('#svc .sv')].map(b=>b.dataset.id)")
    assert len(ids) == 6 and p.evaluate("ids=>ids.every(i=>IRLLive.route(i)&&IRL3.card(i)&&IRL3.card(i).access.cost==='free')", ids), ids  # each really runs in a browser
    assert p.get_attribute("#svc .sv:first-child", "aria-pressed") == "true" and p.input_value("#q") == p.evaluate("IRLLive.services[0].q")
    # before any pick or typing: three asks that work, one tap each (the owner, 2026-10-30: 'no idea what I can search')
    assert p.inner_text("#hint").startswith("Try:") and p.locator("#hint .eg").count() == 3
    assert p.get_attribute("#q", "placeholder") == p.evaluate("IRLLive.services[0].q")
    assert p.locator("#lv").count() == 0  # nothing is fetched until Go
    p.click('#svc .sv[data-id="noaa-tides"]')
    assert p.inner_text("#hint") == "Covers US coasts. Go asks NOAA Tides & Currents straight from your browser."


def test_picking_a_service_fills_its_question_and_focuses_the_field(browser, base):
    p = landing(browser, base, q="?still=1#try")
    p.click('#svc .sv[data-id="noaa-tides"]')
    assert p.input_value("#q") == "Tide at Santa Monica?" and p.evaluate("document.activeElement.id") == "q"
    assert p.get_attribute('#svc .sv[data-id="noaa-tides"]', "aria-pressed") == "true"
    assert p.evaluate("[...document.querySelectorAll('#svc .sv[aria-pressed=true]')].length") == 1
    assert "NOAA Tides" in p.inner_text("#hint")


def test_a_question_for_another_service_goes_to_it(browser, base):
    """Weather typed while USGS rivers is picked: NWS answers, and its capsule lights."""
    p = landing(browser, base, q="?still=1#try")
    p.route("**/api.weather.gov/**", lambda r: r.abort())
    p.fill("#q", "weather in Denver")
    p.click("#ask button")
    p.wait_for_selector("#lv .lvm, #lv .lvs")
    assert p.get_attribute('#svc .sv[data-id="nws"]', "aria-pressed") == "true"


def test_asks_about_a_person_are_refused_on_screen_three(browser, base):
    p = landing(browser, base, q="?still=1#try")
    sent = []
    p.on("request", lambda r: sent.append(r.url) if "usgs" in r.url else None)
    p.fill("#q", "where is my neighbour right now")
    p.click("#ask button")
    assert "won’t route this" in p.inner_text("#lv") and not sent






def test_get_it_now_renders_value_age_and_chip(browser, base):
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(200, usgs_iv(), {})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .ans")
    assert p.inner_text("#lv .you") == "river, Texas"
    assert p.evaluate("getComputedStyle(document.querySelector('#lv .you')).backgroundColor") == "rgb(0, 122, 255)"
    ans = p.inner_text("#lv .ans")
    assert ans == "Right now the Colorado River at Austin, TX is flowing at 76.3 ft³/s, measured 12 min ago.", ans  # a reply, not a data row: the station name spelled out, the figure bold
    assert p.inner_text("#lv .ans b") == "76.3 ft³/s"
    assert "12 min ago" in p.inner_text("#lvc") and "USGS" in p.inner_text("#lvc")
    assert p.inner_text("#lv .via") == "Fetched by your browser, straight from USGS."
    assert len(seen) == 1 and "bbox=" in seen[0] and "latest-continuous" in seen[0] and "river" not in seen[0].lower().split("?")[1], seen  # a map box, never the text
    p.click("#lvc")
    p.wait_for_timeout(600)
    assert p.locator("#lvp li").count() == 8 and p.get_attribute("#lvp", "inert") is None
    assert p.locator("#lvp .px, #lvp small, #lvp .age").count() == 0 and p.locator("#lvp li i svg").count() == 8
    assert p.locator("#lvp li").nth(4).get_attribute("aria-label") == "Known timestamp: Reported (a time is stated, not attested)"
    assert p.evaluate("document.activeElement.classList.contains('pxb')")
    p.click("#lvp .pxb")
    p.wait_for_timeout(500)
    assert p.get_attribute("#lvc", "aria-expanded") == "false"


def test_429_says_busy_and_blocks_retry_until_retry_after(browser, base):
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(429, '{"error":"busy"}', {"Retry-After": "30"})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .lvm")
    assert p.inner_text("#lv .lvm") == "USGS is busy." and p.inner_text("#again") == "Try again in 30 s"
    assert p.is_disabled("#again")
    p.click("#ask button")  # ask again: still blocked, no second request
    p.wait_for_selector("#lv .lvm")
    assert "USGS is busy." in p.inner_text("#lv .lvm") and len(seen) == 1, seen
    assert p.evaluate("IRLLive.blocked('usgs-water')") > 20


def test_retry_after_passes_then_one_tap_asks_once(browser, base):
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(429, "{}", {"Retry-After": "2"}), (200, usgs_iv(), {})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .lvm")
    assert p.is_disabled("#again")
    p.wait_for_timeout(2600)
    assert len(seen) == 1  # never retries by itself
    assert not p.is_disabled("#again") and p.inner_text("#again") == "Try again"
    p.click("#again")
    p.wait_for_selector("#lv .ans", timeout=6000)
    assert len(seen) == 2 and "76.3 ft³/s" in p.inner_text("#lv .ans")


def test_a_fresh_reading_is_reused_not_fetched_again(browser, base):
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(200, usgs_iv(minutes_ago=3), {})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .ans")
    p.click("#ask button")
    p.wait_for_selector("#lv .ans")
    assert len(seen) == 1 and "76.3 ft³/s" in p.inner_text("#lv .ans"), seen


def test_made_to_order_top_match_shows_its_plan_and_never_orders(browser, base):
    p = finder(browser, base, "500 egocentric clips")
    p.wait_for_selector("#best:not([hidden])")
    plan = p.locator("#best .bp").first.inner_text()
    assert plan.startswith("Made to order · "), plan
    assert p.locator("#brun").count() == 0
    p.wait_for_selector("#bcall .call, #bcall .bq", state="attached", timeout=4000)


@pytest.mark.parametrize("q,reply,want", [
    ("river, Smallvillezz, Illinois", None, "Couldn’t place Smallvillezz."),
    ("river level, US", None, "Add a place."),
    ("river, Texas", "down", "USGS didn’t answer. It may be down."),
    ("river, Texas", "empty", "No USGS gauge reporting near Austin, TX."),
])
def test_live_failures_say_what_happened(browser, base, q, reply, want):
    p = landing(browser, base, q="?still=1#try")
    if reply == "down":
        p.route("**/api.waterdata.usgs.gov/ogcapi/**", lambda r: r.abort())
    else:
        seen = fake_usgs(p, [(200, usgs_iv(value=None), {})])
    try_(p, "usgs-water", q)
    p.wait_for_selector("#lv .lvm")
    assert want in p.inner_text("#lv .lvm"), p.inner_text("#lv .lvm")
    if reply is None:
        assert not seen  # nowhere to look: nothing asked
        if "Smallvillezz" in q:  # a city it can place, in the same state, one tap away
            assert p.inner_text("#sug") == "river, Chicago, Illinois"
            p.click("#sug")
            assert p.input_value("#q") == "river, Chicago, Illinois" and p.locator("#lv").count() == 1


# ---------- sweep 2026-10-29 ----------

def usgs_iv_both(flow="210", stage="4.12", with_stage=True):
    """One USGS site reporting flow (00060) and, optionally, gage height (00065)."""
    return usgs_lc([("08158000", "00060", flow)] + ([("08158000", "00065", stage)] if with_stage else []), minutes_ago=10)


@pytest.mark.parametrize("q,body,want", [
    ("river level, Austin, Texas", usgs_iv_both(), "The gauge on the Colorado River at Austin, TX reads 4.12 ft (gage height), measured N min ago."),
    ("river stage in Austin, Texas", usgs_iv_both(), "The gauge on the Colorado River at Austin, TX reads 4.12 ft (gage height), measured N min ago."),
    ("river level, Austin, Texas", usgs_iv_both(with_stage=False), "Right now the Colorado River at Austin, TX is flowing at 210 ft³/s (this gauge reports flow, not level), measured N min ago."),
    ("river, Austin, Texas", usgs_iv_both(), "Right now the Colorado River at Austin, TX is flowing at 210 ft³/s, measured N min ago."),
])
def test_usgs_level_ask_shows_gage_height_when_the_site_has_it(browser, base, q, body, want):
    """Sweep: 'river level in Austin' answered with flow. A level/height/stage ask shows the same gauge's gage height
    (00065, ft) when it reports one; otherwise its flow, saying the gauge reports flow, not level. The ask never leaves."""
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(200, body, {})])
    try_(p, "usgs-water", q)
    p.wait_for_selector("#lv .ans")
    assert re.sub(r"measured \d+ min ago", "measured N min ago", p.inner_text("#lv .ans")) == want  # the case is built at collection
    assert len(seen) == 1 and "parameter_code=00060,00065" in seen[0] and not any(w in seen[0].split("?")[1].lower() for w in ("level", "stage", "river"))


def test_slow_provider_says_it_is_still_waiting(browser, base):
    p = landing(browser, base, q="?still=1#try")
    p.route("**/api.waterdata.usgs.gov/ogcapi/**", lambda r: None)  # never answers (the page's own 15 s timeout ends it)
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_timeout(3400)
    assert "Still waiting for USGS" in p.inner_text("#lv .lvs")


def test_one_source_is_singular_on_the_finder(browser, base):
    p = page(browser)
    p.goto(base + "/find.html?made=1&free=1")
    p.wait_for_function("document.querySelectorAll('#grid .row').length > 0")
    n = p.locator("#grid .row").count()
    assert p.inner_text("#meta") == (f"{n} provider" if n == 1 else f"{n} providers")
    assert "1 providers" not in p.inner_text("#meta")


def test_agents_copy_prompt_names_this_sites_own_address(browser, base):
    """irlpls.com doesn't resolve yet: the copy prompt points at the page's own origin (the preview now, the domain later)."""
    p = page(browser)
    p.goto(base + "/agents.html")
    assert p.inner_text("#ask") == f"Read {base}/agents.md before you use irlpls for me."
    assert "Read https://irlpls.com/agents.md" in (SITE / "agents.html").read_text()  # without JS: the canonical address


@pytest.mark.parametrize("w,h", [(360, 640), (375, 553), (390, 664), (390, 844), (430, 932)])
def test_screen_one_cards_are_one_height_with_no_blank_band(browser, base, w, h):
    """Sweep: at 390x844 the photo stopped at 260px and left 40-100px of empty card above the chip. Every card is one
    height, its photo fills what the reply leaves, and the chip sits right under it."""
    p = landing(browser, base, w=w, h=h, q="?still=1")
    m = p.evaluate("""[...document.querySelectorAll('#track .chat')].map(c=>{const r=c.getBoundingClientRect(),
        md=c.querySelector('.media').getBoundingClientRect(),ch=c.querySelector('.cite').getBoundingClientRect();return [Math.round(r.height),ch.top-md.bottom,r.bottom-ch.bottom]})""")
    assert len({x[0] for x in m}) == 1, m
    assert all(x[1] <= 12 and x[2] <= 24 for x in m), m


def test_open_tool_line_keeps_the_chip_inside_the_card_on_a_short_phone(browser, base):
    """Sweep: at 375x553 opening the steps on the menu card pushed its chip below the fold. The steps scroll inside."""
    p = landing(browser, base, w=375, h=553, q="?still=1&ex=2")
    p.wait_for_timeout(400)
    before = p.evaluate("document.getElementById('ex2').getBoundingClientRect().height")
    p.click("#ex2 .tl")
    p.wait_for_timeout(300)
    card, chip, scrolls = p.evaluate("""(()=>{const c=document.getElementById('ex2');return [c.getBoundingClientRect(),c.querySelector('.cite').getBoundingClientRect(),
        c.querySelector('.tp').scrollHeight>c.querySelector('.tp').clientHeight]})().map(x=>x.bottom===undefined?x:x.bottom)""")
    assert p.evaluate("document.getElementById('ex2').getBoundingClientRect().height") == before
    assert chip <= card <= 553, (chip, card)
    if scrolls:
        assert "ov" in p.get_attribute("#ex2 .tp", "class")


# ---------- wider "Get it now" coverage (2026-10-28): every provider faked; the typed text never leaves ----------

def _iso(minutes_ago=0, fmt=None):
    import datetime as dt
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    return t.strftime(fmt) if fmt else t.isoformat().replace("+00:00", "Z")


def _airnow_rows():
    import datetime as dt
    # AirNow's reporting-area file: local valid date and hour (MDT = UTC-6), O = observation; the AQI is the highest pollutant
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6, minutes=40)
    d, h = t.strftime("%m/%d/%y"), t.strftime("%H:00")
    return "\r\n".join([
        f"{d}|{d}||MDT|-1|F|Y|Denver-Boulder|CO|39.9003|-105.0420|PM2.5|56|Moderate|No||CDPHE",
        f"{d}|{d}|{h}|MDT|0|O|Y|Denver-Boulder|CO|39.9003|-105.0420|OZONE|50|Good|No||CDPHE",
        f"{d}|{d}|{h}|MDT|0|O|N|Denver-Boulder|CO|39.9003|-105.0420|PM2.5|31|Good|No||CDPHE",
        f"{d}|{d}|{h}|MDT|0|O|Y|Aberdeen|SD|45.4680|-98.4940|PM2.5|11|Good|No||SD DANR",
    ])


def _fmi_xml():
    t = _iso(10, "%Y-%m-%dT%H:%M:00Z")
    el = lambda pos, k, v: (f'<wfs:member><BsWfs:BsWfsElement><BsWfs:Location><gml:Point><gml:pos>{pos} </gml:pos></gml:Point></BsWfs:Location>'
                            f'<BsWfs:Time>{t}</BsWfs:Time><BsWfs:ParameterName>{k}</BsWfs:ParameterName><BsWfs:ParameterValue>{v}</BsWfs:ParameterValue></BsWfs:BsWfsElement></wfs:member>')
    return ('<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" '
            'xmlns:BsWfs="http://xml.fmi.fi/schema/wfs/2.0">' + el("60.17 24.95", "t2m", "8.4") + el("60.17 24.95", "ws_10min", "4.6")
            + el("60.33 24.97", "t2m", "NaN") + "</wfs:FeatureCollection>")


def _live_cases():
    import json
    j = json.dumps
    import time
    quake = lambda mag, place, mins: {"type": "Feature", "properties": {"mag": mag, "place": place, "time": int(time.time() * 1000) - mins * 60000}, "geometry": {"type": "Point", "coordinates": [-150, 61, 10]}}
    return [
        ("tide at Santa Monica, California", "noaa-tides", "tide",
         {"**/mdapi/prod/webapi/stations.json*": (j({"stations": [{"id": "9410840", "name": "Santa Monica", "state": "CA", "lat": 34.0083, "lng": -118.5, "greatlakes": False},
                                                                  {"id": "9087044", "name": "Calumet Harbor", "state": "IL", "lat": 41.73, "lng": -87.54, "greatlakes": True}]}), {}),
          "**/api/prod/datagetter*": (j({"metadata": {"id": "9410840"}, "data": [{"t": _iso(6, "%Y-%m-%d %H:%M"), "v": "1.240", "s": "0.3", "f": "0,0,0,0", "q": "p"}]}), {})},
         ("The water at Santa Monica, CA is 1.24 m above mean lower low water, measured ", " min ago."), None),  # metres, as the library (noaa_tides adapter)
        ("earthquakes near Anchorage, Alaska today", "usgs-earthquakes", "earthquake",
         {"**/fdsnws/event/1/query*": (j({"metadata": {"generated": 0}, "features": [quake(2.1, "10 km N of Willow, Alaska", 30), quake(3.5, "27 km E of Point Possession, Alaska", 80)]}), {})},
         "Yes, 2 earthquakes within 250 km of Anchorage, AK in the past 24 h; the largest was M3.5, 27 km E of Point Possession, Alaska, 80 min ago.", None),
        ("earthquakes near Anchorage, Alaska today", "emsc-seismicportal", "earthquake",
         {"**/www.seismicportal.eu/**": (j({"type": "FeatureCollection", "features": []}), {})},
         "No earthquakes recorded within 250 km of Anchorage, AK in the past 24 h.", None),
        ("earthquakes in New Zealand today", "geonet-nz", "earthquake",
         {"**/api.geonet.org.nz/**": (j({"features": [
             {"properties": {"magnitude": 3.61, "locality": "5 km south-west of Hawera", "time": _iso(60)}, "geometry": {"coordinates": [174.25, -39.62]}},
             {"properties": {"magnitude": 5.9, "locality": "far away", "time": _iso(90)}, "geometry": {"coordinates": [166.0, -48.0]}},
             {"properties": {"magnitude": 4.4, "locality": "too old", "time": _iso(60 * 30)}, "geometry": {"coordinates": [174.7, -41.2]}},
             {"properties": {"magnitude": 2.2, "locality": "45 km north of French Pass", "time": _iso(60 * 40)}, "geometry": {"coordinates": [174.0, -40.5]}}]}), {})},
         "Yes, 1 earthquake within 250 km of Wellington in the past 24 h: M3.6, 5 km south-west of Hawera, 60 min ago.", None),
        ("river level in Paris, France", "hubeau-hydrometrie", "river",
         {"**/referentiel/stations*": (j({"data": [{"code_station": "F700000103", "libelle_station": "La Seine à Paris - Austerlitz - station limni", "latitude_station": 48.844, "longitude_station": 2.366},
                                                   {"code_station": "F490000104", "libelle_station": "La Seine à Alfortville", "latitude_station": 48.78, "longitude_station": 2.418}]}), {}),
          "**/observations_tr*": (j({"data": [{"code_station": "F490000104", "date_obs": _iso(5), "resultat_obs": 603.0},
                                              {"code_station": "F700000103", "date_obs": _iso(20), "resultat_obs": 915.0}]}), {})},
         "The water height at La Seine à Paris - Austerlitz is 0.92 m, measured 20 min ago.", "20 min ago"),
        ("river level in Vienna, Austria", "pegelonline", "river",
         {"**/pegelonline.wsv.de/**": (j({"number": "42013", "longname": "KORNEUBURG", "water": {"longname": "DONAU"},
                                          "timeseries": [{"shortname": "W", "unit": "cm", "currentMeasurement": {"timestamp": _iso(15), "value": 204.0}}]}), {})},
         "The water level at Korneuburg (Donau), upstream of Vienna reads 204 cm on the gauge, measured 15 min ago.", "15 min ago"),
        ("air quality in Berlin, Germany", "sensor-community", "quality",
         {"**/data.sensor.community/**": (j([{"sensor": {"id": i}, "timestamp": _iso(2, "%Y-%m-%d %H:%M:%S"), "sensordatavalues": [{"value_type": "P1", "value": "20"}, {"value_type": "P2", "value": v}]}
                                             for i, v in ((1, "4.0"), (2, "6.6"), (3, "30.2"))] + [{"sensor": {"id": 9}, "timestamp": _iso(2, "%Y-%m-%d %H:%M:%S"), "sensordatavalues": [{"value_type": "temperature", "value": "12"}]}]), {})},
         "PM2.5 within 5 km of Berlin is 6.6 µg/m³, the median of 3 citizen sensors, measured 2 min ago.", "2 min ago"),
        ("air quality in Denver, Colorado", "airnow", "quality",
         {"**/files.airnowtech.org/**": (_airnow_rows(), {"content-type": "text/plain"})},
         ("Air quality in Denver-Boulder, CO is good: AQI 50 (ozone), observed ", " ago. The station is 18 km from Denver, CO."), None),
        ("traffic camera in London", "tfl-jamcams", "camera",
         {"**/api.tfl.gov.uk/**": (j({"places": [{"commonName": "Charing Cross Rd/Cranbourne St", "lat": 51.5113, "lon": -0.1283, "additionalProperties": [
             {"key": "available", "value": "true"}, {"key": "imageUrl", "value": "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00001.07550.jpg"}, {"key": "view", "value": "North"}]},
             {"commonName": "Off camera", "lat": 51.5101, "lon": -0.1301, "additionalProperties": [{"key": "available", "value": "false"}]}]}), {}),
          "**/jamcams.tfl.gov.uk/**": ("", {"content-type": "image/jpeg", "last-modified": _iso(4, "%a, %d %b %Y %H:%M:%S GMT")})},
         "The latest still from the Charing Cross Rd/Cranbourne St camera, taken 4 min ago.", "4 min ago"),
        ("weather in Helsinki, Finland", "fmi-open-data", "helsinki",
         {"**/opendata.fmi.fi/**": (_fmi_xml(), {"content-type": "text/xml"})},
         ("It’s 8.4 °C with wind at 4.6 m/s at the FMI weather station in Helsinki, observed ", " ago."), None),
    ]


@pytest.mark.parametrize("i", range(len(_live_cases())), ids=[c[1] + ":" + c[0][:30] for c in _live_cases()])
def test_more_free_providers_answer_in_one_line(browser, base, i):
    """Wider coverage: each new keyless provider answers its everyday ask in one honest line, fetched from the browser.
    Only a map point, radius or station id goes out, never the ask's words. (Cases are built here, so ages are fresh.)"""
    q, cid, word, fakes, want, age = _live_cases()[i]
    p = landing(browser, base, q="?still=1#try")
    seen = []
    def serve(body, hd):
        def h(route):
            seen.append(route.request.url)
            route.fulfill(status=200, body=body, headers={"content-type": "application/json", "access-control-allow-origin": "*", **hd})
        return h
    for pat, (body, hd) in fakes.items():
        p.route(pat, serve(body, hd))
    try_(p, cid, q)
    p.wait_for_selector("#lv .ans", timeout=10000)
    got = p.inner_text("#lv .ans")  # a reply an assistant would write, built only from the reading (IRLLive.reply)
    assert (got.startswith(want[0]) and got.endswith(want[1])) if isinstance(want, tuple) else got == want, got
    assert "Fetched by your browser, straight from" in p.inner_text("#lv .via")
    if age:
        assert age in p.inner_text("#lvc"), p.inner_text("#lvc")
    assert seen and not any(word in u.split("?", 1)[-1].lower() for u in seen if "?" in u), seen
    assert p.evaluate("IRLLive.route(%r).every" % cid) >= 1  # every route says how long a reading stays fresh (the cache)


def test_new_routes_honour_retry_after(browser, base):
    p = landing(browser, base, q="?still=1#try")
    seen = []
    def h(route):
        seen.append(route.request.url)
        route.fulfill(status=429, body="{}", headers={"content-type": "application/json", "access-control-allow-origin": "*",
                                                      "access-control-expose-headers": "Retry-After", "Retry-After": "45"})
    p.route("**/earthquake.usgs.gov/**", h)
    try_(p, "usgs-earthquakes", "earthquakes near Anchorage, Alaska today")
    p.wait_for_selector("#lv .lvm")
    assert p.inner_text("#lv .lvm") == "USGS Earthquakes is busy." and p.inner_text("#again") == "Try again in 45 s"
    p.click("#ask button")
    p.wait_for_selector("#lv .lvm")
    assert len(seen) == 1, seen



def test_usgs_ignores_stopped_series_and_names_the_gauge_once(browser, base):
    """latest-continuous keeps a series' last value even when the gauge stopped (a 2001 reading, live 2026-10-28): a reading
    older than 3 days is not "now". The gauge's name comes from monitoring-locations once, then from this browser."""
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(200, usgs_lc([("08158000", "00060", "999")], minutes_ago=4 * 24 * 60), {})])
    try_(p, "usgs-water", "river, Texas")
    p.wait_for_selector("#lv .lvm")
    assert "No USGS gauge reporting near Austin, TX." in p.inner_text("#lv .lvm") and len(seen) == 1
    names = []
    p.on("request", lambda r: names.append(r.url) if "/monitoring-locations/" in r.url else None)
    seen = fake_usgs(p, [(200, usgs_iv(), {})])
    for q in ("river, Austin, Texas", "river level, Austin, Texas"):
        try_(p, "usgs-water", q)
        p.wait_for_selector("#lv .ans", timeout=8000)
        assert "Colorado River at Austin, TX" in p.inner_text("#lv .ans")
    assert len(seen) == 2 and len(names) == 1, (seen, names)


# ---------- full pass 2026-10-29: site, vocabulary, Get it now ----------

def _sc(values):
    """A Sensor.Community area answer: one SDS011 per value (PM2.5 = P2)."""
    import json
    t = _iso(2, "%Y-%m-%d %H:%M:%S")
    return json.dumps([{"timestamp": t, "sensor": {"id": 100 + k}, "sensordatavalues": [{"value_type": "P2", "value": str(v)}]} for k, v in enumerate(values)])


def _route_json(p, pat, body, seen=None, ctype="application/json"):
    def h(route):
        if seen is not None:
            seen.append(route.request.url)
        route.fulfill(status=200, body=body, headers={"content-type": ctype, "access-control-allow-origin": "*"})
    p.route(pat, h)


def test_go_asks_once_and_typing_asks_nothing(browser, base):
    """Typing a question fetches nothing; Go (or Enter) asks the picked service once."""
    p = landing(browser, base, q="?still=1#try")
    seen = fake_usgs(p, [(200, usgs_iv(), {})])
    assert p.inner_text("#ask button").strip() == "Go"
    p.fill("#q", "river, Texas")
    p.wait_for_timeout(600)
    assert not seen and p.locator("#lv").count() == 0
    p.press("#q", "Enter")
    p.wait_for_selector("#lv .ans")
    assert "76.3 ft³/s" in p.inner_text("#lv .ans") and "USGS" in p.inner_text("#lvc") and len(seen) == 1


def test_finder_shows_the_readiness_line_for_the_top_match(browser, base):
    p = page(browser)
    p.route("https://**/*", lambda r: r.abort())
    p.route("**/catalog.json", lambda r: r.fulfill(status=200, headers={"content-type": "application/json"},
            body='{"providers": {"kled": {"readiness": {"level": "contract", "line": "Needs a sales or approval step first."}}}}'))
    p.goto(base + "/find.html?q=500%20egocentric%20clips")
    p.wait_for_selector("#brdy:not([hidden])", timeout=4000)
    assert p.inner_text("#brdy") == "Needs a sales or approval step first."
    assert "No ready call for this provider yet." in p.inner_text("#bcall")


def test_finder_shows_the_call_an_agent_would_make(browser, base):
    """The owner (2026-10-30): a top match the browser can't run shows the real prepared call (from the adapter, via
    catalog.json), filled with the ask, the key only named and where to get it. Nothing is sent to the provider."""
    p = page(browser)
    sent = []
    p.on("request", lambda r: sent.append(r.url))
    p.route("https://**/*", lambda r: r.abort())
    p.goto(base + "/find.html?q=" + "storefront%20photo%20of%2050%20Elm%20St%2C%20Burlington%2C%20VT")
    p.wait_for_selector("#bcall .call", state="attached", timeout=5000)
    # first the uniform irlpls call (the product), then, folded, what irlpls sends ProxyPics
    assert "irlpls_request(" in p.inner_text("#bcall .ircp-ask") and "storefront photo of 50 Elm St" in p.inner_text("#bcall .ircp-ask")
    open_provider_call(p)
    assert p.inner_text("#best .bn") == "ProxyPics"
    call = p.inner_text("#bcall .call")
    assert call.startswith("POST https://api.proxypics.com/api/v3/photo-requests") and "x-api-key: {PROXYPICS_API_KEY}" in call, call
    assert '"address": "50 Elm St, Burlington, VT"' in call and "storefront photo of 50 Elm St" in call, call
    t = p.inner_text("#bcall")
    assert "PROXYPICS_API_KEY: get it at app.proxypics.com/profile" in t and "Needs your approval" in t and "Nothing is sent from this page." in t, t
    assert p.inner_text("#brdy")  # how close to "just say yes"
    assert not [u for u in sent if "proxypics.com" in u], sent


def test_ready_calls_come_from_the_adapters():
    """catalog.json carries each provider's ready call, built by build.py from the adapter's own prepare(): the ask's point,
    words and place as {LAT} {LON} {NEED} {PLACE}, credentials only named."""
    import re, sys
    sys.path.insert(0, str(SITE.parent))
    import build
    cards = [json.loads(x.read_text()) for x in sorted((SITE.parent / "cards").glob("*.json"))]
    rc = build.ready_calls(cards)
    assert rc["nws"]["url"] == "https://api.weather.gov/points/{LAT},{LON}" and rc["nws"]["auth"] == {"kind": "none"}
    assert rc["proxypics"]["auth"]["key_name"] == "PROXYPICS_API_KEY" and rc["proxypics"]["body"]["additional_notes"] == "{NEED}"
    assert rc["usgs-water"].get("example") == "Austin, TX"  # a box around the point: shown for one named place
    for cid, c in rc.items():
        for k, v in (c.get("headers") or {}).items():
            if re.search(r"key|token|secret|authorization|signature", k, re.I):
                assert "{" in v, (cid, k, v)  # never a real credential
    cat = json.loads((SITE / "catalog.json").read_text())
    assert cat["providers"]["proxypics"]["ready_call"]["url"] == rc["proxypics"]["url"]


def test_delhi_air_says_there_is_no_free_live_source_and_the_plan(browser, base):
    """B1: the Try chip "air, Delhi" never ends at "Can't place Delhi". No sensor reports there: say so, with the plan."""
    p = landing(browser, base, q="?still=1#try")
    seen = []
    _route_json(p, "**/data.sensor.community/**", "[]", seen)
    try_(p, "sensor-community", "air, Delhi")
    p.wait_for_selector("#lv .lvm")
    t = p.inner_text("#lv .lvm")
    assert "Can’t place" not in t and "No free live source for air near Delhi." in t and "OpenAQ" in t, t
    assert seen and "28.61,77.21" in seen[0]


def test_a_saturated_sensor_is_never_the_answer_and_airnow_answers_in_the_us(browser, base):
    """B3: one SDS011 at its 999.9 ceiling read as 1000 µg/m³. It is dropped; in the US, AirNow answers instead, and says why."""
    p = landing(browser, base, q="?still=1#try")
    _route_json(p, "**/data.sensor.community/**", _sc([999.9]))
    an = []
    p.route("**/files.airnowtech.org/**", lambda r: (an.append(1), r.fulfill(status=200, headers={"content-type": "text/plain", "access-control-allow-origin": "*"},
            body="|".join(["x", __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%m/%d/%y"), "00:00", "UTC", "", "O", "", "Portland", "OR", "45.52", "-122.68", "PM2.5", "52", "Moderate", ""]) + "\n"))[1])
    try_(p, "sensor-community", "air quality Portland, OR")
    p.wait_for_selector("#lv .lvm, #lv .ans", timeout=8000)
    t = p.inner_text("#lv")
    assert "999" not in t and "1000" not in t, t


def test_air_median_drops_ceilings_and_flags_lone_extremes(browser, base):
    p = landing(browser, base, q="?still=1#try")
    f = "(xs)=>{const r=IRLLive.airMedian(xs.map((v,i)=>({v:v,t:'2026-10-29T00:0'+i+':00Z'})),{name:'X'});return r&&[r.value,r.what,r.unconfirmed]}"
    assert p.evaluate(f, [999.9]) is None
    v, what, unc = p.evaluate(f, [300])
    assert v == "300 µg/m³" and unc and "unconfirmed" in what
    v, what, unc = p.evaluate(f, [5, 6, 7, 400])
    assert v == "6 µg/m³" and "1 outlier left out" in what and not unc


@pytest.mark.parametrize("q,tap,via,host", [
    ("earthquakes near Wellington, New Zealand", "usgs-earthquakes", "GeoNet", "**/api.geonet.org.nz/**"),
    ("earthquakes near Athens, Greece", "usgs-earthquakes", "EMSC", "**/www.seismicportal.eu/**"),
])
def test_quakes_go_to_the_regional_catalogue(browser, base, q, tap, via, host):
    """B4: USGS misses small quakes in New Zealand and Europe; GeoNet and EMSC answer there, and the answer says so."""
    p = landing(browser, base, q="?still=1#try")
    usgs = []
    _route_json(p, "**/earthquake.usgs.gov/**", '{"features":[]}', usgs)
    _route_json(p, host, '{"features":[]}')
    try_(p, tap, q)
    p.wait_for_selector("#lv .ans", timeout=8000)
    assert via in p.inner_text("#lvc") and not usgs
    assert "answered, not USGS" in p.inner_text("#lv .via")
    assert "checked" in p.inner_text("#lvc")  # an empty catalogue: when it was checked, never a quake's age
    assert " at ," not in p.inner_text("#ann") and " at within" not in p.inner_text("#ann")


def test_gulf_of_finland_is_a_place(browser, base):
    """B2: the agents manual's ship ask read "Gulf" as the place."""
    p = landing(browser, base, q="?still=1#try")
    seen = []
    _route_json(p, "**/meri.digitraffic.fi/**", '{"features":[{"properties":{"timestampExternal":%d}}]}' % int(__import__("time").time() * 1000), seen)
    try_(p, "digitraffic-marine-ais", "ships in the Gulf of Finland")
    p.wait_for_selector("#lv .ans")
    assert "1 ship" in p.inner_text("#lv .ans") and "Gulf of Finland" in p.inner_text("#lv .ans")
    assert "radius=60" in seen[0]


def test_free_live_top_matches_without_a_browser_route_show_their_call(browser, base):
    """B5: a free live provider the browser can't read (no CORS, or no capture time) never offers to run here."""
    for q, cid in (("waves off San Francisco, CA", "ndbc-buoys"), ("highway camera Seattle, WA", "wsdot-cameras")):
        p = finder(browser, base, q)
        ids = p.evaluate("[...document.querySelectorAll('#grid .row')].map(a=>a.dataset.id)")
        if ids and ids[0] == cid:
            assert p.locator("#brun").count() == 0
            p.wait_for_selector("#bcall .call, #bcall .bq", state="attached", timeout=4000)


def test_price_words_are_the_same_on_the_landing_and_the_finder(browser, base):
    """W6: one helper (src/labels.js) for both."""
    p = landing(browser, base, q="?still=1#try")
    got = p.evaluate("IRL3.cards.map(c=>[c.id,IRLLabels.priceText(c)])")
    f = page(browser)
    f.goto(base + "/find.html")
    f.wait_for_selector("#grid .row")
    rows = dict(f.evaluate("[...document.querySelectorAll('#grid .row')].map(a=>[a.dataset.id,a.querySelector('.sub').textContent.split(' · ').pop()])"))
    assert got and all(rows[i] == t for i, t in got if i in rows), [(i, t, rows.get(i)) for i, t in got if rows.get(i) != t][:5]
    assert not [t for _, t in got if t in ("Paid", "Free, key")]


def test_levels_never_say_none(browser, base):
    p = page(browser)
    for path in ("/c/windy-webcams.html", "/compare.html?ids=usgs-water,nws,windy-webcams", "/find.html"):
        p.goto(base + path)
        p.wait_for_timeout(500)
        t = p.inner_text("main")
        assert "None" not in t.split("Sources")[0] and "of 7" not in t, path
    p.goto(base + "/compare.html?ids=usgs-water,nws")
    p.wait_for_selector("#cmp table")
    words = p.evaluate("[...document.querySelectorAll('#cmp tbody.g td')].map(td=>td.textContent)")
    assert len(words) == 16 and set(words) <= {"Evidenced", "Claimed", "Reported", "Not stated"}, words


def test_card_rate_limit_is_one_line(browser, base):
    p = page(browser)
    p.goto(base + "/c/tfl-jamcams.html")
    t = p.inner_text(".cardv")
    assert t.count("Rate limit") == 1 and "unknown" not in p.inner_text(".cardv .rl") and "500 requests per minute" in t
    p.goto(base + "/c/copernicus-sentinel.html")
    assert p.inner_text(".cardv").count("Rate limit") == 1 and "60 requests per minute, per IP" in p.inner_text(".cardv .rl")


def test_opened_steps_never_clip_on_a_small_phone(browser, base):
    """B6: at 375x553 the steps were cut mid-line and the photo became a sliver. Opened, every step shows whole, and the
    photo shows at a useful size or not at all."""
    p = landing(browser, base, 375, 553, q="?still=1", has_touch=True, is_mobile=True)
    for i in range(5):
        p.evaluate(f"document.getElementById('track').scrollTo({{left:document.getElementById('ex{i}').offsetLeft-document.getElementById('ex0').offsetLeft}})")
        p.wait_for_timeout(500)
        p.locator(f"#ex{i} .tl").click()
        p.wait_for_timeout(300)
        tp = p.evaluate(f"(()=>{{const t=document.querySelector('#ex{i} .tp');return [t.clientHeight,t.scrollHeight]}})()")
        assert tp[1] <= tp[0] + 2, (i, tp)
        mh = p.evaluate(f"(()=>{{const m=document.querySelector('#ex{i} .att .media');return m?m.getBoundingClientRect().height:0}})()")
        assert mh == 0 or mh >= 56, (i, mh)
        p.locator(f"#ex{i} .tl").click()


def test_check_card_brings_its_result_into_view_on_a_short_phone(browser, base):
    """B7: at 375x553 the result rendered below the fold, so the tap seemed to do nothing."""
    import json
    p = browser.new_page(viewport={"width": 375, "height": 553})
    p.route("https://**/*", lambda r: r.abort())
    p.goto(base + "/check.html")
    p.fill("#json", (SITE.parent / "cards" / "usgs-water.json").read_text())
    p.click("#go")
    p.wait_for_selector("#live .golive")   # the going-live panel is the first result now (it sits above the findings)
    p.wait_for_timeout(900)
    top = p.evaluate("(document.querySelector('#live:not([hidden])')||document.querySelector('#out').firstElementChild).getBoundingClientRect().top")
    assert 0 <= top < 553 - 96, top
    p.fill("#json", "")
    p.click("#go")
    assert "Paste a card first" in p.inner_text("#out") and "Fix these" not in p.inner_text("#out") and "no overall score" not in p.inner_text("#out")


def test_preview_server_serves_the_404_page():
    """B8: tools/preview.py answers an unknown path with site/404.html, status 404."""
    import socket, subprocess, sys, time, urllib.request, urllib.error
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    pr = subprocess.Popen([sys.executable, str(SITE.parent / "tools" / "preview.py"), "127.0.0.1", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=1); break
            except Exception:
                time.sleep(0.1)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope/missing.html", timeout=3)
        assert e.value.code == 404 and "Not found · irlpls" in e.value.read().decode()
    finally:
        pr.terminate()


def test_finder_runs_the_top_match_live_or_shows_its_call(browser, base):
    """The owner: type anything; a top match the browser can run is run, and its answer shows inline."""
    p = page(browser)
    p.goto(base + "/find.html?q=storefront%20photos%20in%20Lisbon%20with%20proof%20of%20when%20and%20where")
    p.wait_for_selector("#best:not([hidden])")
    t = p.inner_text("#best")
    first = p.evaluate("document.querySelector('#grid .row .nm').textContent")
    assert "BEST MATCH" in t.upper() and first in t and "See the plan" in t
    p = page(browser)
    seen = fake_usgs(p, [(200, usgs_iv(), {})])
    p.goto(base + "/find.html?q=river%20level%20in%20Austin%2C%20Texas")
    p.wait_for_selector("#brun")
    assert p.inner_text("#brun") == "Ask USGS live" and not seen  # a link opens it; nothing runs until asked
    p.press("#q", "Enter")
    p.wait_for_selector("#blv .lva", timeout=8000)
    assert "Austin" in p.inner_text("#blv .lva") and "Fetched by your browser, straight from USGS." in p.inner_text("#blv") and len(seen) == 1
    assert p.locator("#blv .dots i").count() == 8
    # the title is in colour again (the wordmark's)
    assert "gradient" in p.evaluate("getComputedStyle(document.querySelector('.finder h1')).backgroundImage")


def test_finder_runs_live_once_typing_names_a_place(browser, base):
    p = page(browser)
    seen = fake_usgs(p, [(200, usgs_iv(), {})])
    p.goto(base + "/find.html")
    p.wait_for_selector("#grid .row")
    p.type("#q", "river level in Austin, Texas", delay=15)
    p.wait_for_selector("#blv .lva", timeout=8000)
    assert len(seen) == 1


def test_headline_starts_in_colour_with_one_slow_shimmer_over_the_whole_title(browser, base):
    """The owner (2026-10-30): the terms are in colour from the first frame (no flourish, no settle to ink); one slow,
    constant light sweeps the whole title; reduced motion and ?still=1: static colour, no light."""
    p = landing(browser, base)
    css = lambda s, k: p.evaluate(f"getComputedStyle(document.querySelector('{s}')).{k}")
    bg = css("#h1 .k5", "backgroundImage")
    assert "rgb(5, 150, 105)" in bg and "rgb(16, 20, 24)" not in bg  # emerald, never ink
    for k in ("k3", "k4", "k5"):
        assert css(f"#h1 .{k}", "animationName") == "none"  # in colour at once, no flourish
    assert p.evaluate("getComputedStyle(document.getElementById('h1'),'::after').animationName") == "shine"
    assert p.evaluate("getComputedStyle(document.getElementById('h1'),'::after').animationIterationCount") == "infinite"
    assert p.evaluate("getComputedStyle(document.getElementById('h1'),'::after').animationTimingFunction") == "linear"
    assert float(p.evaluate("getComputedStyle(document.getElementById('h1'),'::after').animationDuration")[:-1]) >= 5
    # the light spans the whole title (every word), not only the coloured terms
    w = p.evaluate("[document.getElementById('h1').getBoundingClientRect().width, parseFloat(getComputedStyle(document.getElementById('h1'),'::after').width)]")
    assert abs(w[0] - w[1]) < 1, w
    for q, kw in (("?still=1", {}), ("", {"reduced_motion": "reduce"})):
        r = landing(browser, base, q=q, **kw)
        assert r.evaluate("getComputedStyle(document.getElementById('h1'),'::after').animationName") == "none"
        assert "rgb(5, 150, 105)" in r.evaluate("getComputedStyle(document.querySelector('#h1 .k5')).backgroundImage")


def test_screen_two_stages_use_the_pane_icons(browser, base):
    p = landing(browser, base, q="?still=1")
    assert p.locator("#chain > li i svg").count() == 8
    d = p.evaluate("[...document.querySelectorAll('#chain > li i svg')].map(s=>s.firstElementChild.outerHTML.match(/ (?:d|cx|x)=\"[^\"]+\"/)[0])")
    assert all(x.strip() in v for x, v in zip(d, p.evaluate("IRL3.PVI"))), d


def test_check_page_card_ownership_matches_python(browser, base):
    """check.html's sameOwner is psl.same_owner: one table, both sides (exact host or www twin, PSL incl. PRIVATE)."""
    from irlpls import psl
    from test_protocol_bindings import OWNER
    p = page(browser)
    p.goto(base + "/check.html")
    rows = [[h, d] for h, d, _ in OWNER]
    got = p.evaluate("rows=>Promise.all(rows.map(([h,d])=>sameOwner(h,d)))", rows)
    assert got == [ok for _, _, ok in OWNER]
    assert got == [psl.same_owner(h, d) for h, d, _ in OWNER]



def test_river_example_is_a_dated_sample_with_committed_media(browser, base):
    """The owner (2026-10-30): screen 1's examples are samples; the river shows a committed USGS frame and its saved reading,
    labelled Sample like the others, and the page asks USGS for nothing."""
    import json
    asked = []
    p = page(browser)
    p.on("request", lambda r: asked.append(r.url) if "usgs" in r.url else None)
    p.goto(base + "/?still=1")
    p.wait_for_timeout(500)
    assert not asked, asked
    img = p.locator("#ex0 .media img")
    assert img.get_attribute("src") == "media/guadalupe-comfort-2026-10-29.jpg"
    assert p.evaluate("(()=>{const i=document.querySelector('#ex0 .media img');return i.complete&&i.naturalWidth>0})()")
    assert p.inner_text("#ex0 .smp") == "Sample" and "Sample answer." in p.get_attribute("#ex0 .cite", "aria-label")
    saved = json.loads((SITE / "media" / "guadalupe-comfort-2026-10-29.json").read_text())
    assert f"about {round(saved['flow']['v'])} ft³/s" in p.inner_text("#ex0 .ans") and f"~{round(saved['normal']['p50'])}" in p.inner_text("#ex0 .ans")


def test_first_card_plays_on_load_and_the_photo_place_is_white(browser, base):
    """The owner (2026-10-30): the first card does not wait for the title; before the photo lands its place is white (no grey)."""
    p = page(browser)
    p.goto(base + "/")
    p.wait_for_timeout(900)
    assert p.evaluate("+getComputedStyle(document.querySelector('#ex0 .tool')).opacity") > .5 and p.inner_text("#ex0 .tt").strip()
    assert p.evaluate("getComputedStyle(document.querySelector('#ex0 .media')).backgroundColor") in ("rgba(0, 0, 0, 0)", "transparent")
    p.wait_for_function("!document.querySelector('#ex0 .att').classList.contains('hide')", timeout=5000)


# ---------- the owner, 2026-10-30: every Go ends visibly; world places; the ready call when nothing runs live ----------

def test_munich_air_answers_live_from_sensor_community(browser, base):
    """'Air quality in Munich' did nothing: Munich now comes from the world place table (src/places.js) and the citizen
    sensors answer, as a sentence. Only a map point leaves the browser."""
    import json as js
    p = landing(browser, base, q="?still=1#try")
    seen = []
    rows = [{"sensor": {"id": i}, "timestamp": _iso(3, "%Y-%m-%d %H:%M:%S"), "sensordatavalues": [{"value_type": "P2", "value": v}]} for i, v in ((1, "8.0"), (2, "10.0"), (3, "12.0"))]
    def h(route):
        seen.append(route.request.url)
        route.fulfill(status=200, body=js.dumps(rows), headers={"content-type": "application/json", "access-control-allow-origin": "*"})
    p.route("**/data.sensor.community/**", h)
    try_(p, "sensor-community", "Air quality in Munich?")
    p.wait_for_selector("#lv .ans", timeout=10000)
    assert p.inner_text("#lv .ans") == "PM2.5 within 5 km of Munich is 10 µg/m³, the median of 3 citizen sensors, measured 3 min ago."
    assert len(seen) == 1 and "area=48.1" in seen[0] and "munich" not in seen[0].lower()


@pytest.mark.parametrize("q,want", [("river level in Smallvillezz, Kansas", "Couldn’t place Smallvillezz."), ("air in Flurbington, Germany", "Couldn’t place Flurbington.")])
def test_an_unknown_place_says_how_to_fix_it(browser, base, q, want):
    """Never nothing: a place nobody can find says so, and how to fix it (add the country), with a suggestion."""
    p = landing(browser, base, q="?still=1#try")
    p.route("**/*", lambda r: r.abort() if not r.request.url.startswith(base) else r.continue_())
    p.fill("#q", q)
    p.click("#ask button")
    p.wait_for_selector("#lv .lvm", timeout=8000)
    t = p.inner_text("#lv .lvm")
    assert t.startswith(want) and "Add the country, e.g. “Munich, Germany”." in t and p.locator("#sug").count() == 1


@pytest.mark.parametrize("q,who", [("photo of the storefront at 50 Elm St, Springfield, IL", "ProxyPics"), ("new satellite image of farm fields near Osaka, Japan", None)])
def test_no_browser_source_shows_the_ready_call_and_never_orders(browser, base, q, who):
    """A place no browser source covers, or an ask that needs an account or an order: the best matching provider and the
    call an agent would make (src/callpane.js, as Find data). Nothing is sent to any provider; the key is only named."""
    p = landing(browser, base, q="?still=1#try")
    out = []
    p.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else (out.append(r.request.url), r.abort()))
    p.fill("#q", q)
    p.click("#ask button")
    open_impl(p)
    t = p.inner_text("#duo")
    assert "Here’s the exact call." in p.inner_text("#lv") and (who is None or who in t)
    # two layers (the owner, 2026-10-30): the uniform irlpls call first, then what irlpls sends the provider, folded
    assert "your agent → irlpls" in t.lower() and "irlpls_request(" in t and "what irlpls sends for you" in t.lower()
    p.click("#duo .ircp-x")
    assert out == [], out
    for k in p.evaluate("[...document.querySelectorAll('#lv pre.call .t-ph')].map(e=>e.textContent)"):
        assert k.startswith("{") and k.endswith("}"), k
    assert p.evaluate("document.documentElement.scrollWidth") <= 390


def test_screen_one_desktop_arrows_sit_on_the_card_edges(browser, base):
    p = landing(browser, base, 1440, 900, q="?still=1&ex=1")
    p.wait_for_timeout(500)
    r = p.evaluate("""(()=>{const c=document.getElementById('ex1').getBoundingClientRect(),a=document.getElementById('prev').getBoundingClientRect(),b=document.getElementById('next').getBoundingClientRect();
      return [a.left+a.width/2-c.left, b.left+b.width/2-c.right, a.top+a.height/2-(c.top+c.height/2), document.getElementById('next').parentElement.id]})()""")
    assert abs(r[0]) <= 2 and abs(r[1]) <= 2 and abs(r[2]) <= 2 and r[3] == "stage", r
    t = landing(browser, base, q="?still=1", has_touch=True, is_mobile=True)
    assert t.evaluate("document.getElementById('next').parentElement.id") == "pager"


def test_footer_is_formal_and_the_same_on_every_page(browser, base):
    want = ["Standard", "Docs", "For providers", "Privacy", "Glossary", "Changelog", "Governance"]
    for path in ("", "find.html", "chain.html", "check.html", "standard.html", "agents.html", "docs.html", "privacy.html"):
        h = (SITE / (path or "index.html")).read_text()
        f = h[h.index('<footer class="site-foot">'):h.index("</footer>")]
        assert re.findall(r'<nav class="fl"[^>]*>(.*?)</nav>', f) and [re.sub("<.*?>", "", a) for a in re.findall(r"<a [^>]*>.*?</a>", re.search(r'<nav class="fl"[^>]*>(.*?)</nav>', f).group(1))] == want, path
        assert "Code Apache-2.0 · Cards CC BY 4.0" in f and "hello@irlpls.com" in f and "© irlpls maintainers" in f, path
        assert "github" not in f.lower() and "!" not in re.sub("<!--.*?-->", "", f), path


def open_impl(p):
    """Screen 3's right side (what irlpls did): on a phone it is folded to one line; unfold it."""
    p.wait_for_selector("#duo .ircp", state="attached", timeout=10000)
    if p.is_hidden("#rb"):
        p.click("#rl")


def open_provider_call(p, root="#bcall"):
    """The call pane's layer 2 (what irlpls sends the provider) is folded to one line; unfold it."""
    x = p.locator(f"{root} .ircp-x").first
    if x.count() and x.get_attribute("aria-expanded") != "true":
        x.click()


def _no_net(p, base, out):
    p.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else (out.append(r.request.url), r.abort()))


def test_air_quality_in_tokyo_shows_the_openaq_call(browser, base):
    """Owner bug (live site, 2026-10-30): 'air quality in Tokyo' showed nothing. No free browser source covers air there,
    so the page shows OpenAQ (official stations, free with a key) and the call an agent would make. Never blank."""
    import json as js
    p = landing(browser, base, q="?still=1#try")
    p.route("**/data.sensor.community/**", lambda r: r.fulfill(status=200, body="[]", headers={"content-type": "application/json", "access-control-allow-origin": "*"}))
    p.fill("#q", "air quality in Tokyo")
    p.press("#q", "Enter")
    open_impl(p)
    t = p.inner_text("#duo")
    assert "No free live source for air near Tokyo." in t and "OpenAQ" in t, t


@pytest.mark.parametrize("q", ["river level in Osaka, Japan", "weather in Nairobi, Kenya", "tide in Lisbon, Portugal",
                               "traffic camera in Madrid, Spain", "ships near Sydney, Australia"])
def test_places_no_browser_source_covers_never_end_blank(browser, base, q):
    """Five places no free browser source covers, across services: each Go ends in a ready call or a plain message."""
    p = landing(browser, base, q="?still=1#try")
    out = []
    _no_net(p, base, out)
    p.fill("#q", q)
    p.click("#ask button")
    p.wait_for_selector("#lv .lvm", timeout=10000)
    p.wait_for_timeout(300)
    t = p.inner_text("#lv .turn")
    assert len(t.strip()) > 20 and "Reading your ask" not in t, t
    assert out == [], out  # no provider was asked: none covers the place


def test_enter_runs_go_like_the_button(browser, base):
    """Enter in the field (the phone keyboard's Go too: the field is in a form) gives the same answer as tapping Go."""
    got = []
    for how in ("enter", "click"):
        p = landing(browser, base, q="?still=1#try")
        fake_usgs(p, [(200, usgs_iv(), {})])
        p.fill("#q", "river, Texas")
        p.press("#q", "Enter") if how == "enter" else p.click("#ask button")
        p.wait_for_selector("#lv .ans")
        got.append(p.inner_text("#lv .ans"))
    assert got[0] == got[1] and p.evaluate("document.getElementById('q').closest('form').id") == "ask"
