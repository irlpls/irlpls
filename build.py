#!/usr/bin/env python3
"""Build the static IRLpls site from cards/*.json into site/. No dependencies.

    python3 build.py            private preview build (everything in src/, incl. src/pitch/ prototypes)
    python3 build.py --public   the irlpls.com build: no src/pitch/, no headlines page, no preview-only mic or portal links
"""
import json, html, pathlib, re, shutil, subprocess, urllib.parse

ROOT = pathlib.Path(__file__).parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from irlpls import health  # noqa: E402  (the daily read check, tools/healthcheck.py)
from irlpls.readiness import readiness_for, self_registers  # noqa: E402  (stdlib only; one rule for card pages, catalog and broker)
from irlpls.catalog import adapters as _adapters  # noqa: E402  (the same adapter facts catalog.json uses)
from irlpls import published, standing  # noqa: E402  (provider-published cards: tools/fetch_published.py; ranks: tools/bench_asks.json)
import os  # noqa: E402
ADAPTERS = _adapters()
SITE = pathlib.Path(os.environ.get("IRLPLS_SITE") or ROOT / "site")  # tests build a fixture site elsewhere
PUB = {"state": published.empty_state(), "eff": {}, "standing": {}}   # main() fills it
# `--public` (the irlpls.com build): leave out internal prototypes and preview-only material. The private preview build is unchanged.
PUBLIC_OMIT = {"pitch": "landing prototypes and demo pages with illustrative values", "headlines.html": "internal headline options",
               "fb.js": "preview-only feedback mic", "__fb.js": "preview-only feedback mic"}
PREVIEW_ONLY_RX = re.compile(r'<script[^>]*src="[^"]*fb\.js"[^>]*>\s*</script>|<a\b[^>]*href="/?(?:portal|decisions|board|pitch/)[^"]*"[^>]*>.*?</a>', re.S | re.I)
PUBLIC_LEAK_RX = re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b|\.ts\.net\b|//(localhost|127\.0\.0\.1)\b|(?<![\w.-])(?:/Users/[A-Za-z]|/home/[a-z]|/private/tmp/)")
ASSURANCES = [
    ("human", "Enrolled person", "Identity checked at enrolment"),
    ("application", "Device or app attestation", "App or device passed a server-side attestation"),
    ("camera", "Signed capture", "Signed by a conforming capture product"),
    ("time", "Time-stamp", "Existed no later than a stated time"),
    ("location", "Authenticated place", "Position authenticated, not only reported"),
    ("content", "Content integrity", "Unchanged since signing; shows what it claims"),
    ("corroboration", "Corroboration", "Confirmed by independent sources"),
]
KM2 = re.compile(r"(?<![\w/.:#=?&-])km2\b|(?<=\d/)km2\b")  # card text writes "km2"; pages show km² (never inside a URL)


def e(s, quote=True):
    return KM2.sub("km²", html.escape(s, quote))

GL = {"evidenced": '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3.5 8.4 2.9 2.8 6.1-6.4"/></svg>', "claimed": '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 9.2c1.2-1.6 2.4-1.6 3.6 0s2.4 1.6 3.6 0 1.6-1.4 2.8-1"/></svg>', "none": '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5.5 8h5"/></svg>'}

SEARCH_IC = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'


