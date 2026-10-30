"""Visual QA for the secondary pages: the finder (find.html), every Capture Card page (c/<id>.html), compare.html,
the spec (standard.html), the long documents (changelog, governance, privacy, glossary, providers, agents), the
chain and check pages, and the 404 page.
Same approach as tools/qa.py (the landing page): the floor of quality, checked before anyone else looks.

    python3 build.py && uv run --with playwright python tools/qa_pages.py [OUTDIR] [BASE_URL]

With no BASE_URL it serves site/ itself on a free local port, so it always checks the build on disk.
Renders every state on an iPhone (WebKit, the 390x709 area above Safari's floating bar) and two desktops
(1440x900, 1280x720), and fails on:
  - anything wider than the screen (sideways scroll), or text running off the side
  - text cut off by its container (overflow hidden / ellipsis), and words broken mid-word across lines
  - text covered by anything else (overlap), and fixed/sticky bars outside the visible viewport
  - after scrolling: text showing through a translucent sticky bar (background alpha < 0.99 or a backdrop filter)
  - empty states: a page or list with nothing in it and no message saying why
  - word budgets: finder controls, each result row, the card header, each guarantee cell, the card's first iPhone
    screen, each compare cell
  - tap targets under 40px on the phone, and a side gutter other than 16px
  - a boxed button, capsule or link-button whose label (text plus any icon) is off its box's centre by more than
    1.5px, vertically or horizontally (tools/centre_probe.py), in every state on every device (390, 1440, 1280)
  - JavaScript errors (uncaught or console.error)
  - with JavaScript off: the finder must list every provider (built from cards.json), card pages must be complete,
    compare must say it needs JavaScript, no dead controls may show, and nothing may render below the footer
  - values: every card's page, finder row and compare column is checked against cards/*.json (derived here,
    independently of build.py and app.js), and no internal token (snake_case, matcher words) may show as copy
Screenshots of every state land in OUTDIR so a person can look too. Prints "QA clean" or the problems; exit 1 on any.
"""
import functools
import http.server
import json
import os
import re
import pathlib
import sys
import threading
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from centre_probe import CENTRE  # every boxed button's label centred in its box, within 1.5px (shared with qa.py)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = sys.argv[1] if len(sys.argv) > 1 else "qa-pages"
BASE = sys.argv[2] if len(sys.argv) > 2 else None

BUDGET = {
    "finder_controls": 44,  # +2 (2026-10-30): the read-back of a street address ("50 Elm St, Burlington, VT → Vermont"). +2: the header link "Sources" became "Find data" (the owner's rename). round 5: "made to order" (was "commissioned") and the stage key's "not stated"   # everything on the finder except the result rows (incl. the lede and the "N sources match" line)
    "row": 17,              # one result row (round 5: the eight-stage tally "n claimed · n evidenced" replaced three chips); +1 (sweep nit 14): "Speed not stated" matches "Price not stated"
    "card_head": 60,        # kicker, name, one-liner, fit, spec grid
    "best": 30,             # the finder's best match: label, name, one plan line, readiness line, one or two buttons (its call and live answer apart)
    "call_lines": 70,       # under the top match's ready call: the key and where to get it, up to three values to fill, approval, "Nothing is sent"
    "live_answer": 45,
    "notes": 45,            # the finder's plain notes above the matches (refused, can't capture the past, already have it, no evidence)      # the top match's live answer inline: the reading, its source and age, who fetched it
    "stage_row": 6,         # one link row's head: number + name + level ("8. Delivered to you Not stated")
    "card_first_screen": 90,  # visible words on the card's first iPhone screen (nav included); +10 (sweep 2026-10-29): each count names its unit ("1 of 8 links claimed", "Source guarantees · 3 of 7 claimed · 0 evidenced")
    "cmp_cell": 6,          # one compare cell
}

Q = urllib.parse.quote
FINDER = [
    ("find-empty", "find.html"),
    ("find-river", "find.html?q=" + Q("river level in Texas right now")),
    ("find-store", "find.html?q=" + Q("storefront photos in Lisbon with proof of when and where")),
    ("find-drone", "find.html?q=" + Q("drone survey of a construction site in Canada")),
    ("find-proof", "find.html?q=" + Q("verified human photos of a roof, camera proof, cross-checked")),
    ("find-none", "find.html?q=" + Q("xyzzy plugh")),
    ("find-call", "find.html?q=" + Q("satellite image of Austin, TX")),
    ("find-refused", "find.html?q=" + Q("follow my ex home tonight")),
    ("find-past", "find.html?q=" + Q("storefront photo, Lisbon, last week")),
    ("find-filtered-empty", "find.html?q=" + Q("river level in Texas right now") + "&made=1"),
    ("find-free", "find.html?free=1"),
    ("sheet-q", "find.html?q=" + Q("river level in Texas right now") + "#usgs-water"),
    ("sheet", "find.html#proxypics"),
]
COMPARE = [
    ("cmp-empty", "compare.html"),
    ("cmp-2", "compare.html?ids=nws,usgs-water"),
    ("cmp-3", "compare.html?ids=nws,usgs-water,proxypics"),
    ("cmp-4", "compare.html?ids=nws,usgs-water,proxypics,skyfi"),
    ("cmp-bad", "compare.html?ids=nope,zzz"),
]
# long documents and the stand-alone pages (no word budgets: they are read, not scanned)
DOCS = [("docs", "docs.html"), ("std", "standard.html"), ("log", "changelog.html"), ("gov", "governance.html"), ("privacy", "privacy.html"),
        ("glossary", "glossary.html"), ("providers", "providers.html"), ("agents", "agents.html"), ("chain", "chain.html"),
        ("check", "check.html"), ("check-id", "check.html?id=proxypics"), ("404", "c/nope.html")]
