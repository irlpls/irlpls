"""Shared by tools/qa.py (the landing) and tools/qa_pages.py (every other page): is each button's label visually centred?

For every visible boxed control (a button, summary, [role=button] or link that paints a background or a border, so its
box can be seen), the box of what it shows (its text, plus any icon beside the text) must sit in the middle of the
control: vertically always, horizontally when the control centres or hugs its content (inline-*, text-align:center,
justify-content:center, or a box that only pads its label). Tolerance 1.5px. Rows, multi-line blocks and long labels
(over 8 words) are not buttons and are skipped. Inter's line box centre sits at its cap-height centre (both ~0.364em
above the baseline), so the text rect's centre is where the eye puts the label's middle.

    res = page.evaluate(CENTRE, {"tol": 1.5})   # -> ['off-centre button "Find data": text 2.0px low, 0.0px right', ...]
"""

CENTRE = r"""(B) => {
  const tol = (B && B.tol) || 1.5, out = [], W = innerWidth, H = innerHeight, R = e => e.getBoundingClientRect();
  const alpha = c => { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return c === 'transparent' ? 0 : 1;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean); return p.length > 3 ? parseFloat(p[3]) : 1 };
  const shown = el => { const d = el.closest('details:not([open])'); if (d && !(el.closest('summary') && el.closest('summary').parentElement === d)) return false;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < .05 || e.classList.contains('sr') || e.hidden) return false } return true };
  const boxed = cs => alpha(cs.backgroundColor) > .05 || cs.backgroundImage !== 'none'
    || ['Top', 'Right', 'Bottom', 'Left'].some(s => parseFloat(cs['border' + s + 'Width']) >= .5 && alpha(cs['border' + s + 'Color']) > .05 && cs['border' + s + 'Style'] !== 'none');
  const seen = new Set();
  const ACT = 'button, summary, [role=button], a[href], label';
  for (const e of document.body.querySelectorAll('*')) {
    if (seen.has(e) || /^(SCRIPT|STYLE|SVG|svg|PATH|path|IMG|INPUT|SELECT|TEXTAREA|OPTION|VIDEO|CANVAS|HTML|BODY|MAIN|SECTION|ARTICLE|UL|OL|LI|TABLE|TR|TD|TH|P|H1|H2|H3|H4|PRE|CODE|NAV|HEADER|FOOTER|DIALOG)$/.test(e.tagName) && !e.matches(ACT + ', p.cap')) continue;
    const cs = getComputedStyle(e), b = R(e);
    if (b.width < 8 || b.height < 8) continue;
    const act = e.matches(ACT), capsule = parseFloat(cs.borderTopLeftRadius) >= .4 * b.height && b.height <= 60;
    if (!act && !capsule) continue;
    if (!shown(e)) continue; seen.add(e);
    if (!boxed(cs) || cs.display === 'contents') continue;
    // what it shows: text rects, plus icons (svg, img, i, glyph spans) that are in the flow
    const rs = [], tw = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); let n, words = 0;
    while ((n = tw.nextNode())) { const el = n.parentElement; if (!n.textContent.trim() || !shown(el) || /^(SCRIPT|STYLE|OPTION)$/.test(el.tagName)) continue;
      const pos = getComputedStyle(el).position; if (pos === 'absolute' || pos === 'fixed') continue;
      words += n.textContent.trim().split(/\s+/).length;
      const rg = document.createRange(); rg.selectNodeContents(n); for (const x of rg.getClientRects()) if (x.width > .5 && x.height > .5) rs.push(x) }
    if (!rs.length || words > 8) continue;
    const lines = new Set(rs.map(x => Math.round(x.top + x.height / 2)));
    if ([...lines].some(a => [...lines].some(c => Math.abs(a - c) > 4))) continue;   // more than one line: a row, not a button
    const ic = [];
    for (const g of e.querySelectorAll('svg, img, i, .ic, .gl')) { if (!shown(g)) continue; const p = getComputedStyle(g).position;
      if (p === 'absolute' || p === 'fixed') continue; const x = R(g); if (x.width > .5 && x.height > .5) ic.push(x) }
    const all = rs.concat(ic), L = Math.min(...all.map(x => x.left)), Rr = Math.max(...all.map(x => x.right));
    const T = Math.min(...rs.map(x => x.top)), Bo = Math.max(...rs.map(x => x.bottom));   // vertical: the text itself
    const dy = (T + Bo) / 2 - (b.top + b.bottom) / 2, dx = (L + Rr) / 2 - (b.left + b.right) / 2;
    const hugs = /inline/.test(cs.display) || cs.textAlign === 'center' || cs.justifyContent === 'center'
      || (Rr - L) >= b.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth) - 2;
    const offY = Math.abs(dy) > tol, offX = hugs && Math.abs(dx) > tol;
    if (offY || offX) {
      const what = (e.textContent.trim().replace(/\s+/g, ' ').slice(0, 28) || e.getAttribute('aria-label') || e.tagName.toLowerCase());
      out.push('off-centre ' + e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/)[0] : '') +
        ' "' + what + '": text ' + Math.abs(dy).toFixed(1) + 'px ' + (dy > 0 ? 'low' : 'high') + ', ' + (hugs ? Math.abs(dx).toFixed(1) + 'px ' + (dx > 0 ? 'right' : 'left') : 'left-aligned') +
        ' (box ' + Math.round(b.width) + 'x' + Math.round(b.height) + ')') }
  }
  return out;
}"""