def page(title, body, depth=0, cls="", desc="Real-world data on demand, with provenance. An open protocol and router for agents.", card="", canon="", head="", prefix=None):
    p = "../" * depth if prefix is None else prefix
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta name="theme-color" content="#f6f8fb"><meta name="color-scheme" content="light"><link rel="canonical" href="https://irlpls.com/{canon}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}"><meta property="og:url" content="https://irlpls.com/{canon}"><meta property="og:image" content="https://irlpls.com/media/og.png"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{p}favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="{p}fonts.css"><link rel="stylesheet" href="{p}style.css">{head}</head>
<body class="{cls}"><a class="skip" href="#main">Skip to content</a><div id="root" data-base="{p}"{f' data-card="{e(card)}"' if card else ""}>{body}</div>
<script src="{p}match.js"></script><script src="{p}labels.js"></script><script src="{p}icons.js"></script><script src="{p}app.js" defer></script><script src="{p}bar.js"></script></body></html>'''


def nav(p="", here=""):
    """The landing page's header: Find data · irlpls · For agents, the wordmark centred."""
    cur = lambda k: ' aria-current="page"' if k == here else ""
    return (f'<header class="top ctr"><nav aria-label="Main"><a class="nl" href="{p}find.html"{cur("find")}>Find data</a>'
            f'<a class="brand" href="{p}index.html" aria-label="irlpls home">irl<i>pls</i></a><a class="nr" href="{p}agents.html"{cur("agents")}>For agents</a></nav></header>')


def foot(p="", extra=""):
    """The one footer on every page (src/index.html, chain.html, check.html and standard.html carry the same markup with p="";
    tests/test_ui.py pins them to this). extra: page-specific small print (credits), above the licence line."""
    links = (("standard.html", "Standard"), ("docs.html", "Docs"), ("providers.html", "For providers"), ("privacy.html", "Privacy"),
             ("glossary.html", "Glossary"), ("changelog.html", "Changelog"), ("governance.html", "Governance"))
    return ('<footer class="site-foot"><div class="ft"><p class="fd"><a class="fw" href="' + p + 'index.html" aria-label="irlpls home">irl<i>pls</i></a>'
            '<span>No data sold, no cut. Buy from providers directly.</span></p><nav class="fl" aria-label="Footer">'
            + "".join(f'<a href="{p}{h}">{t}</a>' for h, t in links) + '</nav></div>' + extra +
            '<p class="fm"><span>Code Apache-2.0 · Cards CC BY 4.0</span><a href="mailto:hello@irlpls.com">hello@irlpls.com</a><span>© irlpls maintainers</span></p></footer>')


# ---- card values. Every value comes from the card JSON; anything the card leaves out reads "Not stated".
# src/app.js has the same helpers for the finder rows and compare; tools/qa_pages.py checks both against the JSON.
SHORT = {"human": "Person", "application": "App", "camera": "Camera", "time": "Time", "location": "Place", "content": "Content", "corroboration": "Cross-check"}
LEVEL = {"evidenced": "Evidenced", "claimed": "Claimed", "reported": "Reported", "none": "Not stated"}  # the one set of level words
DEFAULT_PROOF = ["time", "location", "content"]


def plain_name(c):
    return re.sub(r"\s*\(.*\)", "", c["name"])


def money(n):
    return f"${n:g}" if n < 1 else f"${n:.2f}" if n % 1 else f"${int(n)}"


def unit(c):
    u = re.sub(r"^per ", "", str((c.get("pricing") or {}).get("unit") or ""))
    return re.split(r"\s*\(|;|,\s", u)[0].strip()


def price_text(c):
    k, usd = (c.get("access") or {}).get("cost"), (c.get("pricing") or {}).get("from_usd")
    if k == "free":
        return "Free"
    if k == "free with key":
        return "Free with a key"
    if k == "freemium":
        return "Free tier"
    if usd:
        return money(usd) + (" / " + unit(c) if unit(c) else "")
    return {"quote": "On request", "paid": "Price not stated"}.get(k, "Not stated")


def mode_text(c):
    m = (c.get("delivery") or {}).get("modes") or []
    return "Live, made to order" if len(m) > 1 else "Live" if m == ["live"] else "Made to order" if m else "Not stated"


def speed_text(c):
    s = (c.get("delivery") or {}).get("speed")
    return s if s and s != "unknown" else "Speed not stated"


def where(c):
    r = [x.split("(")[0].split(",")[0].strip() for x in (c.get("coverage") or {}).get("regions") or []]
    r = [x for x in r if x]
    return (", ".join(r[:2]) + f" +{len(r) - 2}") if len(r) > 2 else ", ".join(r) or "Not stated"


def right(v):
    return {"allowed": "Allowed", "forbidden": "Not allowed"}.get(v, "Not stated")


def links(c):
    """True if an agent can link the person's account with one OAuth consent: the client registers itself (a Client ID
    Metadata Document, or the deprecated dynamic client registration)."""
    return any(self_registers(m) for m in (c.get("api") or {}).get("auth") or [])


def key_text(c):
    k = (c.get("access") or {}).get("key_required")
    s = "Not stated" if k is None else "Required" if k else "Not needed"
    return s + ", or link an account" if k and links(c) else s


def glyph(level):
    return f'<i aria-hidden="true">{GL[level]}</i>'


def pf(c, k):
    lv = c["verification"][k]["level"]
    return f'<span class="pf {lv}">{glyph(lv)}{SHORT[k]}<span class="sr">: {lv}</span></span>'


def wb(s):
    return re.sub(r"([/?&=,])", r"\1<wbr>", e(s))


def host(u):
    return re.sub(r"/$", "", re.sub(r"^https?://", "", str(u)))


def auth_text(c):
    a = c.get("api") or {}
    ms = [m for m in a.get("auth") or [] if m.get("type") not in (None, "none")]
    if not ms:
        return "No key"
    def one(m):
        t = m["type"]
        if t == "api_key_header":
            return "API key" + (" · " + m["header"] if m.get("header") else "")
        if t == "oauth2":
            return "OAuth" + (" (an agent can register itself)" if self_registers(m) else "")
        return {"bearer": "Bearer token", "basic": "Basic auth", "api_key_query": "API key in the URL"}.get(t, "Account credentials")
    rank = lambda m: (0 if self_registers(m) else 1 if m["type"] == "api_key_header" else 2)
    ms = sorted(ms, key=rank)  # the easiest for an agent first; the rest are in the JSON
    s = one(ms[0]) + (f" +{len(ms) - 1} more" if len(ms) > 1 else "")
    return "Optional: " + s if (c.get("access") or {}).get("key_required") is False else s


PAY = {"card": "card", "hosted_checkout": "checkout page", "prepaid_balance": "prepaid balance", "invoice": "invoice",
       "x402": "x402", "mpp": "MPP", "ucp_checkout": "UCP checkout", "acp_checkout": "ACP checkout", "crypto": "crypto"}


def api_block(c):
    a = c.get("api")
    if not a or not a.get("available"):
        return '<p class="none-api">No public API listed.</p>'
    access = "Self-serve" if a.get("self_serve") else "By agreement" if a.get("self_serve") is False else "Not stated"
    rows = [("base", wb(a["base_url"]) if a.get("base_url") else "Not stated"), ("auth", e(auth_text(c))), ("access", access)]
    if c.get("adapter"):
        rows.append(("adapter", f'<code>{e(c["adapter"])}</code>'))
    m = a.get("mcp") or {}
    remote = m.get("remote") if str(m.get("remote") or "").startswith("http") else None
    if remote or m.get("registry_name"):  # a real server only; "none found" notes stay in the JSON
        mcp = remote or m.get("registry_name")
        rows.append(("mcp", wb(mcp) + ("" if m.get("first_party", True) else " (community)")))
    pay = c.get("payment") or {}
    if pay.get("methods"):
        rows.append(("pay", e(", ".join(PAY.get(x, x) for x in pay["methods"]) + (" · an agent can pay" if pay.get("agent_can_pay") else ""))))
    if a.get("docs_url"):
        rows.append(("docs", f'<a href="{e(a["docs_url"])}" target="_blank" rel="noopener">{e(host(a["docs_url"]).split("/")[0])}</a>'))
    h = '<div class="api"><dl>' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows) + "</dl>"
    ep = a.get("endpoints") or []
    if ep:
        h += '<ul class="eps">' + "".join(f'<li><b>{e(x.get("method") or "")}</b> {wb(x.get("path") or "")}</li>' for x in ep[:6])
        h += (f'<li class="more-ep">+{len(ep) - 6} more in the JSON</li>' if len(ep) > 6 else "") + "</ul>"
    return h + "</div>"


def ready_line(c):
    """How close this provider is to "just say yes" (irlpls.readiness), in one short line under the API heading."""
    r = readiness_for(c, ADAPTERS.get(c["id"]))  # exactly catalog.json providers[id].readiness (irlpls/catalog.py)
    return f'<p class="rdy" data-level="{r["level"]}">{e(r["line"])}</p>'


def use_line(c):
    """How it is used, and that it is real: one line under the description."""
    a, ac = c.get("api") or {}, c.get("access") or {}
    if a.get("available"):
        kr = ac.get("key_required")
        bits = ["Self-serve API" if a.get("self_serve") else "API by agreement" if a.get("self_serve") is False else "API",
                "no key" if kr is False else "key required" if kr else "key not stated"]
        if c.get("adapter"):
            bits.append("irlpls adapter")
        h = ", ".join(bits) + (f' · <a href="{e(a["docs_url"])}" target="_blank" rel="noopener">docs</a>' if a.get("docs_url") else "")
    else:
        h = "No public API listed"
    return f'<p class="use">{h}</p>'


def card_article(c, p="../"):
    v = c["verification"]
    lic = c.get("licensing") or {}
    ent, st = pub_entry(c["id"])
    mine = c.get("status") == "provider-maintained" and st in published.SHOWN
    h = f'<article class="cardv">{pub_badge(c, ent, st)}<h1 class="ttl" id="card-h">{e(c["name"])}</h1><p class="lede">{e(c.get("short") or c.get("summary") or "")}</p>{use_line(c)}'
    h += ('<dl class="spec">'
          f'<div><dt>Mode</dt><dd>{e(mode_text(c))} · {e(speed_text(c))}</dd></div>'
          f'<div><dt>Coverage</dt><dd>{e(where(c))}</dd></div>'
          f'<div><dt>Price</dt><dd>{e(price_text(c))}</dd></div>'
          f'<div><dt>Key</dt><dd>{e(key_text(c))}</dd></div>'
          f'<div><dt>Resale</dt><dd>{right(lic.get("resale"))}</dd></div>'
          f'<div><dt>AI training</dt><dd>{right(lic.get("ai_training"))}</dd></div></dl>')
    if mine:
        h += provider_sections(c)
    # one proof vocabulary: the eight stages; each row folds in the card fields (guarantees) it is read from
    h += stages_block(c)
    if mine:
        h += evidence_line(ent) + changed_block(ent)
    if c.get("attestations"):
        h += '<h2 class="sec">Independent checks</h2><ul class="srcl">' + "".join(
            f'<li>{e(x.get("verifier", ""))}: {e(x.get("scope", ""))} <a href="{e(x.get("url", ""))}" target="_blank" rel="noopener">source</a></li>' for x in c["attestations"]) + "</ul>"
    h += '<h2 class="sec">API</h2>' + ready_line(c) + api_block(c) + rate_limit_line(c) + read_check_line(c)
    src = c.get("sources") or []
    h += '<h2 class="sec">Sources</h2>'
    h += (f'<details class="dt"><summary>{len(src)} {"source" if len(src) == 1 else "sources"}</summary><ol class="srcl">' + "".join(
        f'<li>{e(x.get("claim", ""))} <a href="{e(x["url"])}" target="_blank" rel="noopener">{e(host(x["url"]).split("/")[0])}</a></li>' for x in src) + "</ol></details>") if src else '<p class="none-api">No sources listed.</p>'
    notes = [("About", c.get("summary")), ("Price", (c.get("pricing") or {}).get("notes")), ("Coverage", (c.get("coverage") or {}).get("notes")),
             ("Timing", (c.get("delivery") or {}).get("basis") or (c.get("latency") or {}).get("notes")), ("Licence", lic.get("summary"))]
    h += '<details class="dt"><summary>Notes</summary>' + "".join(f"<p><b>{k}.</b> {e(t)}</p>" for k, t in notes if t) + "</details>"
    who = "Compiled from public sources" if c.get("status") == "unclaimed" else "Published by the provider"
    if mine:
        h += badge_block(c)
    elif c.get("status") == "unclaimed" and not ent:
        h += (f'<p class="claim">Compiled from public sources. Is this your company? '
              f'<a href="{p}check.html?id={e(c["id"])}">Publish your card&nbsp;›</a></p>')
    h += (f'<p class="foot"><span>{who} · updated {e(c.get("updated") or "date not stated")}</span><span class="links"><a href="{p}cards/{c["id"]}.json">JSON</a>'
          + (f'<a href="{e(c["provider_url"])}" target="_blank" rel="noopener">Site</a>' if c.get("provider_url") else "")
          + f'<a href="{p}compare.html?ids={c["id"]}">Compare</a></span></p>'
          + (f'<p class="readfrom">Read from <span class="u">{wb(ent["url"])}</span> · sha256 <code title="{e(ent.get("sha256") or "")}">{e((ent.get("sha256") or "")[:12])}…</code></p>' if mine and ent.get("url") else "")
          + '</article>')
    return h


# ---- provider-published cards (irlpls/published.py, tools/fetch_published.py): the badge and the sections a provider sees
CHECK_IC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>'


def day(iso):
    """'29 Oct' from an ISO date or time; '' when unreadable."""
    import datetime
    try:
        return datetime.date.fromisoformat(str(iso)[:10]).strftime("%-d %b")
    except ValueError:
        return ""


def pub_entry(cid):
    """(published.json entry or None, effective state or None) for a card id."""
    return (PUB["state"].get("cards") or {}).get(cid), PUB["eff"].get(cid)


def pub_badge(c, ent, st):
    """Above the name: 'Published by <Provider> from <domain> · checked <date>' (ink, never green: green means evidenced).
    Grey when irlpls could not use the latest read; then it says why and which card is shown."""
    if not ent:
        return ""
    who, dom = e(plain_name(c)), e(ent.get("domain") or "")
    if st == "live":
        return (f'<p class="pubb">{CHECK_IC}<span><b>Published by {who}</b> from {dom} · checked {day(ent.get("last_read"))}</span></p>')
    shown = f'showing the copy from {day(ent.get("last_ok"))}' if st == "stale" else "showing the compiled card"
    why = {"expired": f'the provider\'s card expired on {day(ent.get("expires"))}'}.get(st) or ent.get("reason") or "not current"
    return f'<p class="pubb old"><span><b>Published by {who}</b> from {dom} · {e(why)}; {shown}</span></p>'


def provider_sections(c):
    """What an agent says, where it ranks and what would move it up: all computed (irlpls/standing.py), never written."""
    s = PUB["standing"].get(c["id"]) or {}
    h = ""
    say = s.get("says")
    if say:
        h += ('<h2 class="sec">What an agent says</h2><blockquote class="say">'
              f'<p>{e(say["line"])}</p>' + (f'<p>{e(say["ready"])}</p>' if say.get("ready") else "") + f'<p>{e(say["proof"])}</p></blockquote>')
    rows = s.get("ranks") or []
    if rows:
        li = "".join(
            f'<li><span>{e(r["label"])}</span><b>#{r["pos"]} <small>of {r["of"]}</small></b></li>' if r["pos"] else
            f'<li class="no"><span>{e(r["label"])}</span><b>{"Not covered" if not r["covered"] else "Not a match"}</b></li>' for r in rows)
        h += f'<h2 class="sec">Where it ranks</h2><ul class="ranks">{li}</ul>'
    up = s.get("move_up")
    if up:
        w = up["words"]
        h += (f'<p class="up">Evidence {e(w)}: #{up["before"]} → #{up["after"]} for {e(up["label"])}. '
              f'<a href="../{up["how"]}">How</a></p>')
    return h


def evidence_line(ent):
    """The daily sample-pack check, one quiet line: verified, or 'didn't verify on <date>'. Never changes a level."""
    ev = [x for x in ent.get("evidence") or [] if x.get("verdict") in ("verified", "failed")]
    if not ev:
        return ""
    bad = [x for x in ev if x["verdict"] == "failed"]
    t = (f"Evidence didn't verify on {day(bad[0]['checked_at'])}" if bad else f"Evidence verified {day(ev[0]['checked_at'])}")
    return f'<p class="rl ev" data-ok="{str(not bad).lower()}">{e(t)}</p>'


def changed_block(ent):
    rows = ent.get("changed") or []
    if not rows:
        return ""
    more = ent.get("changed_more") or 0
    li = "".join(f'<li><b>{e(r["field"])}</b> · {e(r["before"])} → {e(r["after"])}</li>' for r in rows)
    return (f'<h2 class="sec">Changed from the compiled card</h2><ul class="chg">{li}</ul>'
            + (f'<p class="rl">+{more} more in <a href="../published.json">published.json</a></p>' if more else ""))


def badge_block(c):
    snip = f'<a href="https://irlpls.com/c/{c["id"]}.html"><img src="https://irlpls.com/badge/{c["id"]}.svg" alt="{plain_name(c)} on irlpls"></a>'
    return (f'<h2 class="sec">Badge for your site</h2><div class="bdg"><img src="../badge/{e(c["id"])}.svg" width="236" height="28" alt="{e(plain_name(c))} on irlpls">'
            f'<code>{e(snip)}</code></div>')


def badge_svg(name, ent, st):
    """/badge/<id>.svg: 'irlpls | Capture Card · checked 29 Oct'. Grey in every other state. No score, no rank, no
    'verified', no tracking."""
    if st == "live":
        txt, ink, fill, edge = f'Capture Card · checked {day(ent.get("last_read"))}', "#3d4654", "#fff", "#c9d0da"
    else:
        txt = {"expired": "Card expired", "stale": f'Card not read · {day(ent.get("last_read"))}', "rejected": "Card not valid",
               "gone": "Card withdrawn"}.get(st, "Card not current")
        ink, fill, edge = "#6b7480", "#eef1f5", "#d5dae1"
    w = 54 + int(len(txt) * 6.6) + 12
    lab = html.escape(f"{name} on irlpls: {txt}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="28" viewBox="0 0 {w} 28" role="img" aria-label="{lab}">'
            f'<title>{lab}</title><rect x=".5" y=".5" width="{w - 1}" height="27" rx="13.5" fill="{fill}" stroke="{edge}"/>'
            f'<text x="12" y="18.5" font-family="Inter,system-ui,sans-serif" font-size="12.5" font-weight="700" fill="#101418" letter-spacing="-.4">irl<tspan fill="#5b6574">pls</tspan></text>'
            f'<line x1="44" y1="7" x2="44" y2="21" stroke="#dfe4ea"/>'
            f'<text x="53" y="18.5" font-family="Inter,system-ui,sans-serif" font-size="12" fill="{ink}">{html.escape(txt)}</text></svg>\n')


def pub_strip(cards):
    """Find data: 'Recently published by providers', a quiet section AFTER the full list (the owner, 2026-10-29: provider
    promotion never sits above a user's task). src/app.js hides it whenever there is a query or a filter, so a user's
    results are all they see. Evidenced (a sample pack verified) first, by the date it verified;
    then by the last material change (irlpls/published.py: at most once per 30 days). Hidden when there are none."""
    by = {c["id"]: c for c in cards}
    rows = []
    for cid, ent in (PUB["state"].get("cards") or {}).items():
        if PUB["eff"].get(cid) != "live" or cid not in by:
            continue
        ver = [x for x in ent.get("evidence") or [] if x.get("verdict") == "verified"]
        rows.append((bool(ver), (ver[0]["checked_at"] if ver else ent.get("listed_at") or ""), cid, ent, ver))
    if not rows:
        return ""
    rows.sort(key=lambda r: (r[1], r[2]), reverse=True)   # newest first...
    rows.sort(key=lambda r: not r[0])                      # ...with evidenced cards ahead (a stable sort keeps the dates)
    def li(r):
        c = by[r[2]]
        when = (f'<em>Evidenced</em>' if r[0] else "") + e(day(r[1]))
        return (f'<li><a class="pubrow" href="c/{e(c["id"])}.html"><span class="nm">{e(plain_name(c))}</span>'
                f'<span class="sub">{e(c.get("short") or "")}</span><span class="when">{when}</span></a></li>')
    top, rest = rows[:3], rows[3:]
    h = f'<section class="pubs" aria-labelledby="pubh"><h2 id="pubh">Recently published by providers</h2><ul class="list">{"".join(li(r) for r in top)}</ul>'
    if rest:
        h += f'<details class="pmore"><summary>All published ({len(rows)})</summary><ul class="list">{"".join(li(r) for r in rest)}</ul></details>'
    return h + '<a class="plink" href="providers.html">Publish yours&nbsp;›</a></section>'


# ---- the eight stages, from a card's seven guarantees: mirrors stageLevels() in src/match.js (tests pin the two) ----
STAGE_TITLES = ["Real person", "Captured locally", "Genuine device", "Sealed capture", "Known timestamp", "Tracked in transit",
                "Independently verified", "Delivered to you"]
STAGE_FROM = [["human"], ["camera", "location"], ["application"], ["camera", "content"], ["time"], [], ["corroboration"], []]
STAGE_FIELDS = {"camera": ("standard", "method"), "content": ("method",), "time": ("method",)}  # time.unknown never lights a stage
STAGE_PROVENANCE = {5: "custody", 7: "receipt"}
PROV_SHORT = {"custody": "Custody record", "receipt": "Delivery receipt"}  # Tracked in transit / Delivered to you: the card's optional provenance block (STANDARD 3.1)
SEAL_RX = re.compile(r"\bc2pa\b|content credentials|\bmanifests?\b|\bsigned at capture\b|\b(?:captures?|files?|images?|photos?|frames?|videos?|clips?)\s+(?:is |are )?(?:cryptographically )?signed\b", re.I)
ATTEST_RX = re.compile(r"rfc ?3161|time[- ]?stamp(?:ing)? authority|\btsa\b|transparency log|\bmerkle\b|on[- ]chain|\banchor(?:ed|s)?\b|\bledger\b|\btrace receipt\b", re.I)
REPORTS_RX = re.compile(r"\b(?:time ?stamps?|timestamped|observation time|capture time|acquisition|acquired|origin time|date and time|datetime|captured_at|utc)\b", re.I)
STAGE_SAYS = {"evidenced": "Evidenced", "claimed": "Claimed", "reported": "Reported (a time is stated, not attested)", "none": "Not stated"}
STAGE_CLAIMED_7 = "Claimed (the provider says it was checked; no public evidence)"
RANK = {"none": 0, "reported": 1, "claimed": 2, "evidenced": 3}



HEALTH = health.load()   # tools/healthcheck.py's last daily read check (irlpls/data/health.json, gitignored)
LIVE = {}                # the landing's 30-minute check (src/media/live/health.json); main() loads it


def read_check_line(c):
    """One quiet line from the newest check of either source: 'Read check: worked 28 Oct' or 'Read check: failed 28 Oct (404)'."""
    r = health.latest(c["id"], HEALTH, LIVE)
    if not r:
        return ""
    t = re.sub(r"\s+for https?://.*$", ")", health.line(c["id"], HEALTH, LIVE), flags=re.S)  # the status, never the raw URL and body
    t = t if len(t) <= 90 else t[:88].rstrip() + "…)"
    return f'<p class="rl rc" data-ok="{str(r["ok"]).lower()}">{e(t)}</p>'


def limit_text(r):
    """One structured limit in words: '50 requests per minute, per IP'. An unknown scope says nothing."""
    per = {1: "second", 60: "minute", 3600: "hour", 86400: "day"}
    if not (r.get("requests") and r.get("per_seconds")):
        return ""
    u = per.get(r["per_seconds"])
    t = f'{r["requests"]:,} requests per {u}' if u else f'{r["requests"]:,} requests per {r["per_seconds"]:,} s'
    sc = {"per-ip": "per IP", "per-key": "per key", "per-account": "per account", "global": "for everyone"}.get(r.get("scope"))
    return t + (", " + sc if sc else "")


def rate_limit_line(c):
    """The card's rate limit in ONE plain line (STANDARD 2.5): the structured access.rate_limit when the card has it, else the
    free-text api.rate_limits; nothing when the card states neither."""
    r = (c.get("access") or {}).get("rate_limit")
    if not r:
        t = str((c.get("api") or {}).get("rate_limits") or "").strip()
        return f'<p class="rl"><b>Rate limit.</b> {e(t[:1].upper() + t[1:])}</p>' if t else ""
    bits = [x for x in [limit_text(r)] if x]
    wk = r.get("with_key") or {}
    if limit_text(wk):
        bits.append(limit_text(wk) + " with a key")
    if r.get("batch_max"):
        bits.append(f'up to {r["batch_max"]:,} per call')
    if r.get("cache_seconds"):
        bits.append(f'updates about every {max(1, r["cache_seconds"] // 60)} min, so a newer ask reuses the last reading')
    text = "; ".join(bits) + (". " if bits else "") + (r.get("notes") or "")
    src = f' <a href="{e(r["source_url"])}" target="_blank" rel="noopener">source</a>' if r.get("source_url") else ""
    text = text.strip()
    return f'<p class="rl"><b>Rate limit.</b> {e(text[:1].upper() + text[1:])}{src}</p>'


def stage_levels(c):
    """Each stage from the card, honestly: Sealed capture only with a stated signing mechanism; Known timestamp only with an
    attested time ("reported" when the card only reports one); see src/index.html for the full rules."""
    v = c.get("verification") or {}
    lvl = lambda g: (g or {}).get("level") or "none"  # noqa: E731

    def both(gs):
        ls = [lvl(g) for g in gs]
        if not ls or all(x == "none" for x in ls):
            return "none"
        return "evidenced" if all(x == "evidenced" for x in ls) else "claimed"
    def weakest(gs):  # a stage needing every input is only as strong as its weakest one
        ls = [lvl(g) for g in gs]
        return "none" if "none" in ls else "claimed" if "claimed" in ls else "evidenced"
    seal = [g for k, g in (("camera", v.get("camera")), ("content", v.get("content"))) if g and lvl(g) != "none"
            and ((k == "camera" and str(g.get("standard") or "").lower() == "c2pa") or SEAL_RX.search(g.get("method") or ""))]
    t = v.get("time") or {}
    time = lvl(t) if lvl(t) != "none" and ATTEST_RX.search(t.get("method") or "") else \
        "reported" if lvl(t) != "none" or REPORTS_RX.search(t.get("method") or "") else "none"
    pv = c.get("provenance") or {}
    return [lvl(v.get("human")), weakest([v.get("camera"), v.get("location")]), lvl(v.get("application")), both(seal), time,
            lvl(pv.get("custody")), lvl(v.get("corroboration")), lvl(pv.get("receipt"))]


def stage_says(i, level):
    """A stage's level in the one set of words (Evidenced / Claimed / Reported / Not stated), with the reason where the word
    alone would mislead. Mirrors stageSays in src/app.js and says() in src/index.html (tests pin all three)."""
    return STAGE_CLAIMED_7 if level == "claimed" and i == 6 else STAGE_SAYS[level]


def stage_mark(lv):
    """One stage's dot class (IRLMatch.stageMark): green evidenced, blue claimed, ink ring reported, grey ring not stated."""
    return {"evidenced": "e", "claimed": "c", "reported": "r"}.get(lv, "")


def stage_dots(c, hidden=False):
    """The landing's eight stage dots (src/index.html dots()): filled green evidenced, blue claimed, ink ring reported, grey ring otherwise."""
    ls = stage_levels(c)
    lab = "; ".join(f"{STAGE_TITLES[i]}: {stage_says(i, lv)}" for i, lv in enumerate(ls))
    a = 'aria-hidden="true"' if hidden else f'role="img" aria-label="{e(lab)}"'
    return f'<span class="dots" {a}>' + "".join(f'<i class="{stage_mark(lv)}"></i>' for lv in ls) + "</span>"


def stage_tally(c, short=False):
    """'1 of 8 links claimed · 0 evidenced' (short, for rows beside their dots: '1 claimed · 0 evidenced'); mirrors
    IRLMatch.stageTally in src/match.js. A reported time counts as neither."""
    ls = stage_levels(c)
    cl, ev = ls.count("claimed"), ls.count("evidenced")
    return f"{cl} claimed · {ev} evidenced" if short else f"{cl} of {len(ls)} links claimed · {ev} evidenced"


def nw_tally(t):
    """A tally in HTML whose parts wrap whole: '1 of 8 links claimed' / '· 0 evidenced'."""
    a, b = t.split(" · ")
    return f'<span class="nw">{e(a)}</span> <span class="nw">· {e(b)}</span>'


def stage_detail(c, i, lv):
    """A stage row's detail: the card fields (guarantees) it is read from, each with its level, method, public evidence and
    what is still open; and, when the stage reads lower than its best field, the rule that holds it there."""
    if STAGE_FROM[i]:
        parts = [(SHORT[k], (c.get("verification") or {}).get(k) or {}) for k in STAGE_FROM[i]]
    else:
        k = STAGE_PROVENANCE[i]
        parts = [(PROV_SHORT[k] + " (optional field)", (c.get("provenance") or {}).get(k) or {})]
    h = ""
    for name, g in parts:
        gl = g.get("level") or "none"
        h += (f'<p><b>{e(name)}</b> · {LEVEL[gl]}' + (f'. {e(g["method"])}' if g.get("method") else "")
              + (f' <a href="{e(g["evidence_url"])}" target="_blank" rel="noopener">public evidence</a>' if g.get("evidence_url") else "") + "</p>"
              + (f'<p class="unk">Still open: {e(g["unknown"])}</p>' if g.get("unknown") else ""))
    best = max((RANK[g.get("level") or "none"] for _, g in parts), default=0)
    if RANK[lv] < best or (i == 4 and lv == "reported"):
        h += '<p class="rule">' + {1: "Needs both: the weaker one counts.",
                                    3: "Counts only with a stated signing mechanism (C2PA, a manifest).",
                                    4: "A time stated, not attested (RFC 3161, a log): Reported."}.get(i, "") + "</p>"
    return f'<div class="sd">{h}</div>'


def stages_block(c):
    """Card pages: the eight stages, the one proof vocabulary. Each row says its level and folds in the card fields it is read
    from (STANDARD Section 3 calls them guarantees), so the page never shows a second tally."""
    who = "Stated by the provider" if c.get("status") == "provider-maintained" else "Compiled from public sources, unclaimed"
    ls = stage_levels(c)
    rows = "".join(
        f'<li class="{lv}"><span>{i + 1}. {STAGE_TITLES[i]}</span><b>{LEVEL[lv]}</b>{stage_detail(c, i, lv)}</li>'
        for i, lv in enumerate(ls))
    return (f'<h2 class="sec">Eight links</h2><p class="stg-t">{stage_dots(c, True)}<span>{nw_tally(stage_tally(c))}</span></p>'
            f'<p class="stg-l">{e(who)}. <a href="../chain.html">What they mean</a></p>'
            f'<details class="dt stg"><summary>Each link</summary><ol class="stg-o">{rows}</ol></details>')


def card_page(c):
    body = f'{nav("../")}<main id="main" class="page"><div id="label">{card_article(c)}</div></main>{foot("../")}'
    return page(f'{c["name"]} · Capture Card · irlpls', body, 1, desc=c.get("short") or c["summary"][:200], card=c["id"], canon=f'c/{c["id"]}.html')


def finder_row(c):
    mark = f'<span class="pmk">{CHECK_IC}Published</span>' if c.get("status") == "provider-maintained" else ""
    return (f'<li><a class="row" data-id="{e(c["id"])}" href="c/{e(c["id"])}.html"><span class="main"><span class="nm">{e(plain_name(c))}{mark}</span>'
            f'<span class="sub">{e(mode_text(c))} · {e(speed_text(c))} · {e(price_text(c))}</span></span><span class="proof">{stage_dots(c)}<span class="tl">{stage_tally(c, True)}</span></span></a></li>')


STAGE_KEY = '<span class="kd"><i class="e"></i>Evidenced</span><span class="kd"><i class="c"></i>Claimed</span><span class="kd"><i class="r"></i>Reported</span><span class="kd"><i></i>Not stated</span>'


def index_page(cards):
    """The finder. Without JavaScript it is the full provider list, rendered here from the same cards as cards.json."""
    rows = "".join(finder_row(c) for c in sorted(cards, key=lambda c: plain_name(c).lower()))
    body = f'''{nav(here="find")}