NOJS = [("find", "find.html", "finder"), ("card-usgs", "c/usgs-water.html", "card"), ("card-zeitview", "c/zeitview.html", "card"),
        ("card-proxypics", "c/proxypics.html", "card"), ("card-capella", "c/capella.html", "card"), ("card-nws", "c/nws.html", "card"),
        ("compare", "compare.html?ids=nws,usgs-water", "compare")] + [(n, u, "doc") for n, u in DOCS]
READ_AS = ["radar image of a port through clouds", "first-person clips of opening doors", "air quality in Fresno right now",
           "photo of the storefront at 123 Main St, Springfield, IL", "verified human photos of a roof under $50"]

PROBE = r"""(B) => {
  const out = [], W = innerWidth, H = innerHeight, r = e => e.getBoundingClientRect();
  const words = s => s.split(/\s+/).filter(w => /[\wÀ-￿$]/.test(w)).length;
  const shown = el => { const d = el.closest('details:not([open])'); if (d && !(el.closest('summary') && el.closest('summary').parentElement === d)) return false;
    for (let e = el; e && e !== document.body; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < .05 || e.classList.contains('sr')) return false } return true };
  const sheet = document.getElementById('sheet'), inSheet = sheet && !sheet.hidden;
  const scope = inSheet ? sheet : document.body;
  if (document.documentElement.scrollWidth > W + 1) out.push('sideways scroll: ' + document.documentElement.scrollWidth + ' > ' + W);
  if (inSheet && sheet.scrollWidth > sheet.clientWidth + 1) out.push('sideways scroll in the sheet');
  // every visible piece of text, as rects
  const texts = [], tw = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = tw.nextNode())) {
    const el = n.parentElement;
    if (!n.textContent.trim() || /^(SCRIPT|STYLE|NOSCRIPT|OPTION|SELECT)$/.test(el.tagName) || el.closest('noscript') || !shown(el)) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const rc of rg.getClientRects()) if (rc.width > 1 && rc.height > 1) texts.push({ el, rc, t: n.textContent.trim().slice(0, 30) });
  }
  // words broken across two lines ("Commissi / on"); long URLs and paths may break at their punctuation
  const tw2 = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT); let bn;
  while ((bn = tw2.nextNode())) {
    const el = bn.parentElement; if (!bn.textContent.trim() || !shown(el) || /^(SCRIPT|STYLE|OPTION)$/.test(el.tagName) || el.closest('pre')) continue;  // code (the ready call) wraps anywhere
    const all = document.createRange(); all.selectNodeContents(bn); if (all.getClientRects().length < 2) continue;
    const re = /[^\s\/\u2010-\u2014-]+/g; let m;  // pieces between spaces, hyphens and slashes: breaking at those is fine
    while ((m = re.exec(bn.textContent))) { if (/[.:_]/.test(m[0]) && m[0].length > 18) continue;
      const rg = document.createRange(); rg.setStart(bn, m.index); rg.setEnd(bn, m.index + m[0].length);
      const tops = new Set([...rg.getClientRects()].filter(x => x.width > 1).map(x => Math.round(x.top)));
      if (tops.size > 1) out.push('word broken across lines: "' + m[0] + '"'); }
  }
  const scroller = el => { for (let e = el.parentElement; e; e = e.parentElement) { const cs = getComputedStyle(e);
      if (/(auto|scroll)/.test(cs.overflowX) && e.scrollWidth > e.clientWidth + 1) return e } return null };
  for (const x of texts) {
    const sc = scroller(x.el);
    if (!sc && (x.rc.right > W + 1 || x.rc.left < -1)) out.push('text off the side: "' + x.t + '"');
    // clipped by an ancestor that hides overflow
    for (let e = x.el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (/(hidden|clip)/.test(cs.overflowX + cs.overflowY)) { const a = r(e);
        if (e === sc && x.rc.bottom <= a.bottom + 1 && x.rc.top >= a.top - 1) continue;  // a sideways-scrolling row: off to the side on purpose, never cut top or bottom
        if (x.rc.right > a.right + 1 || x.rc.bottom > a.bottom + 1 || x.rc.left < a.left - 1 || x.rc.top < a.top - 1) { out.push('text clipped: "' + x.t + '"'); break } }
      if (cs.textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth + 1) { out.push('text truncated: "' + x.t + '"'); break }
    }
    // covered: what's actually painted at the middle of the text must be that text's element (or its family)
    const cx = (x.rc.left + x.rc.right) / 2, cy = (x.rc.top + x.rc.bottom) / 2;
    if (!sc && cx > 0 && cx < W && cy > 0 && cy < H) {
      const hit = document.elementFromPoint(cx, cy);
      if (hit && !(hit === x.el || x.el.contains(hit) || hit.contains(x.el))) out.push('text covered: "' + x.t + '" under ' + hit.tagName.toLowerCase() + '.' + String(hit.className).split(' ')[0]);
    }
  }
  // the page gutter: on the phone, text starts 16px from the edge (as on the landing page), never more or less
  if (B.phone) { const ls = texts.filter(x => !scroller(x.el)).map(x => x.rc.left); const m = Math.min(...ls);
    if (ls.length && (m < 14 || m > 18)) out.push('side gutter is ' + Math.round(m) + 'px, not 16'); }
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const a = texts[i], b = texts[j]; if (a.el === b.el) continue;
    const ox = Math.min(a.rc.right, b.rc.right) - Math.max(a.rc.left, b.rc.left), oy = Math.min(a.rc.bottom, b.rc.bottom) - Math.max(a.rc.top, b.rc.top);
    if (ox > 2 && oy > 2) out.push('overlap: "' + a.t + '" / "' + b.t + '"');
  }
  // fixed and sticky bars sit inside the visible area (above Safari's bar)
  for (const e of scope.querySelectorAll('*')) { const p = getComputedStyle(e).position;
    if ((p === 'fixed' || p === 'sticky') && shown(e) && e.id !== 'sheet' && !e.closest('table')) { const a = r(e); if (a.height && (a.bottom > H + 1 || a.top < -1)) out.push('bar outside the viewport: ' + e.className) } }
  // tap targets on the phone
  if (B.phone) for (const e of scope.querySelectorAll('button, select, summary, a.row, .chips button, nav a')) {
    if (!shown(e)) continue; const a = r(e); if (a.height && a.height < 40) out.push('tap target ' + Math.round(a.height) + 'px: ' + (e.textContent.trim().slice(0, 20) || e.getAttribute('aria-label')));
  }
  // words
  const vis = el => { let s = 0; const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let t;
    while ((t = w.nextNode())) if (shown(t.parentElement) && !/^(OPTION|SCRIPT|STYLE)$/.test(t.parentElement.tagName)) s += words(t.textContent); return s };
  const budget = (k, v, what) => { if (v > B.budget[k]) out.push(what + ': ' + v + ' words (budget ' + B.budget[k] + ')') };
  const page = B.kind;
  if (page === 'finder' && !inSheet) {
    const main = document.getElementById('main'), grid = document.getElementById('grid');
    const best = document.getElementById('best');  // the best match (read like the landing's screen 3) has its own budget
    const notes = document.getElementById('notes');  // what irlpls won't or can't do for this ask: its own budget
    budget('finder_controls', vis(main) - vis(grid) - (best ? vis(best) : 0) - (notes ? vis(notes) : 0), 'finder controls');
    if (notes) budget('notes', vis(notes), 'notes');
    const bcall = document.getElementById('bcall'), blv = document.getElementById('blv'), pre = bcall && bcall.querySelector('pre');
    if (best) budget('best', vis(best) - (bcall ? vis(bcall) : 0) - (blv ? vis(blv) : 0), 'best match');
    if (bcall) budget('call_lines', vis(bcall) - (pre ? vis(pre) : 0), 'the call\'s notes');
    if (blv) budget('live_answer', vis(blv), 'live answer');
    grid.querySelectorAll('.row').forEach((row, i) => budget('row', vis(row), 'row ' + (i + 1)));
    if (!grid.querySelector('.row') && !document.getElementById('meta').textContent.trim()) out.push('empty: no rows and no message');
  }
  const card = scope.querySelector('.cardv');
  if (page === 'card' || inSheet) {
    if (!card || card.querySelectorAll('.stg-o > li').length !== 8) out.push('empty: card has no eight stages');
    else {
      let head = 0; for (const s of card.querySelectorAll('.ttl, .lede, .use, .fitb, .spec')) head += vis(s);
      budget('card_head', head, 'card header');
      card.querySelectorAll('.stg-o > li').forEach(li => budget('stage_row', (li.querySelector('span').textContent + ' ' + li.querySelector('b').textContent).split(/\s+/).length, 'stage ' + li.querySelector('span').textContent));
      if (card.querySelector('.gua')) out.push('a second proof tally (the old guarantee grid) is showing');
      if (!card.querySelector('.api, .none-api')) out.push('card has no API block');
    }
  }
  if (page === 'compare') {
    const t = document.querySelector('#cmp table'), e = document.querySelector('#cmp .empty, #cmp li a, noscript ~ *');
    if (!t && !e) out.push('empty: compare shows nothing');
    const sc = document.getElementById('cmp');
    if (t && sc.scrollWidth > sc.clientWidth + 1) out.push('compare table scrolls sideways with only ' + (t.querySelectorAll('thead th').length - 1) + ' providers');
    if (t) t.querySelectorAll('td').forEach(td => budget('cmp_cell', vis(td), 'compare cell "' + td.textContent.trim().slice(0, 20) + '"'));
  }
  if (!texts.length) out.push('empty page');
  // words on the first screen (phone): count each text node once if any of it is on screen
  let first = 0;
  const w2 = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT); let m;
  while ((m = w2.nextNode())) { const el = m.parentElement; if (!m.textContent.trim() || !shown(el) || /^(OPTION|SCRIPT|STYLE)$/.test(el.tagName)) continue;
    const rg = document.createRange(); rg.selectNodeContents(m); const rc = rg.getBoundingClientRect(); if (rc.height && rc.top < H && rc.bottom > 0) first += words(m.textContent) }
  return { problems: out, first };
}"""

