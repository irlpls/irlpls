"""Visual QA for the landing page: the floor of quality, checked before anyone else looks.

    uv run --with playwright python tools/qa.py [OUTDIR] [BASE_URL]

The landing is three screens, each one <section>: #s1 (headline + a swipeable row of five example chats), #how (the eight-link
provenance standard) and #try (type anything; matched in the browser). Every screen is rendered on three iPhones (WebKit:
390x709 the area above Safari's bar, 375x553 an SE in Safari, 390x664 with the keyboard-less toolbar) plus one desktop,
and fails on:
  - anything wider than the screen (sideways scroll), or any visible element past the screen's left/right edge
  - text clipped by its own box (overflow hidden without an ellipsis or line clamp) or spilling out of its glass card
  - two blocks overlapping
  - an empty card (a glass panel with no text, or an image that did not load)
  - the sticky header covering the top of a screen reached from a link
  - a tap target (link, button, field, chain link row) smaller than 44px
  - screen 1's chat, its source chip or its call to action below the first screen on a phone (every example); ?ex=N not
    opening example N; the dots not moving to the next example
  - the headline's three lines not each whole on one line (VOICE-1028 #1)
  - a staged example without its faint "Sample" label inside the photo's bottom-right corner, or the river with one; "Sample" in a chip
  - on every example: a source chip whose sheet does not show all eight stages and the card link without inner scrolling,
    anything moving (page, card, headline) while it opens or closes, or a close button that does not return to the chip
  - the one-time swipe nudge not playing (~32px) once the first reply has arrived
  - a blank card on arrival: by 0.8 s the question and its status line must show; by 2.2 s the whole reply
  - the 'Try:' line cut off (it must fit whole); Match not bringing the results into view
  (the next card's peeking edge is clipped by the row on purpose, so cards not in view skip the screen-edge check)
  - a typed query that renders neither results nor an honest empty state; a person-targeting query that is not refused,
    or a family member's place ("my mom's house") that is
  - round 4: a card not yet seen that says "searching" or is part-way through arriving (cards other than the first are
    complete from the start); an open source sheet with an empty band (> 48px) under its last row; screen 2's heading under
    the header when scrolled to; a result name cut off; an ask to check existing media without the plain note; the
    Golden Gate ask not led by Caltrans; the agent line not saying "private preview, not published yet"
  - round 6 (every screen, at rest and while screen 1 plays): screen 2's or 3's first line under 16px from the sticky header; an empty band
    over 120px inside the screen (nothing visible: no text, photo, placeholder or icon); and at 390x844 and 375x667 any of a
    screen's content past the bottom of its own screen (screen 2's closing line falling onto screen 3)
  - a result linking to a Capture Card page that does not exist
  - a boxed button, capsule or link-button whose label (text plus any icon) is off its box's centre by more than 1.5px,
    vertically or horizontally (tools/centre_probe.py; every probe, plus a pass at 390 and 1280 wide)
  - JavaScript errors
Screen 1 is checked at rest (?still=1) for every example and while the first example plays. Screenshots of every check
land in OUTDIR so a person can look too. Exit code 1 on any failure.
"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from centre_probe import CENTRE  # every boxed button's label centred in its box, within 1.5px (shared with qa_pages.py)

OUT = sys.argv[1] if len(sys.argv) > 1 else "qa-shots"
# Preview URL: argv, else $IRLPLS_BASE, else the first line of the gitignored .preview-url at the repo root, else localhost.
_PREVIEW = os.environ.get("IRLPLS_BASE") or (_PU.read_text().strip() if (_PU := pathlib.Path(__file__).resolve().parent.parent / ".preview-url").exists() else "") or "http://localhost:8430/"
BASE = (sys.argv[2] if len(sys.argv) > 2 else _PREVIEW).rstrip("/") + "/"
EXAMPLES = 5

# blocks that must never overlap one another (ancestor/descendant pairs are skipped)
BLOCKS = ("h1, h2, .kick, .lede, .chat, .pager, .cta, .you, .tool, .ans, .media, .cites, .pv li, #chain > li, .lkf .cap, .lkf .std, .ask, .priv,"
          " .tries, .note, .oh, .res li, .all, .msg, .dev")
TAPS = "a[href], button, input, textarea, summary, select"

PROBE = r"""([sel, BLOCKS, TAPS]) => {
  const sec = document.querySelector(sel), hd = document.querySelector('.top'), R = e => e.getBoundingClientRect(), out = [];
  if (!sec) return ['screen ' + sel + ' missing'];
  const vis = e => { const d = e.closest('details:not([open])'); if (d && !e.closest('summary')) return false;  // folded content
    for (let x = e; x && x !== document.documentElement; x = x.parentElement) { const cs = getComputedStyle(x);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false; }
    const r = R(e); return r.width > 0 && r.height > 0; };
  const name = e => (e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''))
    + ' "' + (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 30) + '"';
  const W = innerWidth;
  // inside a sideways-scrolling row that itself sits on the screen: off to the side on purpose (and must not be cut top or bottom)
  const inRow = e => { for (let x = e.parentElement; x && x !== sec; x = x.parentElement) { const cs = getComputedStyle(x);
      if (/(auto|scroll)/.test(cs.overflowX) && x.scrollWidth > x.clientWidth + 1) { const a = R(x), b = R(e);
        return a.left >= -1 && a.right <= W + 1 && b.top >= a.top - 1 && b.bottom <= a.bottom + 1 } } return false };
  // inside a box that scrolls up and down (the source chip's open panel): hidden past its edge on purpose
  const inBox = e => { for (let x = e.parentElement; x && x !== sec; x = x.parentElement) { const cs = getComputedStyle(x);
      if (/(auto|scroll)/.test(cs.overflowY) && x.scrollHeight > x.clientHeight + 1) { const a = R(x), b = R(e);
        return b.top < a.top - 1 || b.bottom > a.bottom + 1 } } return false };
  if (document.documentElement.scrollWidth > W + 1) out.push('sideways scroll: ' + document.documentElement.scrollWidth + ' > ' + W);
  const all = [...sec.querySelectorAll('*')].filter(e => !e.closest('.sr, script, style') && vis(e) && !inBox(e));
  // past the screen edge, clipped by its own box, or spilling out of its glass card
  for (const e of all) {
    const r = R(e), cs = getComputedStyle(e);
    if ((r.right > W + 1 || r.left < -1) && !e.closest('.chat.off') && !inRow(e)) out.push(name(e) + ' past the screen edge (' + Math.round(r.left) + '..' + Math.round(r.right) + ')');
    const hasText = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (hasText && (cs.overflowX !== 'visible' || cs.overflowY !== 'visible')) {
      const meant = cs.textOverflow === 'ellipsis' || cs.webkitLineClamp !== 'none' && cs.webkitLineClamp;
      if (!meant && (e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 2)) out.push(name(e) + ' text clipped by its box');
    }
    const g = e.parentElement && e.parentElement.closest('.glass');
    if (g && sec.contains(g)) { const gr = R(g);
      if (r.left < gr.left - 1 || r.right > gr.right + 1 || r.top < gr.top - 1 || r.bottom > gr.bottom + 1) out.push(name(e) + ' spills out of its card'); }
  }
  // overlap
  const bl = [...sec.querySelectorAll(BLOCKS)].filter(e => vis(e) && !inBox(e));
  for (let i = 0; i < bl.length; i++) for (let j = i + 1; j < bl.length; j++) {
    if (bl[i].contains(bl[j]) || bl[j].contains(bl[i])) continue;
    const a = R(bl[i]), b = R(bl[j]);
    const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left), oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (ox > 1 && oy > 1) out.push('overlap: ' + name(bl[i]) + ' / ' + name(bl[j]));
  }
  // empty cards and broken media
  for (const g of [...sec.querySelectorAll('.glass')].filter(vis))
    if (!g.innerText.trim() && !g.querySelector('img, video')) out.push('empty card: ' + name(g));
  for (const m of [...sec.querySelectorAll('img')].filter(vis)) if (m.complete && !m.naturalWidth) out.push('image did not load: ' + m.getAttribute('src'));
  for (const m of [...sec.querySelectorAll('.media')].filter(vis)) if (!m.querySelector('img, video')) out.push('empty media box');
  // tap targets (the header's too)
  for (const t of [...sec.querySelectorAll(TAPS), ...hd.querySelectorAll(TAPS)].filter(vis)) {
    const r = R(t); if (r.height < 43.5 || r.width < 43.5) out.push('tap target ' + Math.round(r.width) + 'x' + Math.round(r.height) + ': ' + name(t));
  }
  return out;
}"""

# the sticky header must not cover the top of a screen reached from a link (#how from screen 1's call to action, #try from a hash)
UNDER_HEADER = r"""(sel) => {
  const hb = document.querySelector('.top').getBoundingClientRect().bottom, sec = document.querySelector(sel);
  const first = [...sec.children].find(e => !['STYLE', 'SCRIPT'].includes(e.tagName) && e.getBoundingClientRect().height > 0);
  const t = first.getBoundingClientRect().top;
  return t < hb - 1 ? [sel + ' starts under the header (' + Math.round(t) + ' < ' + Math.round(hb) + ')'] : [];
}"""

# round 6: a screen as it shows when scrolled to (links and paging land there): the first line clear of the header (16px),
# no empty band over 120px between the things that show, and (spill) nothing of it past the bottom of its screen. The last
# screen ends on the footer, so the footer counts as part of it. "Ink" is what a person sees: text, a photo or its placeholder,
# an icon, a field. Cards swiped out of view to the side are skipped.
SCREEN = r"""([sel, spill]) => {
  const sec = document.querySelector(sel), hb = document.querySelector('.top').getBoundingClientRect().bottom, R = e => e.getBoundingClientRect(),
    W = innerWidth, H = innerHeight, out = [], foot = document.querySelector('.site-foot'), last = !sec.nextElementSibling;
  const vis = e => { for (let x = e; x && x !== document.documentElement; x = x.parentElement) { const cs = getComputedStyle(x);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false; } const r = R(e); return r.width > 0 && r.height > 0; };
  const ink = e => !e.closest('.sr, script, style, dialog:not([open])') && ([...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
      || /^(img|video|svg|input|textarea)$/i.test(e.tagName) || e.matches('.media, .dots i, .lk i')) && vis(e);
  const side = r => r.right > 1 && r.left < W - 1, own = [...sec.querySelectorAll('*')].filter(ink).map(R).filter(side);
  const els = own.concat(last && foot ? [...foot.querySelectorAll('*')].filter(ink).map(R).filter(side) : []);
  if (!els.length) return [sel + ': nothing shows'];
  const s = R(sec), top = Math.max(hb, s.top), bot = Math.min(H, last && foot ? R(foot).bottom : s.bottom);
  const first = Math.min(...els.map(r => r.top));
  if (sel !== '#s1' && first < hb + 15.5) out.push(sel + ': first line ' + Math.round(first - hb) + 'px from the header (want 16)');
  const iv = els.map(r => [Math.max(top, r.top), Math.min(bot, r.bottom)]).filter(x => x[1] > x[0]).sort((a, b) => a[0] - b[0]);
  let y = top;
  for (const [a, b] of iv) { if (a - y > 120) out.push(sel + ': empty band of ' + Math.round(a - y) + 'px at ' + Math.round(y) + '..' + Math.round(a)); y = Math.max(y, b); }
  if (bot - y > 120) out.push(sel + ': empty band of ' + Math.round(bot - y) + 'px at ' + Math.round(y) + '..' + Math.round(bot) + ' (the screen\'s foot)');
  if (spill) { const low = Math.max(...own.map(r => r.bottom)); if (low > H + 1) out.push(sel + ': runs ' + Math.round(low - H) + 'px past its screen (onto the next)'); }
  return out;
}"""
GO_SCREEN = "(sel) => { document.querySelector(sel).scrollIntoView({block: 'start', behavior: 'instant'}); }"
# round 6: the two phones the visitors used, for the screen checks (spill included)
SCREEN_DEVICES = [("r6-390x844", {"viewport": {"width": 390, "height": 844}}), ("r6-375x667", {"viewport": {"width": 375, "height": 667}})]


# the headline: three lines broken on meaning, each whole on one line inside the column
HEADLINE = r"""() => [...document.querySelectorAll('#h1 .ln')].flatMap((l, i) => { const g = document.createRange(); g.selectNodeContents(l);
  const w = g.getBoundingClientRect().width, tops = new Set([...g.getClientRects()].map(x => Math.round(x.top))).size;
  return w > l.clientWidth + 0.5 || tops !== 1 ? ['headline line ' + (i + 1) + ' not whole (' + Math.round(w) + ' > ' + l.clientWidth + ', ' + tops + ' lines)'] : []; })"""

# a staged example says "Sample" small and faint inside its photo's bottom-right corner; the river is real (no label); no chip says it
SAMPLE = r"""(ex) => { const c = document.getElementById('ex' + ex), s = c.querySelector('.smp'), m = c.querySelector('.media'), out = [];
  if (/Sample/.test(c.querySelector('.chip').innerText)) out.push('"Sample" in the chip');
  if (ex === 0) { if (s) out.push('the river (real) has a Sample label'); return out; }
  if (!s || s.innerText.trim() !== 'Sample') return ['no Sample label in the photo'];
  const a = m.getBoundingClientRect(), b = s.getBoundingClientRect();
  if (b.left < a.left + a.width * 2 / 3 || b.top < a.top + a.height - 30 || b.right > a.right + 1 || b.bottom > a.bottom + 1) out.push('Sample label not in the photo\'s bottom-right corner');
  if (parseFloat(getComputedStyle(s).fontSize) > 11) out.push('Sample label not small');
  return out; }"""

# the open sheet: all eight stages and the card link inside it, no inner scrolling, inside its card
SHEET = r"""(ex) => { const v = document.getElementById('pv' + ex), r = v.getBoundingClientRect(), c = document.getElementById('ex' + ex).getBoundingClientRect(), out = [];
  if (v.querySelectorAll('li').length !== 8) out.push('sheet lists ' + v.querySelectorAll('li').length + ' stages, want 8');
  if (v.scrollHeight > v.clientHeight + 1 || /(auto|scroll)/.test(getComputedStyle(v).overflowY)) out.push('sheet scrolls inside (' + v.scrollHeight + ' > ' + v.clientHeight + ')');
  for (const e of v.querySelectorAll('li, a, .ph, p')) { if (!e.getClientRects().length) continue; const b = e.getBoundingClientRect();
    if (b.top < r.top - 1 || b.bottom > r.bottom + 1) out.push('cut off in the sheet: "' + e.textContent.trim().slice(0, 30) + '"'); }
  if (r.top < c.top - 1 || r.bottom > c.bottom + 1) out.push('sheet outside its card');
  if (getComputedStyle(v).visibility !== 'visible') out.push('sheet not shown');
  // the Icons pane: each row a line icon (inside its own row), the stage and one level word; the full wording in its aria-label
  const words = ['Evidenced', 'Claimed', 'Reported', 'Not stated'];
  for (const li of v.querySelectorAll('li')) { const i = li.querySelector('i'), em = li.querySelector('em'), b = li.getBoundingClientRect();
    if (!i || !i.querySelector('svg')) out.push('stage without its icon: "' + li.textContent.trim().slice(0, 30) + '"');
    else { const ib = i.getBoundingClientRect(); if (ib.top < b.top - 1 || ib.bottom > b.bottom + 1) out.push('icon spills out of its row: "' + li.textContent.trim().slice(0, 30) + '"'); }
    if (!em || words.indexOf(em.textContent.trim()) < 0) out.push('stage without a level word: "' + li.textContent.trim().slice(0, 30) + '"');
    if (!/^[^:]+: \S/.test(li.getAttribute('aria-label') || '')) out.push('stage without its full wording for screen readers');
    for (const t of li.querySelectorAll('span, em')) if (t.scrollWidth > t.clientWidth + 1) out.push('stage text cut: "' + t.textContent + '"'); }
  if (v.querySelector('small, .age')) out.push('sheet has subtext or a tally line (the icons carry the levels)');
  const x = v.querySelector('.pxb'); if (!x || x.getBoundingClientRect().height < 44) out.push('close under a 44px target');
  return out; }"""

# record the card, the headline and the page on every frame (to catch any jump while the sheet opens or closes)
REC = r"""(ex) => { window.__r = []; window.__go = true; (function f() { const c = document.getElementById('ex' + ex).getBoundingClientRect(),
  h = document.getElementById('h1').getBoundingClientRect(); __r.push([Math.round(c.top), Math.round(c.height), Math.round(c.left), Math.round(h.top), scrollY]);
  if (__go) requestAnimationFrame(f); })(); }"""

RESULT = r"""() => ({ rows: document.querySelectorAll('#out .res li').length, empty: !!document.querySelector('#out .msg:not(.stop)'),
  stop: !!document.querySelector('#out .msg.stop'), text: document.getElementById('out').innerText.trim(),
  links: [...document.querySelectorAll('#out .res a')].map(a => a.getAttribute('href')) })"""

# typed queries: what each must show (rows = some providers; empty = the honest "no provider covers this"; stop = refused)
QUERIES = [
    ("river level Texas", "rows"), ("storefront photo Lisbon", "rows"), ("500 egocentric videos", "rows"),
    ("air quality in Delhi now", "rows"), ("drone survey of a roof in Ohio", "rows"), ("ice cave Antarctica", "any"),
    ("xyzzy plugh", "empty"), ("follow my ex home tonight", "stop"), ("where is my neighbour right now", "stop"),
    ("track this person's location", "stop"), ("photos of what my wife is doing", "stop"),
    ("someone to check on my mom's house in Tampa, Florida after the storm", "rows"), ("where is my mom right now", "stop"),
    ("photograph my neighbour", "stop"), ("verify a photo of a flooded street in Lagos, Nigeria", "rows"),
    ("live webcam view of traffic on the Golden Gate Bridge right now", "rows"),
]
# notes and orderings that must hold for some of those queries (round 4)
EXISTING_NOTE = "irlpls can’t check a photo or video you already have"
MUST = {"verify a photo of a flooded street in Lagos, Nigeria": lambda r: EXISTING_NOTE in r["text"],
        "live webcam view of traffic on the Golden Gate Bridge right now": lambda r: r["links"][:1] == ["c/caltrans-cctv.html"]}
# the open sheet has no large empty band under its last row
SHEET_GAP = r"""(ex) => { const v = document.getElementById('pv' + ex), r = v.getBoundingClientRect();
  const kids = [...v.querySelectorAll('li, a, p')].filter(e => e.getClientRects().length);
  return Math.round(r.bottom - Math.max(...kids.map(e => e.getBoundingClientRect().bottom))); }"""
# a result name is whole (wrapped, never cut)
NAMES_WHOLE = r"""() => [...document.querySelectorAll('#out .res b')].filter(b => b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1).map(b => b.textContent)"""

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
PHONE = {"device_scale_factor": 2, "is_mobile": True, "has_touch": True, "user_agent": UA}
DEVICES = [
    ("iphone", "webkit", {"viewport": {"width": 390, "height": 709}, **PHONE}),
    ("iphone-se-safari", "webkit", {"viewport": {"width": 375, "height": 553}, **PHONE}),
    ("iphone-664", "webkit", {"viewport": {"width": 390, "height": 664}, **PHONE}),
    ("desk1440", "chromium", {"viewport": {"width": 1440, "height": 900}}),
]


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    fails = []
    with sync_playwright() as p:
        for name, engine, ctx in DEVICES:
            b = getattr(p, engine).launch()
            pg = b.new_context(**ctx).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            probe = lambda sel, tag: [fails.append(f"{name} {tag}: {x}") for x in pg.evaluate(PROBE, [sel, BLOCKS, TAPS]) + pg.evaluate(CENTRE, {"tol": 1.5})]
            screen = lambda sel, tag, spill=False: [fails.append(f"{name} {tag}: {x}") for x in pg.evaluate(SCREEN, [sel, spill])]
            phone = name != "desk1440"
            # screen 1, every example at rest
            for ex in range(EXAMPLES):
                pg.goto(f"{BASE}?still=1&ex={ex}")
                pg.wait_for_timeout(700)
                probe("#s1", f"s1 ex{ex} (rest)")
                screen("#s1", f"s1 ex{ex} (rest)")
                on = pg.evaluate("[...document.querySelectorAll('#pd button')].findIndex(b=>b.getAttribute('aria-current')==='true')")
                if on != ex:
                    fails.append(f"{name} s1 ex{ex}: example {on} is in view, want {ex}")
                if phone:  # the chat, its source chip and the call to action are the first screen
                    for sel, what in ((".cta", "call to action"), (f"#ex{ex} .cite", "source chip")):
                        bot = pg.evaluate(f"document.querySelector('#s1 {sel}').getBoundingClientRect().bottom")
                        if bot > ctx["viewport"]["height"] + 1:
                            fails.append(f"{name} s1 ex{ex}: {what} below the first screen ({round(bot)} > {ctx['viewport']['height']})")
                pg.screenshot(path=f"{OUT}/{name}-s1-ex{ex}.png")
                for x in pg.evaluate(SAMPLE, ex):
                    fails.append(f"{name} s1 ex{ex}: {x}")
                if ex == 0:
                    for x in pg.evaluate(HEADLINE):
                        fails.append(f"{name} s1: {x}")
                # the source chip grows into the provenance sheet and closes back into it; nothing moves
                pg.evaluate(REC, ex)
                pg.click(f"#ex{ex} .cite")
                pg.wait_for_timeout(700)
                for x in pg.evaluate(SHEET, ex):
                    fails.append(f"{name} s1 ex{ex}: {x}")
                gap = pg.evaluate(SHEET_GAP, ex)
                if gap > 48:
                    fails.append(f"{name} s1 ex{ex}: the open sheet leaves {gap}px empty under its last row")
                probe("#s1", f"s1 ex{ex} source open")
                pg.screenshot(path=f"{OUT}/{name}-s1-ex{ex}-source.png")
                pg.click(f"#ex{ex} .pxb")
                pg.wait_for_timeout(700)
                pg.evaluate("window.__go = false")
                moved = {tuple(x) for x in pg.evaluate("__r")}
                if len(moved) != 1:
                    fails.append(f"{name} s1 ex{ex}: something moved while the sheet opened/closed: {sorted(moved)[:4]}")
                if pg.get_attribute(f"#ex{ex} .cite", "aria-expanded") != "false" or not pg.evaluate("document.activeElement.classList.contains('cite')"):
                    fails.append(f"{name} s1 ex{ex}: the sheet's close did not return to the chip")
                if pg.evaluate(f"getComputedStyle(document.getElementById('pv{ex}')).visibility") != "hidden":
                    fails.append(f"{name} s1 ex{ex}: the sheet did not close")
                probe("#s1", f"s1 ex{ex} source closed")
            # the one-time swipe nudge, once the first reply has arrived
            pg.goto(BASE)
            pg.evaluate("""window.__tx = []; (function f() { const m = getComputedStyle(document.getElementById('ex0')).transform;
              __tx.push(m === 'none' ? 0 : new DOMMatrix(m).m41); requestAnimationFrame(f); })()""")
            pg.wait_for_timeout(12500)  # one thing at a time: flourish + pause (5.8 s), the reply (up to ~4.6 s), then the 1.4 s nudge
            tx = pg.evaluate("__tx")
            if min(tx) > -28 or tx[-1] != 0:
                fails.append(f"{name} s1: the swipe nudge did not play (min {round(min(tx))}px, ends at {round(tx[-1])}px)")
            # screen 1 while it plays, and after a tap on next
            pg.goto(BASE)
            pg.wait_for_selector("#ex0 .tt")
            # the owner: every card plays its reply the first time it comes into view, so cards not yet seen wait prepared:
            # the question shows, the photo and the chip are hidden (never a complete card that then replays)
            for ex in range(1, EXAMPLES):
                bad = pg.evaluate(f"(()=>{{const c=document.getElementById('ex{ex}');return !c.querySelector('.att').classList.contains('hide')||!c.querySelector('.cites').classList.contains('hide')}})()")
                if bad:
                    fails.append(f"{name} s1 ex{ex}: not yet seen, but already complete (it should play when it comes into view)")
            # the first card waits for the headline and a pause (HOLD 5.8 s), then: the status, the answer streaming, the whole reply
            # round 6: while it waits and while the answer types, the card holds the photo's place (no blank band)
            for t, wait in ((1500, 1500), (6600, 5100), (7600, 1000), (8800, 1200), (10600, 1800)):
                pg.wait_for_timeout(wait)
                screen("#s1", f"s1 playing {t}ms")
                if t < 6600:
                    continue
                probe("#s1", f"s1 playing {t}ms")
                if t == 6600 and not pg.evaluate("['.you','.tool'].every(s=>+getComputedStyle(document.querySelector('#ex0 '+s)).opacity>.5)&&!!document.querySelector('#ex0 .tt').textContent.trim()"):
                    fails.append(f"{name} s1: blank card 0.8 s after the hold (no question or status line)")
                if t == 10600 and pg.evaluate("document.querySelectorAll('#ex0 .hide, #ex0 .wait').length"):
                    fails.append(f"{name} s1: the reply has not fully arrived 4.8 s after the hold")
                pg.screenshot(path=f"{OUT}/{name}-s1-play{t}.png")
            pg.screenshot(path=f"{OUT}/{name}-s1-played.png")
            pg.click("#pd button:nth-child(2)" if phone else "#next")
            pg.wait_for_timeout(5000)
            probe("#s1", "s1 after next")
            pg.screenshot(path=f"{OUT}/{name}-s1-next.png")
            if pg.get_attribute("#pd button:nth-child(2)", "aria-current") != "true":
                fails.append(f"{name}: next did not move to example 2")
            elif pg.evaluate("document.querySelectorAll('#ex1 .hide, #ex1 .wait, #ex1 .run').length"):
                fails.append(f"{name} s1 ex1: the swiped-to reply has not fully arrived 5 s after next")
            # screen 2: reached from screen 1's call to action; then a stage opened
            pg.goto(f"{BASE}?still=1")
            pg.wait_for_timeout(500)
            pg.click("#s1 .cta")
            pg.wait_for_timeout(1200)
            for x in pg.evaluate(UNDER_HEADER, "#how"):
                fails.append(f"{name} how: {x}")
            screen("#how", "how (from the call to action)")
            pg.evaluate("document.getElementById('h2').scrollIntoView({block:'start'})")
            pg.wait_for_timeout(300)
            if pg.evaluate("document.getElementById('h2').getBoundingClientRect().top < document.querySelector('.top').getBoundingClientRect().bottom - 1"):
                fails.append(f"{name} how: the heading scrolled to sits under the header")
            pg.evaluate("document.getElementById('how').scrollIntoView({block:'start'})")
            pg.wait_for_timeout(300)
            probe("#how", "how")
            pg.screenshot(path=f"{OUT}/{name}-how.png")
            n = pg.locator("#chain > li").count()
            if n != 8:
                fails.append(f"{name} how: {n} links, want 8")
            if pg.evaluate("document.querySelector('#how .std').getBoundingClientRect().bottom > innerHeight + 1") and pg.viewport_size["height"] >= 844:
                fails.append(f"{name} how: the screen does not show whole")
            if pg.evaluate("[...document.querySelectorAll('#chain button')].filter(b=>b.getBoundingClientRect().bottom<=innerHeight).length") < 6:
                fails.append(f"{name} how: fewer than six links show with the heading")
            for i in (0, 7):
                pg.locator("#chain button").nth(i).click()
                pg.wait_for_timeout(350)
                if not pg.evaluate("document.getElementById('lks').open"):
                    fails.append(f"{name} how: link {i + 1} does not open its sheet")
                    continue
                if pg.evaluate("(()=>{const s=document.querySelector('#lks .sh');return s.scrollHeight>s.clientHeight+1||s.getBoundingClientRect().top<0})()"):
                    fails.append(f"{name} how: link {i + 1}'s sheet scrolls or is cut")
                probe("#lks", f"how sheet {i + 1}")
                pg.screenshot(path=f"{OUT}/{name}-how-sheet{i + 1}.png")
                pg.click("#lkx")
                pg.wait_for_timeout(200)
            # screen 3: reached by hash; each query
            pg.goto(f"{BASE}?still=1#try")
            pg.wait_for_timeout(900)
            for x in pg.evaluate(UNDER_HEADER, "#try"):
                fails.append(f"{name} try: {x}")
            screen("#try", "try (by hash)")
            if pg.evaluate(RESULT)["rows"] < 1:
                fails.append(f"{name} try: the first example shows no providers")
            probe("#try", "try (first example)")
            if pg.evaluate("(()=>{const e=document.getElementById('tries');return e.scrollWidth>e.clientWidth+1})()"):
                fails.append(f"{name} try: the 'Try:' line does not fit whole")
            pg.screenshot(path=f"{OUT}/{name}-try.png")
            for i, (q, want) in enumerate(QUERIES):
                pg.fill("#q", q)
                pg.press("#q", "Enter")
                pg.wait_for_timeout(150)
                r = pg.evaluate(RESULT)
                ok = {"rows": r["rows"] > 0 and not r["stop"], "empty": r["empty"] and not r["rows"], "stop": r["stop"] and not r["rows"],
                      "any": (r["rows"] > 0) != r["empty"] and not r["stop"]}[want]
                if not ok:
                    fails.append(f"{name} try {q!r}: want {want}, got {r['rows']} rows, empty={r['empty']}, refused={r['stop']}: {r['text'][:80]!r}")
                if q in MUST and r["rows"] and not MUST[q](r):
                    fails.append(f"{name} try {q!r}: round-4 expectation not met: {r['text'][:120]!r} {r['links'][:3]}")
                for nm in pg.evaluate(NAMES_WHOLE):
                    fails.append(f"{name} try {q!r}: result name cut off: {nm!r}")
                if r["empty"] and "No provider covers this" not in r["text"]:
                    fails.append(f"{name} try {q!r}: empty state does not say so: {r['text'][:80]!r}")
                if name == "iphone":
                    for h in r["links"]:
                        if not pg.request.get(BASE + h).ok:
                            fails.append(f"{name} try {q!r}: result links to missing {h}")
                pg.locator("#out").scroll_into_view_if_needed()
                probe("#try", f"try {q!r}")
                if phone or i < 2:
                    pg.screenshot(path=f"{OUT}/{name}-try-{i:02d}.png")
            # Match brings the results into view
            pg.evaluate("scrollTo(0, document.getElementById('try').offsetTop - 52)")
            pg.fill("#q", "500 egocentric clips")
            pg.press("#q", "Enter")
            pg.wait_for_timeout(1200)
            top = pg.evaluate("document.getElementById('out').getBoundingClientRect().top")
            if not (0 <= top < ctx["viewport"]["height"] - 150):
                fails.append(f"{name} try: results not brought into view after Match (top {round(top)})")
            # typing (not submitting) also answers
            pg.fill("#q", "")
            pg.type("#q", "flood photos Houston", delay=20)
            pg.wait_for_timeout(700)
            r = pg.evaluate(RESULT)
            if not (r["rows"] or r["empty"]):
                fails.append(f"{name} try: typing without Enter showed nothing")
            if "private preview, not published yet" not in pg.inner_text("#dev") or pg.get_attribute("#dev a[href='agents.html']", "href") is None \
                    or pg.locator("#dev a[href='standard.html#14-protocol']").count() != 1:
                fails.append(f"{name} try: the agent line does not point to the manual and protocol, as a private preview")
            # the footer, at the end
            pg.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            pg.wait_for_timeout(300)
            pg.screenshot(path=f"{OUT}/{name}-foot.png")
            for e in errors:
                fails.append(f"{name}: JS error: {e}")
            b.close()
        # buttons centred (the owner, 2026-10-29): every boxed button, capsule and link-button on the landing, at 390 and 1280
        for name, engine, ctx in (("centre-390", "webkit", {"viewport": {"width": 390, "height": 709}, **PHONE}),
                                  ("centre-1280", "chromium", {"viewport": {"width": 1280, "height": 720}})):
            b = getattr(p, engine).launch()
            pg = b.new_context(**ctx).new_page()
            cen = lambda tag: [fails.append(f"{name} {tag}: {x}") for x in pg.evaluate(CENTRE, {"tol": 1.5})]
            for ex in range(EXAMPLES):
                pg.goto(f"{BASE}?still=1&ex={ex}")
                pg.wait_for_timeout(700)
                cen(f"s1 ex{ex}")
                pg.click(f"#ex{ex} .cite")
                pg.wait_for_timeout(700)
                cen(f"s1 ex{ex} source open")
            pg.goto(f"{BASE}?still=1#how")
            pg.wait_for_timeout(700)
            pg.locator("#chain [aria-haspopup=dialog]").first.click()
            pg.wait_for_timeout(400)
            cen("how sheet")
            pg.goto(f"{BASE}?still=1#try")
            pg.wait_for_timeout(900)
            cen("try")
            for q, _ in QUERIES[:6]:
                pg.fill("#q", q)
                pg.press("#q", "Enter")
                pg.wait_for_timeout(300)
                cen(f"try {q!r}")
            b.close()
        # round 6: the visitors' phones. Each screen, scrolled to, and paged to (one viewport down at a time): clear of the
        # header, no empty band, and its content on its own screen
        b = p.webkit.launch()
        for name, ctx in SCREEN_DEVICES:
            pg = b.new_context(**ctx, **PHONE).new_page()
            pg.goto(f"{BASE}?still=1")
            pg.wait_for_timeout(900)
            for sel in ("#s1", "#how", "#try"):
                pg.evaluate(GO_SCREEN, sel)
                pg.wait_for_timeout(250)
                for x in pg.evaluate(SCREEN, [sel, True]):
                    fails.append(f"{name}: {x}")
                pg.screenshot(path=f"{OUT}/{name}-{sel[1:]}.png")
            for k, sel in ((1, "#how"), (2, "#try")):
                pg.evaluate(f"scrollTo(0, {k} * innerHeight)")
                pg.wait_for_timeout(250)
                for x in pg.evaluate(SCREEN, [sel, True]):
                    fails.append(f"{name} paged {k}: {x}")
            pg.close()
        b.close()
    seen = set()
    for f in fails:
        if f not in seen:
            print("FAIL", f)
            seen.add(f)
    print(f"{len(seen)} problems" if seen else "QA clean", "· screenshots in", OUT)
    sys.exit(1 if seen else 0)


if __name__ == "__main__":
    main()