<main id="main" class="finder">
<h1 class="hl all" data-t="Find data">Find data</h1>
<p class="lede-b">Every provider, link by link.</p>
<noscript><p class="nojs">Search needs JavaScript. All providers are listed below.</p></noscript>
<div class="search">{SEARCH_IC}<input id="q" type="search" autocomplete="off" enterkeyhint="search" aria-label="Type anything you need: what, where, how soon" placeholder="Type anything" aria-describedby="meta"><button type="button" class="x" id="clr" aria-label="Clear search" hidden>&times;</button></div>
<p class="examples" id="ex" aria-label="Example requests"><span>Try:</span><button type="button" data-q="river level in Texas right now">river level, Texas</button><i aria-hidden="true">·</i><button type="button" data-q="storefront photos in Lisbon with proof of when and where">storefront, Lisbon</button><i aria-hidden="true">·</i><button type="button" data-q="drone survey of a construction site in Canada">drone survey</button></p>
<div class="chips" id="filters" role="group" aria-label="Filters"><button type="button" aria-pressed="false" data-f="live">Live</button><button type="button" aria-pressed="false" data-f="commission">Made to order</button><button type="button" aria-pressed="false" data-f="free">Free</button></div>
<p class="read" id="parsed" aria-label="How the request was read" hidden></p>
<div class="notes" id="notes" aria-live="polite"></div>
<section class="best" id="best" aria-label="Best match" aria-live="polite" hidden></section>
<div class="lhead"><p class="meta count" id="meta" role="status" aria-live="polite">{len(cards)} providers</p><p class="key" aria-hidden="true">{STAGE_KEY}</p></div>
<ul id="grid" class="list" aria-label="Providers">{rows}</ul>
<button type="button" class="linkish more" id="more" hidden></button>
{pub_strip(cards)}
</main>{foot()}'''
    head = '<noscript><style>.search,.examples,.chips,#parsed,#more{display:none!important}</style></noscript><script src="places.js"></script><script src="live.js"></script><script src="callpane.js"></script>'
    return page("Find data · irlpls", body, 0, "p-finder", desc="Find who can capture real-world data, and what each provider evidences or only claims.", canon="find.html", head=head)


# ---------- the ready call per provider (Find data's "What irlpls sends for you", src/callpane.js layer 2) ----------
# From each adapter's own prepare() (the call the library and the broker hand an agent), for a generic ask. Built offline;
# nothing is sent and no credential is filled in (keys stay named placeholders). Where the call only puts the ask's point,
# words and place in as they are, they become {LAT} {LON} {NEED} {PLACE} and the page fills them from the person's ask;
# where it derives from them (a box around the point), the call is shown for one example place, and says so.
_RC_PT = ((11.1111, 22.2222), (33.3333, 44.4444))
_RC_NEED, _RC_PLACE = "ZZNEEDZZ", "ZZPLACEZZ"
_RC_EXAMPLE = {"us": (30.27, -97.74, "Austin, TX"), "gb": (51.51, -0.13, "London"), "de": (50.94, 6.96, "Köln"), "fi": (60.17, 24.94, "Helsinki"),
               "fr": (48.86, 2.35, "Paris"), "nz": (-41.29, 174.78, "Wellington"), "at": (48.21, 16.37, "Vienna")}


def _rc_tokens(v, k):
    """The call with the sentinel point `k` (0 or 1), words and place written as {LAT} {LON} {NEED} {PLACE}."""
    lat, lon = _RC_PT[k]
    if isinstance(v, dict):
        return {a: _rc_tokens(b, k) for a, b in v.items()}
    if isinstance(v, list):
        return [_rc_tokens(b, k) for b in v]
    if isinstance(v, float) and abs(v - lat) < 1e-6:
        return "{LAT}"
    if isinstance(v, float) and abs(v - lon) < 1e-6:
        return "{LON}"
    if isinstance(v, str):
        for x, t in ((lat, "{LAT}"), (lon, "{LON}")):
            s = f"{x:.4f}"
            v = re.sub(re.escape(s[:-1]) + r"\d*", t, v)
        return v.replace(_RC_NEED, "{NEED}").replace(_RC_PLACE, "{PLACE}")
    return v


def _rc_keep(pc):
    """The parts of a prepared call the page shows: method, URL, headers, body, the key (named) and other placeholders."""
    keep = {k: pc[k] for k in ("operation", "method", "url", "headers", "body", "auth", "placeholders", "docs_url", "notes") if pc.get(k) not in (None, {}, [])}
    keep["safe_to_run_without_approval"] = bool(pc.get("safe_to_run_without_approval"))
    return keep


def ready_calls(cards):
    """{card id: ready call template} for every in-scope card whose adapter prepares one."""
    from irlpls import adapters as lib
    out = {}
    stamp = re.compile(r"\d{4}-\d\d-\d\dT[\d:%A-F]+Z?|\b\d{12,}\b")   # times a call computes when it is built (starttime, from=)
    for c in cards:
        a = lib.get(c["id"])
        if not a or not callable(getattr(a, "prepare", None)):
            continue
        route = "live" if "live" in ((c.get("delivery") or {}).get("modes") or []) else "commission"
        def prep(lat, lon, need=_RC_NEED, place=_RC_PLACE):
            try:
                pc = a.prepare({"need": need, "text": need, "place": place, "lat": lat, "lon": lon, "quantity": 1, "mode": route}, route)
            except Exception:
                return None
            return pc if isinstance(pc, dict) and pc.get("url") else None
        p = [prep(*_RC_PT[k]) for k in (0, 1)]
        if not p[0]:
            continue
        t = [_rc_tokens(p[k], k) for k in (0, 1)]
        if stamp.sub("", json.dumps(t[0], sort_keys=True)) == stamp.sub("", json.dumps(t[1], sort_keys=True)):
            rc = _rc_keep(t[0])
        else:   # derived from the point: one example place, named
            cc = next((x.lower() for x in ((c.get("coverage") or {}).get("areas") or []) if isinstance(x, str) and x.lower() in _RC_EXAMPLE), "us")
            lat, lon, name = _RC_EXAMPLE[cc]
            pc = prep(lat, lon, place=name)
            if not pc:
                continue
            rc = _rc_keep(pc)
            rc = json.loads(json.dumps(rc).replace(_RC_NEED, "{NEED}"))
            rc["example"] = name
        out[c["id"]] = rc
    return out


def add_ready_calls(cards):
    """catalog.json providers[id].ready_call: the adapter's prepared call for a generic ask (see ready_calls)."""
    p = SITE / "catalog.json"
    cat = json.loads(p.read_text())
    rc = ready_calls(cards)
    for cid, call in rc.items():
        if cid in cat.get("providers", {}):
            cat["providers"][cid]["ready_call"] = call
    cat["ready_call_note"] = ("providers[id].ready_call: the reference library's prepared call for a generic ask, built offline. "
                              "{LAT} {LON} {NEED} {PLACE} are the ask's point, words and place; {KEY_NAME} placeholders are credentials "
                              "you supply (auth says where to get them). `example` names the place a call was prepared for when it "
                              "derives from the point. Nothing was sent.")
    p.write_text(json.dumps(cat, separators=(",", ":"), ensure_ascii=False))
    return len(rc)


def compare_page():
    body = f'''{nav(here="compare")}<main id="main" class="wide"><h1>Compare</h1><noscript><p class="nojs">Compare needs JavaScript. Every provider has its own page under <a href="find.html">Find data</a>.</p></noscript><div class="cmpbar" id="picker"></div><p class="meta" id="cmpmsg" role="status"></p><div id="cmp"></div><p class="key cmpkey" aria-hidden="true" hidden>{STAGE_KEY}</p></main>{foot()}'''
    return page("Compare providers · irlpls", body, 0, "p-compare", desc="Up to four providers side by side, link by link.", canon="compare.html")


def not_found_page():
    """Served for any missing path, so its links are absolute."""
    body = f'{nav("/")}<main id="main" class="wide"><h1>Not found</h1><p class="empty nf">No page or Capture Card at this address. <a href="/find.html">Browse providers</a></p></main>{foot("/")}'
    return page("Not found · irlpls", body, prefix="/", canon="404.html")



# ---- the agent manual: docs/AGENTS.md plus a provider index from the cards, served as agents.md and agents.html
def md_inline(t):
    t = e(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)


def slug(h):
    return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9 -]", "", h.lower()).strip())


def md_html(md):
    """The small Markdown subset the manual uses: ## headings, paragraphs, lists, tables, code fences. Returns (html, toc)."""
    out, toc, ls, i = [], [], md.split("\n"), 0
    block = lambda l: l.startswith(("#", "|", "```", "- ")) or re.match(r"\d+\. ", l)
    while i < len(ls):
        l = ls[i]
        if not l.strip() or l.startswith("# "):
            i += 1
        elif l.startswith("```"):
            j = ls.index("```", i + 1)
            out.append("<pre><code>" + e("\n".join(ls[i + 1:j]), quote=False) + "</code></pre>")
            i = j + 1
        elif l.startswith("### "):
            out.append(f'<h3 id="{slug(l[4:])}">{md_inline(l[4:])}</h3>')
            i += 1
        elif l.startswith("## "):
            toc.append((slug(l[3:]), l[3:]))
            out.append(f'<h2 id="{toc[-1][0]}">{md_inline(l[3:])}</h2>')
            i += 1
        elif l.startswith("|"):
            rows = []
            while i < len(ls) and ls[i].startswith("|"):
                rows.append([c.strip() for c in ls[i].strip().strip("|").split("|")])
                i += 1
            hd = rows[0]
            out.append(f'<div class="tbl c{min(len(hd), 3)}"><table>\n<thead><tr>' + "".join(f"<th>{md_inline(h)}</th>" for h in hd) + "</tr></thead>\n<tbody>\n"
                       + "".join("<tr>" + "".join(f'<td data-l="{e(h)}">{md_inline(c)}</td>' for h, c in zip(hd, r)) + "</tr>\n" for r in rows[2:]) + "</tbody></table></div>")
        elif l.startswith("- ") or re.match(r"\d+\. ", l):
            tag, items = ("ul" if l.startswith("- ") else "ol"), []
            while i < len(ls) and (ls[i].startswith("- ") or re.match(r"\d+\. ", ls[i])):
                items.append(re.sub(r"^(- |\d+\. )", "", ls[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{md_inline(x)}</li>" for x in items) + f"</{tag}>")
        else:
            para = []
            while i < len(ls) and ls[i].strip() and not block(ls[i]):
                para.append(ls[i])
                i += 1
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
    return "\n".join(out), toc


def agents_md(cards):
    lv = lambda c, k: c["verification"][k]["level"]
    rows = "".join(f"| **{plain_name(c)}** `{c['id']}` | {mode_text(c)} | {speed_text(c)} | {price_text(c)} | {lv(c, 'time')} · {lv(c, 'location')} · {lv(c, 'content')} |\n" for c in cards)
    u = sum(c.get("status") == "unclaimed" for c in cards)
    return ((ROOT / "docs" / "AGENTS.md").read_text().rstrip() + f"\n\n## 14. Providers listed today\n\n{len(cards)} Capture Cards, generated from `/cards.json`; {u} are compiled from public sources, not confirmed by the provider. "
            "The last column is each card's level for time, place and content. Read the full card before you rely on it.\n\n"
            "| Provider | Routes | Speed | Price | Time · place · content |\n|---|---|---|---|---|\n" + rows)


def toc_html(toc):
    """A long document's contents (its ## headings), as on the standard: a sticky column on a desktop, folded on a phone
    (src/bar.js opens it on a desktop and marks the section in view). Fewer than three headings: none, the column centred."""
    if len(toc) < 3:
        return ""
    return ('<details class="toc" id="toc" data-toc><summary>Contents</summary><nav aria-label="Contents"><ol>'
            + "".join(f'<li><a href="#{s}">{md_inline(h)}</a></li>' for s, h in toc) + '</ol></nav></details>')


def doc_page(md, title, canon, desc, aud=""):
    """A plain document page (changelog, governance) from Markdown, in the spec layout. aud: one plain line under the
    title saying who the page is for (the owner, 2026-10-30: providers, agents, the standard)."""
    body, toc = md_html(md)
    h1 = re.search(r"^# (.*)", md, re.M).group(1)
    head = (f'{nav("")}<div class="spec-wrap{"" if len(toc) >= 3 else " solo"}">{toc_html(toc)}<div class="spec-head"><h1 class="hl hl-doc all" data-t="{e(h1)}">{e(h1)}</h1>{f"<p class=aud>{e(aud)}</p>" if aud else ""}</div>'
            f'<main id="main" class="std">\n{body}\n</main></div>{foot()}')
    return page(title, head, cls="doc", desc=desc, canon=canon, head="")


DOORS = (("agents.html", "For agents", "The manual your AI agent reads: how to ask, the tools, the rules."),
         ("providers.html", "For providers", "Publish your Capture Card and make a link evidenced."),
         ("standard.html", "The standard", "The full specification: card format, the eight links, levels, protocol."))
DOCS_MORE = (("glossary.html", "Glossary"), ("chain.html", "The full chain"), ("check.html", "Check a card"), ("changelog.html", "Changelog"))


def docs_page():
    """docs.html: three doors (agents, providers, the standard) as cards, and a few small secondary links."""
    arrow = '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>'
    doors = "".join(f'<li><a class="door" href="{h}"><b>{e(t)}</b><span>{e(d)}</span>{arrow}</a></li>' for h, t, d in DOORS)
    more = "".join(f'<a href="{h}">{e(t)}</a>' for h, t in DOCS_MORE)
    body = (f'{nav("")}<main id="main" class="docs"><h1 class="hl all" data-t="The docs">The docs</h1>'
            f'<p class="aud">Pick the one for you.</p><ul class="doors">{doors}</ul>'
            f'<nav class="more" aria-label="More">{more}</nav></main>{foot()}')
    return page("Docs · irlpls", body, cls="doc", desc="The irlpls docs: the agent manual, the provider guide and the standard.", canon="docs.html", head="")


def agents_page(md):
    body, toc = md_html(md)
    ask = "Read https://irlpls.com/agents.md before you use irlpls for me."
    head = (f'{nav("", "agents")}<div class="spec-wrap{"" if len(toc) >= 3 else " solo"}">{toc_html(toc)}<div class="spec-head agents-head"><h1 class="hl all" data-t="irlpls for agents">irlpls for agents</h1><p class="aud">The manual your AI agent reads.</p>'
            f'<div class="handoff"><code id="ask">{e(ask)}</code><button type="button" id="copy">Copy</button></div>'
            '<p class="raw">Plain text: <a href="agents.md">agents.md</a></p></div>'
            f'<main id="main" class="std">\n{body}\n</main></div>{foot()}')
    # the prompt names this page's own host at runtime (the preview now, the real domain later); irlpls.com without JS
    js = ("<script>(function(){var b=document.getElementById('copy'),a=document.getElementById('ask');"
          "if(/^https?:$/.test(location.protocol)){var u=new URL('agents.md',location.href).href,x=a.textContent.split('https://irlpls.com/agents.md');"
          "if(x.length===2)a.innerHTML=x[0].replace(/&/g,'&amp;').replace(/</g,'&lt;')+u.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\\//g,'/<wbr>')+x[1].replace(/&/g,'&amp;').replace(/</g,'&lt;')}"
          "var t=a.textContent;b.onclick=function(){(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){b.textContent='Copied'},function(){var r=document.createRange();r.selectNodeContents(document.getElementById('ask'));getSelection().removeAllRanges();getSelection().addRange(r)})};"
          "})();</script>")
    return page("For agents · irlpls", head, cls="doc", desc="The agent manual: how to request real-world data and report where it came from.", canon="agents.html", head="").replace("</body></html>", js + "</body></html>")

LLMS = """# irlpls

> Real-world data on demand, with provenance. An open protocol and router for agents. One call for new observations of the physical world: a reading, photo, video or report, live now or captured to order by people, drones, satellites, dashcams or sensors. A draft standard (version 2026-10-26) and a registry of Capture Cards: what each provider captures, where, how fast, at what price, under what rights, and, guarantee by guarantee, what it evidences and what it only claims. irlpls checks nothing itself, captures nothing, sells no data and takes no cut; you buy from providers directly.

Use it when the answer depends on the world as it is now, or when someone goes and looks, and nothing published answers it. Not for archives, news, or facts on a website.

## Start here
- [Agent manual](/agents.md): read first. When to use irlpls, the loop, how to report where a result came from, and what is usually missing.
- [Catalog index](/catalog.index.json): read before any card. Five question types, 25 ask families with their providers, and what irlpls cannot serve. About 29 KB.
- [All Capture Cards](/cards.json): every provider, as JSON. About 1 MB: fetch single cards at /cards/<id>.json instead.
- [Reference matcher](/match.js): ranks cards for a need (`IRLMatch.parse`, `IRLMatch.score`).
- [Glossary](/glossary.html): the eight provenance links, from capture to delivery.

## Standard
- [Specification](/standard.html): Capture Card, discovery, Request, Policy, Job, matching, conformance.
- Ordering: Section 14 of the specification is the wire protocol (discover, quote, order with an Idempotency-Key, status, deliver, cancel, at each provider's own endpoint); how an agent uses it is under "Ordering through the protocol" in the [agent manual](/agents.md).
- [Changelog](/changelog.html) and [governance](/governance.html).
- [Capture Card schema](/2026-10-26/schemas/capture-card.json), [Request](/2026-10-26/schemas/request.json), [Policy](/2026-10-26/schemas/policy.json), [Job](/2026-10-26/schemas/job.json).
- A provider's own card: `https://<provider>/.well-known/irlpls` (capability `com.irlpls.capture.card`).

## Reference library (not yet published)
- Python: `irlpls.request(need, why, policy)` returns a job (questions, plan, ruled_out, submit, results).
- MCP tools: irlpls_request, irlpls_answer, irlpls_choose_provider, irlpls_submit, irlpls_get_job, irlpls_find_providers, irlpls_get_card.

## Optional
{ids}
"""

LANDING_KEYS = ("id", "name", "status", "tags", "summary", "short", "capabilities", "coverage", "delivery", "pricing", "access", "expires", "verification")


def landing(h, cards):
    """src/index.html, made current: the embedded card snapshot (what the matcher and stage dots need, used until
    cards.json arrives or if it never does). Screen 1's examples are samples with committed media: nothing live here."""
    def trim(c):  # only the fields match.js and the stage dots read
        t = {k: c[k] for k in LANDING_KEYS if k in c}
        # the level, plus what the stage mapping reads: a camera standard, and the camera/content/time methods (a seal, an attested time)
        t["verification"] = {k: {"level": v.get("level", "none"), **{f: v[f] for f in STAGE_FIELDS.get(k, ()) if v.get(f)}}
                             for k, v in (c.get("verification") or {}).items()}
        if c.get("provenance"):  # stages 6 and 8 read only the levels
            t["provenance"] = {k: {"level": g.get("level")} for k, g in c["provenance"].items() if isinstance(g, dict)}
        t["delivery"] = {k: v for k, v in (c.get("delivery") or {}).items() if k in ("modes", "speed")}
        t["pricing"] = {k: v for k, v in (c.get("pricing") or {}).items() if k == "from_usd"}
        t["access"] = {k: v for k, v in (c.get("access") or {}).items() if k == "cost"}
        return t
    snap = json.dumps({"cards": [trim(c) for c in cards]}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    h, n = re.subn(r'(<script id="cards" type="application/json">).*?(</script>)', lambda m: m.group(1) + snap + m.group(2), h, count=1, flags=re.S)
    if not n:
        raise SystemExit("src/index.html: no <script id=\"cards\"> to fill")
    pubs = [c for c in cards if PUB["eff"].get(c["id"]) == "live"][:3]
    if pubs:  # one quiet line, the LAST thing in the footer (never above a task), hidden while no provider has published
        names = " · ".join(e(plain_name(c)) for c in pubs)
        foot_at = h.index('<footer class="site-foot">')
        end = h.index('</footer>', foot_at)
        h = h[:end] + f'<a class="publ" href="find.html#pubh">Published by providers: {names}&nbsp;›</a>' + h[end:]
    return h


def public_scrub(site):
    """--public: drop omitted files anywhere in site/, strip preview-only tags, and fail on preview hosts or local paths."""
    for f in sorted(site.rglob("*"), reverse=True):
        if f.name in PUBLIC_OMIT and f.exists():
            shutil.rmtree(f) if f.is_dir() else f.unlink()
    leaks = []
    for f in site.rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".css", ".json", ".txt", ".md", ".xml", ".svg"):
            t = f.read_text(errors="replace")
            t2 = PREVIEW_ONLY_RX.sub("", t)
            if t2 != t:
                f.write_text(t2)
            leaks += [f"{f.relative_to(site)}: {m.group(0)}" for m in PUBLIC_LEAK_RX.finditer(t2)]
    if leaks:
        sys.exit("public build refused, preview hosts or local paths in site/:\n  " + "\n  ".join(leaks[:40]))


def main(public=False):
    all_cards = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
    # The site lists only providers that deliver NEW data (live feeds or commissioned capture).
    cards = [c for c in all_cards if c.get("status") != "defunct" and (c.get("delivery") or {}).get("modes")]
    # a provider's own card, read daily from its domain, replaces the compiled one while it is live (or stale, kept 7 days)
    PUB["state"] = published.load_state()
    cards, PUB["eff"] = published.overrides(cards, PUB["state"])
    for c in cards:
        if c.get("status") == "provider-maintained" and PUB["eff"].get(c["id"]) in published.SHOWN:
            try:
                PUB["standing"][c["id"]] = standing.standing(c["id"], cards)
            except Exception as x:  # a hostile or odd card never breaks the build: fall back to the compiled card
                print(f"published card {c['id']} not shown ({type(x).__name__}: {x})")
                comp = next(a for a in all_cards if a["id"] == c["id"])
                cards[cards.index(c)] = comp
                PUB["eff"][c["id"]] = "rejected"
    if not public:  # the private refresh job's check (gitignored); the public build never reads it
        LIVE.update(health.load(ROOT / "src" / "media" / "live" / "health.json"))  # merged into each card's read-check line
    if SITE.exists():
        for f in SITE.iterdir():
            if f.name != "CNAME":
                shutil.rmtree(f) if f.is_dir() else f.unlink()
    SITE.mkdir(exist_ok=True)
    for f in (ROOT / "src").iterdir():
        if public and f.name in PUBLIC_OMIT:
            continue
        (shutil.copytree if f.is_dir() else shutil.copy)(f, SITE / f.name)
    if public:  # src/media/live/ is the private refresh job's gitignored output: never part of the public site
        shutil.rmtree(SITE / "media" / "live", ignore_errors=True)
    (SITE / "c").mkdir(parents=True, exist_ok=True)
    (SITE / "cards").mkdir(exist_ok=True)
    for i, c in enumerate(cards):
        try:
            pg = card_page(c)
        except Exception as x:  # never ship a page we could not render from a provider's card: the compiled one instead
            if c.get("status") != "provider-maintained":
                raise
            print(f"published card {c['id']} not rendered ({type(x).__name__}: {x}); compiled card shown")
            c = cards[i] = next(a for a in all_cards if a["id"] == c["id"])
            PUB["eff"][c["id"]] = "rejected"
            pg = card_page(c)
        (SITE / "c" / f'{c["id"]}.html').write_text(pg)
        (SITE / "cards" / f'{c["id"]}.json').write_text(json.dumps(c, indent=2) + "\n")
    (SITE / "cards.json").write_text(json.dumps({"assurances": [list(a) for a in ASSURANCES], "cards": cards}))
    shutil.copy(ROOT / "schema" / "request.schema.json", SITE / "request.schema.json")
    pages = ["", "find.html", "compare.html", "docs.html", "agents.html", "standard.html", "changelog.html", "governance.html", "providers.html", "privacy.html", "glossary.html"] + [f'c/{c["id"]}.html' for c in cards]
    (SITE / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"<url><loc>https://irlpls.com/{u}</loc></url>\n" for u in pages) + "</urlset>\n")
    (SITE / "published.json").write_text(json.dumps(published.public(PUB["state"]), indent=1) + "\n")  # check.html ?d= reads it
    if PUB["state"].get("cards"):
        (SITE / "badge").mkdir(exist_ok=True)
        for cid, ent in PUB["state"]["cards"].items():
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", cid):
                nm = next((plain_name(c) for c in cards if c["id"] == cid), ent.get("name") or cid)
                (SITE / "badge" / f"{cid}.svg").write_text(badge_svg(nm, ent, PUB["eff"].get(cid) or ent.get("state")))
    from irlpls.catalog import FAMILIES
    fam = {fid: {"title": t, "providers": provs, "asks": standing.bench().get(fid, [])} for fid, _ty, t, _ex, provs in FAMILIES}
    (SITE / "bench_asks.json").write_text(json.dumps({"max_rows": standing.MAX_ROWS, "families": fam}, separators=(",", ":")))  # check.html ranks in the browser
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: https://irlpls.com/sitemap.xml\n")
    data = ROOT / "irlpls" / "data"
    data.mkdir(exist_ok=True)
    (data / "cards.json").write_text(json.dumps({"assurances": [list(a) for a in ASSURANCES], "cards": all_cards}, indent=1))
    (SITE / "index.html").write_text(landing((SITE / "index.html").read_text(), cards))
    import subprocess, sys
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_catalog.py"), "--out", str(SITE), "--cards-json", str(SITE / "cards.json")], check=True)  # catalog.index.json + catalog.json for agents
    add_ready_calls(cards)  # + each provider's ready call (Find data shows it for the top match)
    (SITE / "llms.txt").write_text(LLMS.format(ids="\n".join(f"- [{c['name']}](/cards/{c['id']}.json): {c.get('short') or c['summary']}" for c in cards)))
    md = agents_md(cards)
    (SITE / "agents.md").write_text(md)
    (SITE / "agents.html").write_text(agents_page(md))
    (SITE / "docs.html").write_text(docs_page())
    (SITE / "find.html").write_text(index_page(cards))  # the finder; src/index.html is the landing page
    (SITE / "check.html").write_text((ROOT / "src" / "check.html").read_text())
    shutil.copyfile(ROOT / "irlpls" / "data" / "public_suffix_list.dat", SITE / "public_suffix_list.dat")  # check.html's www-twin rule (MPL-2.0)
    (SITE / "chain.html").write_text((ROOT / "src" / "chain.html").read_text())  # the provenance chain, stage by stage  # in-browser card checker for providers
    (SITE / "compare.html").write_text(compare_page())
    (SITE / "404.html").write_text(not_found_page())  # GitHub Pages serves it for any missing path
    (SITE / "changelog.html").write_text(doc_page((ROOT / "CHANGELOG.md").read_text(), "Changelog · irlpls", "changelog.html", "Every version of the irlpls standard, newest first."))
    (SITE / "governance.html").write_text(doc_page((ROOT / "GOVERNANCE.md").read_text(), "Governance · irlpls", "governance.html", "Who decides what at irlpls, what it will not do, and when that changes."))
    (SITE / "providers.html").write_text(doc_page((ROOT / "docs" / "PROVIDERS.md").read_text(), "For providers · irlpls", "providers.html", "How a provider publishes its own Capture Card at /.well-known/irlpls.", aud="For companies that capture real-world data."))
    (SITE / "privacy.html").write_text(doc_page((ROOT / "docs" / "PRIVACY.md").read_text(), "Privacy · irlpls", "privacy.html", "What irlpls sees, logs and sends: there is no hosted irlpls service, only static files with no request endpoint."))
    (SITE / "glossary.html").write_text(doc_page((ROOT / "docs" / "GLOSSARY.md").read_text(), "Glossary · irlpls", "glossary.html", "The field's terms for provenance, from capture to delivery: what each link shows, and what it cannot."))
    for f in [*(ROOT / "schema").glob("*.schema.json"), *(ROOT / "schema" / "protocol").glob("*.schema.json")]:  # each version's schemas at the address their $id names
        sid = json.loads(f.read_text())["$id"]
        out = SITE / urllib.parse.urlparse(sid).path.lstrip("/")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f.read_text())
    if public:
        public_scrub(SITE)
    print(f"built {len(cards)} cards -> {SITE}" + (" (public: no prototypes, no preview-only files)" if public else ""))

if __name__ == "__main__":
    main(public="--public" in sys.argv[1:])