# after scrolling: a sticky or fixed bar that lets text show through (background alpha < 0.99, or a backdrop filter)
# must not have any page text under it. Round 5: body text ghosted through the translucent inner-page header.
GHOST = r"""() => {
  const out = [], alpha = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return c === 'transparent' ? 0 : 1;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean); return p.length > 3 ? parseFloat(p[3]) : 1 };
  const bars = [...document.querySelectorAll('body *')].filter(e => { const cs = getComputedStyle(e);
    return (cs.position === 'sticky' || cs.position === 'fixed') && cs.display !== 'none' && cs.visibility !== 'hidden' && e.id !== 'sheet' && !e.closest('table') && e.getBoundingClientRect().height > 0 });
  for (const bar of bars) {
    const cs = getComputedStyle(bar), bf = cs.backdropFilter || cs.webkitBackdropFilter || 'none', a = alpha(cs.backgroundColor);
    if (a >= 0.99 && (bf === 'none' || !bf)) continue;
    const b = bar.getBoundingClientRect(), w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) { const el = n.parentElement; if (!n.textContent.trim() || bar.contains(el) || /^(SCRIPT|STYLE|NOSCRIPT)$/.test(el.tagName)) continue;
      const vs = getComputedStyle(el); if (vs.visibility === 'hidden' || vs.display === 'none') continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) if (r.width > 1 && r.height > 1 && r.bottom > b.top + 1 && r.top < b.bottom - 1 && r.right > b.left && r.left < b.right) {
        out.push('text shows through the ' + (bar.className || bar.tagName.toLowerCase()) + ' bar (alpha ' + a + ', backdrop ' + bf + '): "' + n.textContent.trim().slice(0, 30) + '"'); return out } }
  }
  return out;
}"""


