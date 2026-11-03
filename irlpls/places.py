"""An offline gazetteer: put a place name on the map without asking anyone (no network).

Same table as the browser's src/places.js: GeoNames cities15000 (CC BY 4.0, https://www.geonames.org/), cities of
50,000+ people and every national capital, bundled as irlpls/data/places.json. The hosted read-only MCP server uses
it instead of Nominatim (deploy/serve.py), so a place written in a need never leaves the server.

    lookup("Munich")            -> {"lat": 48.14, "lon": 11.58, "name": "Munich", "country_code": "de", ...}
    lookup("Portland, Maine")   -> Portland, ME (the named state picks among same-named cities)
    lookup("Comfort, TX")       -> None (too small for the table: pass lat/lon instead)

The result has the shape of geo.geocode(): lat, lon, name, country_code, state, subdivision, subdivisions.
"""
import functools
import json
import re
import unicodedata
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "places.json"
_REPO = Path(__file__).resolve().parent.parent / "src" / "places.json"


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower().replace("’", "'").replace("`", "'"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).replace("ß", "ss")
    return re.sub(r"\s+", " ", s).strip()


@functools.lru_cache(maxsize=1)
def _table():
    for p in (PATH, _REPO):
        try:
            d = json.loads(p.read_text())
            break
        except (OSError, ValueError):
            d = None
    if not d:
        return None
    idx, ctry, adm = {}, {}, {}
    for i, r in enumerate(d.get("cities") or []):
        for n in [r[0]] + (str(r[1]).split("|") if r[1] else []):
            if n:
                rows = idx.setdefault(_norm(n), [])
                if i not in rows:
                    rows.append(i)
    for k, v in (d.get("countries") or {}).items():
        ctry[_norm(k)] = v
    for k, v in (d.get("admin1") or {}).items():
        adm.setdefault(_norm(v), []).append(k)
    return d, idx, ctry, adm


def _words(text):
    out = []
    for m in re.finditer(r"[^\s,;:!?()\"\[\]{}/]+", str(text or "")):
        w = m.group(0).strip("'.-’")
        if w:
            out.append((w, _norm(w)))
    return out


def lookup(place):
    """The biggest city of that name, preferring one in a state/division or country also named. None if unknown."""
    t = _table()
    if not t or not place or not str(place).strip():
        return None
    d, idx, ctry, adm = t
    ws = _words(place)
    ctx = []  # (start, end, kind, code): countries and first-level divisions named in the text
    for n in range(4, 0, -1):
        for i in range(len(ws) - n + 1):
            k = " ".join(w[1] for w in ws[i:i + n])
            if k in ctry and not (n == 1 and k in ("us", "in") and ws[i][0] != ws[i][0].upper()):
                ctx.append((i, i + n, "cc", ctry[k]))
            ctx += [(i, i + n, "adm", a) for a in adm.get(k, ())]
    for i, (raw, _) in enumerate(ws):
        if re.fullmatch(r"[A-Z]{2}", raw) and f"us.{raw}" in d["admin1"]:
            ctx.append((i, i + 1, "adm", f"us.{raw}"))
    cities = d["cities"]
    best = None
    for n in range(4, 0, -1):
        for i in range(len(ws) - n + 1):
            rows = idx.get(" ".join(w[1] for w in ws[i:i + n]))
            if not rows:
                continue
            # context named outside this run of words ("Paris, Texas": Texas; not "Washington" naming itself)
            around = [c for c in ctx if c[1] <= i or c[0] >= i + n]
            ad = {c[3] for c in around if c[2] == "adm"}
            cc = {c[3] for c in around if c[2] == "cc"}
            in_adm = [r for r in rows if cities[r][3] and f"{cities[r][2]}.{cities[r][3]}" in ad]
            in_cc = [r for r in rows if cities[r][2] in cc]
            if around and not in_adm and not in_cc:
                continue  # a same-named city somewhere else is the wrong place, not a guess
            pick = (in_adm or in_cc or rows)[0]
            score = (2 if in_adm else 1 if in_cc else 0, n, cities[pick][6])
            if best is None or score > best[0]:
                best = (score, cities[pick])
    if not best:
        return None
    name, _alt, c, a1, lat, lon, _pop = best[1]
    state = d["admin1"].get(f"{c}.{a1}") if a1 else None
    sub = f"US-{a1}" if c == "us" and a1 else None
    return {"lat": lat / 100, "lon": lon / 100, "name": f"{name}, {a1}" if c == "us" and a1 else name,
            "country_code": c, "state": state.lower() if state else None, "subdivision": sub,
            "subdivisions": [sub] if sub else [], "source": "gazetteer"}