DEVICES = [
    ("iphone", "webkit", {"viewport": {"width": 390, "height": 709}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
                          "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"}),
    ("desk1440", "chromium", {"viewport": {"width": 1440, "height": 900}}),
    ("desk1280", "chromium", {"viewport": {"width": 1280, "height": 720}}),
]


# ---- expected values, derived here from cards/*.json on their own (not imported from build.py or app.js)
SHORT = {"human": "Person", "application": "App", "camera": "Camera", "time": "Time", "location": "Place", "content": "Content", "corroboration": "Cross-check"}
LEVEL = {"evidenced": "Evidenced", "claimed": "Claimed", "reported": "Reported", "none": "Not stated"}  # the one set of level words
STAGE_FROM = [["human"], ["camera", "location"], ["application"], ["camera", "content"], ["time"], [], ["corroboration"], []]  # docs/GLOSSARY.md
TOKENS = re.compile(r"\b[a-z]+_[a-z0-9_]+\b|\b(allweather|egocentric|no_match|text-report)\b")


def km2(s):
    """Pages write a card's "km2" as km² (build.py e(), app.js unit())."""
    return re.sub(r"(?<![\w/.:#=?&-])km2\b|(?<=\d/)km2\b", "km²", s)


def exp_price(c):
    cost, usd = (c.get("access") or {}).get("cost"), (c.get("pricing") or {}).get("from_usd")
    fixed = {"free": "Free", "free with key": "Free with a key", "freemium": "Free tier"}
    if cost in fixed:
        return fixed[cost], None
    if usd:
        return None, ("$%g" % usd if usd < 1 else "$%.2f" % usd if usd % 1 else "$%d" % usd)  # the number must show
    return {"quote": "On request", "paid": "Price not stated"}.get(cost, "Not stated"), None


def exp_right(v):
    return {"allowed": "Allowed", "forbidden": "Not allowed"}.get(v, "Not stated")


def exp_key(c):
    k = (c.get("access") or {}).get("key_required")
    link = any(m.get("type") == "oauth2" and (m.get("cimd") or m.get("dynamic_registration")) for m in (c.get("api") or {}).get("auth") or [])
    s = "Not stated" if k is None else "Required" if k else "Not needed"
    return s + ", or link an account" if k and link else s


def exp_mode(c):
    m = (c.get("delivery") or {}).get("modes") or []
    return "Live, made to order" if len(m) > 1 else "Live" if m == ["live"] else "Made to order"


def exp_speed(c):
    s = (c.get("delivery") or {}).get("speed")
    return s if s and s != "unknown" else "Speed not stated"


def price_ok(c, shown):
    """Free/quote words must match exactly; a price must show the card's number and its whole unit phrase
    (up to a bracket, semicolon or ", "): "$10 / 1,000,000 points", never "$10 / 1"."""
    fixed, number = exp_price(c)
    if fixed is not None:
        return shown == fixed
    unit = km2(re.sub(r"^per ", "", str(c["pricing"].get("unit") or "")))
    if not unit:
        return shown == number
    if not shown.startswith(number + " / "):
        return False
    rest = shown[len(number) + 3:]
    return unit.startswith(rest) and re.match(r"(\s*\(|;|,\s|$)", unit[len(rest):]) is not None


def card_problems(pg, c):
    """A built card page against its JSON."""
    out = []
    got = pg.evaluate("""() => { const q = s => document.querySelector(s), t = s => (q(s) || {}).textContent || null;
      const spec = {}; document.querySelectorAll('.cardv .spec > div').forEach(d => spec[d.querySelector('dt').textContent] = d.querySelector('dd').textContent);
      return { name: t('.cardv .ttl'), lede: t('.cardv .lede'), spec,
        stg: [...document.querySelectorAll('.cardv .stg-o > li')].map(li => [li.querySelector('span').textContent, li.className, li.querySelector('b').textContent, (li.querySelector('.sd') || {}).textContent || '']),
        gua: document.querySelectorAll('.cardv .gua').length,
        base: [...document.querySelectorAll('.api dl > div')].filter(d => d.querySelector('dt').textContent === 'base').map(d => d.querySelector('dd').textContent)[0] || null,
        noapi: !!q('.none-api'), use: t('.cardv .use') } }""")
    if got["name"] != c["name"]:
        out.append(f"name {got['name']!r} != {c['name']!r}")
    if got["lede"] != km2(c.get("short") or c.get("summary")):
        out.append(f"one-liner differs from the card's short")
    sp, lic = got["spec"], c.get("licensing") or {}
    want = {"Resale": exp_right(lic.get("resale")), "AI training": exp_right(lic.get("ai_training")), "Key": exp_key(c),
            "Mode": exp_mode(c) + " · " + exp_speed(c)}
    for k, v in want.items():
        if sp.get(k) != v:
            out.append(f"{k} shows {sp.get(k)!r}, card says {v!r}")
    if not price_ok(c, sp.get("Price") or ""):
        out.append(f"Price shows {sp.get('Price')!r}; card: from_usd={c['pricing'].get('from_usd')} unit={c['pricing'].get('unit')!r} cost={(c.get('access') or {}).get('cost')!r}")
    regions = (c.get("coverage") or {}).get("regions") or []
    if regions and regions[0].split("(")[0].split(",")[0].strip() not in (sp.get("Coverage") or ""):
        out.append(f"Coverage {sp.get('Coverage')!r} doesn't start from {regions[0]!r}")
    if got["gua"]:
        out.append("a second proof tally (guarantee grid) beside the stages")
    if len(got["stg"]) != 8:
        out.append(f"{len(got['stg'])} stages, not 8")
    for i, (name, cls, word, detail) in enumerate(got["stg"]):
        if cls not in LEVEL or word != LEVEL[cls]:
            out.append(f"stage {name}: level word {word!r} for {cls!r}")
        for k in STAGE_FROM[i] if i < len(STAGE_FROM) else []:  # each card field the stage reads is named, with the card's own level
            lv = c["verification"][k]["level"]
            if f"{SHORT[k]} · {LEVEL[lv]}" not in detail:
                out.append(f"stage {name}: {SHORT[k]} should read {LEVEL[lv]}")
    api = c.get("api") or {}
    if api.get("available"):
        if api.get("base_url") and got["base"] != api["base_url"]:
            out.append(f"API base {got['base']!r} != {api['base_url']!r}")
    elif not got["noapi"]:
        out.append("card has no API but the page doesn't say so")
    if got["use"] and TOKENS.search(got["use"]):
        out.append(f"internal token in the use line: {got['use']!r}")
    return out


def row_problems(pg, by_id):
    """Finder rows against the JSON: name and mode · speed · price."""
    out = []
    for rid, nm, sub in pg.evaluate("[...document.querySelectorAll('#grid a.row')].map(a=>[a.dataset.id,a.querySelector('.nm').textContent,a.querySelector('.sub').textContent])"):
        c = by_id.get(rid)
        if not c:
            out.append(f"row for unknown id {rid}")
            continue
        parts = sub.split(" · ")
        if nm != re.sub(r"\s*\(.*\)", "", c["name"]):
            out.append(f"row {rid}: name {nm!r}")
        if len(parts) != 3 or parts[0] != exp_mode(c) or parts[1] != exp_speed(c) or not price_ok(c, parts[2]):
            out.append(f"row {rid}: {sub!r} disagrees with the card")
    return out


def nojs_problems(pg, kind, n_cards):
    out = []
    got = pg.evaluate("""() => { const vis = e => !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0;
      const f = document.querySelector('.site-foot, body > footer'), fb = f ? f.getBoundingClientRect().bottom + scrollY : 0;
      let below = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) { const el = n.parentElement; if (!n.textContent.trim() || !vis(el) || el.closest('details:not([open]) > :not(summary)')) continue;
        const r = document.createRange(); r.selectNodeContents(n); const b = r.getBoundingClientRect(); if (b.height && b.top + scrollY > fb + 1) below.push(n.textContent.trim().slice(0, 30)) }
      const dead = ['.search', '.examples', '.chips', '#parsed', '#picker select', '#more'].filter(s => vis(document.querySelector(s)));
      return { below, dead, rows: document.querySelectorAll('#grid a.row').length, nojs: [...document.querySelectorAll('main .nojs')].map(p => p.textContent),
        spec: document.querySelectorAll('.cardv .spec > div').length, gua: document.querySelectorAll('.cardv .stg-o > li').length, api: !!document.querySelector('.cardv .api, .cardv .none-api'),
        h1: (document.querySelector('h1') || {}).textContent || '' } }""")
    if got["below"]:
        out.append(f"text below the footer: {got['below'][:3]}")
    if got["dead"]:
        out.append(f"controls that do nothing without JavaScript are showing: {got['dead']}")
    if kind == "finder":
        if got["rows"] != n_cards:
            out.append(f"{got['rows']} providers listed, cards.json has {n_cards}")
        if not any("Search needs JavaScript" in t for t in got["nojs"]):
            out.append("no word that search needs JavaScript")
    if kind == "card" and (got["spec"] != 6 or got["gua"] != 8 or not got["api"]):
        out.append(f"card incomplete: spec {got['spec']}/6, stages {got['gua']}/8, api {got['api']}")
    if kind == "compare" and not any("Compare needs JavaScript" in t for t in got["nojs"]):
        out.append("no word that compare needs JavaScript")
    if kind != "finder" and any("lists providers" in t for t in got["nojs"]):
        out.append("finder copy on a page that isn't the finder")
    return out


def checks_js(pg, base, cards):
    """With JavaScript on: finder rows, compare cells and the sheet against the JSON; how a request was read; error messages."""
    out, by_id = [], {c["id"]: c for c in cards}
    pg.goto(base + "find.html")
    pg.wait_for_selector("#grid a.row")
    out += [f"finder: {x}" for x in row_problems(pg, by_id)]
    for q in READ_AS:
        pg.goto(base + "find.html?q=" + Q(q))
        pg.wait_for_timeout(150)
        read = pg.inner_text("#parsed")
        if TOKENS.search(read):
            out.append(f"read-as shows an internal token for {q!r}: {read!r}")
    pg.goto(base + "find.html?q=" + Q("air quality in Fresno right now"))
    pg.wait_for_timeout(150)
    if "not recognised" not in pg.inner_text("#parsed"):
        out.append("an unknown place (Fresno) isn't flagged as not recognised")
    sample = [c for c in cards if c["id"] in ("purpleair", "usgs-water", "proxypics", "skyfi", "capella", "zeitview")] or cards[:4]
    for i in range(0, len(sample), 3):
        grp = sample[i:i + 3]
        pg.goto(base + "compare.html?ids=" + ",".join(c["id"] for c in grp))
        pg.wait_for_selector("#cmp table")
        cells = pg.evaluate("Object.fromEntries([...document.querySelectorAll('#cmp tbody tr')].filter(r=>r.querySelector('td')).map(r=>[r.querySelector('th').textContent,[...r.querySelectorAll('td')].map(td=>td.textContent)]))")
        for j, c in enumerate(grp):
            lic = c.get("licensing") or {}
            want = {"Mode": exp_mode(c), "Speed": exp_speed(c), "Key": exp_key(c), "Resale": exp_right(lic.get("resale")), "AI training": exp_right(lic.get("ai_training"))}
            for k, v in want.items():
                if (cells.get(k) or [None] * 3)[j] != v:
                    out.append(f"compare {c['id']} {k}: shows {(cells.get(k) or [None] * 3)[j]!r}, card says {v!r}")
            if not price_ok(c, (cells.get("Price") or [""] * 3)[j]):
                out.append(f"compare {c['id']} Price: {(cells.get('Price') or [''] * 3)[j]!r}")
            # the eight stages, in the card page's words (the card page is the reference: it is checked against the JSON above)
            for si in range(8):
                got_w = pg.evaluate(f"(()=>{{const r=document.querySelectorAll('#cmp tbody.g tr:not(.sec)')[{si}];return r?r.querySelectorAll('td')[{j}].textContent:null}})()")
                if got_w not in LEVEL.values():
                    out.append(f"compare {c['id']} stage {si + 1}: {got_w!r} is not a level word")
    pg.goto(base + "compare.html?ids=nope,zzz")
    pg.wait_for_timeout(150)
    if "nope" not in pg.inner_text("#cmpmsg"):
        out.append("compare says nothing about unknown ids")
    pg.goto(base + "find.html#purpleair")
    pg.wait_for_selector("#sheet .cardv .spec")
    out += [f"sheet purpleair: {x}" for x in card_problems_sheet(pg, by_id["purpleair"])] if "purpleair" in by_id else []
    return out


def card_problems_sheet(pg, c):
    price = pg.evaluate("[...document.querySelectorAll('#sheet .spec > div')].filter(d=>d.querySelector('dt').textContent==='Price').map(d=>d.querySelector('dd').textContent)[0]")
    return [] if price_ok(c, price or "") else [f"Price shows {price!r}"]


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_error(self, code, message=None, explain=None):  # like GitHub Pages: a missing path gets site/404.html
            nf = SITE / "404.html"
            if code != 404 or not nf.exists():
                return super().send_error(code, message, explain)
            body = nf.read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
    handler = functools.partial(Quiet, directory=str(SITE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)  # WebKit keeps connections open
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


def main():
    if not (SITE / "find.html").exists():
        sys.exit("site/ not built: run python3 build.py first")
    base = BASE or serve()
    if not base.endswith("/"):
        base += "/"
    os.makedirs(OUT, exist_ok=True)
    cards = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
    ids = [c["id"] for c in json.loads((SITE / "cards.json").read_text())["cards"]]
    cards = [c for c in cards if c["id"] in ids]
    # a provider's own card (irlpls/published.py) is served in place of the compiled one: check those pages against
    # the provider's file as published (site/cards/<id>.json is that file, copied as read), not against cards/
    for i, c in enumerate(cards):
        pub = SITE / "cards" / f'{c["id"]}.json'
        if pub.exists() and json.loads(pub.read_text()).get("status") == "provider-maintained":
            cards[i] = json.loads(pub.read_text())
    if len(cards) != len(ids):
        fails_pre = [f"site/cards.json lists {len(ids)} cards but cards/ has {len(cards)} of them: rebuild"]
    else:
        fails_pre = []
    states = [(n, u, "finder") for n, u in FINDER]
    states += [(f"card-{i}", f"c/{i}.html", "card") for i in ids]
    states += [("card-q", "c/usgs-water.html?q=" + Q("river level in Texas right now"), "card")]
    states += [(n, u, "compare") for n, u in COMPARE]
    states += [(n, u, "doc") for n, u in DOCS]
    fails = list(fails_pre)
    with sync_playwright() as p:
        for dev, engine, ctx in DEVICES:
            b = getattr(p, engine).launch()
            pg = b.new_context(**ctx).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            # failed requests are caught by URL (the 404 state asks for c/nope.html on purpose), not by console timing
            pg.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource") and errors.append(m.text + " at " + pg.url))
            pg.on("response", lambda r: r.status >= 400 and "nope" not in r.url and errors.append(f"HTTP {r.status} for {r.url}"))
            phone = dev == "iphone"
            for name, url, kind in states:
                pg.goto(base + url)
                pg.wait_for_timeout(350)
                if name == "check-id":  # the going-live panel and ranks, computed in the browser from cards.json
                    pg.wait_for_selector("#live .golive", timeout=15000)
                res = pg.evaluate(PROBE, {"budget": BUDGET, "phone": phone, "kind": kind})
                if phone and kind == "card" and res["first"] > BUDGET["card_first_screen"]:
                    res["problems"].append(f"{res['first']} words on the first screen (budget {BUDGET['card_first_screen']})")
                fails += [f"{dev} {name}: {x}" for x in res["problems"]]
                fails += [f"{dev} {name}: {x}" for x in pg.evaluate(CENTRE, {"tol": 1.5})]
                if not phone:  # the owner, 2026-10-30: the footer is a full-width band; its content at least 80% of the main column's width
                    fw = pg.evaluate("""(()=>{const f=document.querySelector('.site-foot'),m=document.querySelector('main, #main');if(!f||!m)return null;
                      const cs=getComputedStyle(f);return [f.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight), m.getBoundingClientRect().width]})()""")
                    if fw and fw[0] < 0.8 * fw[1]:
                        fails.append(f"{dev} {name}: footer content {round(fw[0])}px is under 80% of the main column ({round(fw[1])}px)")
                pg.screenshot(path=f"{OUT}/{dev}-{name}.png")
                if kind != "finder" or name.startswith("find-river"):
                    pg.screenshot(path=f"{OUT}/{dev}-{name}-full.png", full_page=True)
                pg.evaluate("window.scrollTo(0, Math.min(600, document.documentElement.scrollHeight - innerHeight))")
                pg.wait_for_timeout(60)
                fails += [f"{dev} {name} scrolled: {x}" for x in pg.evaluate(GHOST)]
            # mid-interaction: typing, filters, opening and closing the sheet, every <details> opened
            pg.goto(base + "find.html")
            pg.fill("#q", "river")
            pg.wait_for_timeout(300)
            fails += [f"{dev} typing: {x}" for x in pg.evaluate(PROBE, {"budget": BUDGET, "phone": phone, "kind": "finder"})["problems"]]
            pg.click("#filters button[data-f=live]")
            pg.wait_for_timeout(150)
            fails += [f"{dev} live filter: {x}" for x in pg.evaluate(PROBE, {"budget": BUDGET, "phone": phone, "kind": "finder"})["problems"]]
            fails += [f"{dev} live filter: {x}" for x in pg.evaluate(CENTRE, {"tol": 1.5})]
            pg.screenshot(path=f"{OUT}/{dev}-typing-live.png")
            pg.locator("a.row").first.click()
            pg.wait_for_timeout(250)
            pg.evaluate("document.querySelectorAll('#sheet details').forEach(d=>d.open=true)")
            fails += [f"{dev} sheet: {x}" for x in pg.evaluate(CENTRE, {"tol": 1.5})]
            fails += [f"{dev} sheet details open: {x}" for x in pg.evaluate(PROBE, {"budget": {**BUDGET, "card_head": 999}, "phone": phone, "kind": "finder"})["problems"]
                      if "words" not in x]
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(150)
            if pg.locator("#sheet").is_visible():
                fails.append(f"{dev}: sheet did not close on Escape")
            pg.goto(base + "c/proxypics.html")
            pg.evaluate("document.querySelectorAll('details').forEach(d=>d.open=true)")
            fails += [f"{dev} card details open: {x}" for x in pg.evaluate(PROBE, {"budget": BUDGET, "phone": phone, "kind": "card"})["problems"]]
            fails += [f"{dev} card details open: {x}" for x in pg.evaluate(CENTRE, {"tol": 1.5})]
            pg.screenshot(path=f"{OUT}/{dev}-card-details-full.png", full_page=True)
            pg.goto(base + "compare.html?ids=nws")
            pg.select_option("#addp", "usgs-water")
            pg.wait_for_timeout(150)
            if pg.locator("#cmp thead th").count() != 3:
                fails.append(f"{dev}: adding a provider in compare didn't add a column")
            pg.locator("#cmp .rm").first.click()
            if pg.locator("#cmp thead th").count() != 2:
                fails.append(f"{dev}: removing a provider in compare didn't remove its column")
            fails += [f"{dev} {x}" for x in checks_js(pg, base, cards)]
            for e in errors:
                fails.append(f"{dev}: JS error: {e}")
            # the same pages with JavaScript off
            nj = b.new_context(**ctx, java_script_enabled=False).new_page()
            for name, url, kind in NOJS:
                nj.goto(base + url)
                res = nj.evaluate(PROBE, {"budget": BUDGET, "phone": phone, "kind": kind if kind != "finder" else "doc"})
                fails += [f"{dev} no-JS {name}: {x}" for x in res["problems"] + nojs_problems(nj, kind, len(ids))]
                nj.screenshot(path=f"{OUT}/{dev}-nojs-{name}.png")
                if dev == "iphone":
                    nj.screenshot(path=f"{OUT}/{dev}-nojs-{name}-full.png", full_page=True)
            if dev == "desk1440":  # values do not depend on the screen: check every built card once
                for c in cards:
                    nj.goto(base + f"c/{c['id']}.html")
                    fails += [f"no-JS card {c['id']}: {x}" for x in card_problems(nj, c)]
                nj.goto(base + "find.html")
                fails += [f"no-JS finder: {x}" for x in row_problems(nj, {c["id"]: c for c in cards})]
            b.close()
    seen = []
    for f in fails:
        if f not in seen:
            seen.append(f)
            print("FAIL", f)
    print(f"{len(seen)} problems" if seen else "QA clean", "·", len(states), "states x", len(DEVICES), "devices · screenshots in", OUT)
    sys.exit(1 if seen else 0)


if __name__ == "__main__":
    main()
