"""Deterministic fit score of one Capture Card against one Request.

Fit is for this request only: description 40%, guarantees 40%, coverage/timing/price 20%, then multiplied by the
description match squared, so proof and coverage can't rescue a provider that doesn't do what was asked.
Only providers that deliver NEW data are in scope: live feeds, or capture commissioned to order.
evidenced = 1, claimed = 0.5, none = 0. It is never a quality grade for a provider.

Description match (parts: matched, custom, specifics):
  - each term weighs 1; `specifics` (what's in the shot or where, or words no card uses) weigh 0.5;
  - a term counts if the card's name, summary, tags, tasks or modalities mention it (or a concept it maps to);
  - a provider that takes custom capture tasks in the requested format gets 0.75 for specifics its card doesn't mention;
  - each requested modality (photo, video, sensor...) weighs 1, and counts only alongside a matched term
    (a camera that films something else is not a match) or for a custom-capture provider;
  - imaging sensor is matched like any term: "radar"/"sar", "thermal"/"infrared", "optical". A card with a photo or video
    modality is an optical camera; "through clouds" / "all-weather" on either side reads as the one word "allweather".
Mirrors src/match.js; keep them in step.
"""
import re

from . import geo
from .need import TONIGHT

WEIGHTS = {"description": 0.4, "guarantees": 0.4, "coverage": 0.2}
SPECIFIC = 0.5   # weight of a term that only describes the shot or the spot
MIN_DESCRIPTION = 0.35  # matching less than about a third of the request is no match (fit 0)
STRONG, WEAK = 60, 30  # verdict: fit >= 60 strong, 30-59 weak, below 30 none (nothing here does what you asked)
LATE_WEEKS = 0.7   # fit multiplier: "weeks" delivery against a deadline of 3 days or less
LATE_DAYS = 0.85   # fit multiplier: "days" delivery against a deadline of a day or less
GEO_ELSEWHERE = 0.3  # coverage value of a card whose explicit areas are elsewhere, when the ask's place has no known country
LIVE_ONLY = 0.85  # fit multiplier for a "now" request on a provider with no live feed
SENSORS = {"optical", "radar", "thermal", "allweather"}  # imaging-sensor terms: custom-task credit never covers them
CUSTOM = 0.75    # credit for an unmentioned specific, from a provider that takes custom capture tasks in the requested format
BULK_SINGLE = 0.5  # fit multiplier: a dataset vendor, for one new photo or clip of a named place or time
SPARSE = 0.85    # fit multiplier: a card whose regions say it is dense only somewhere else ("Global (dense in Europe)")
# A dataset vendor: its capture is datasets made to order by contributors at scale (training-data collection), not one
# visit to one spot. Read from the card's tags.
BULK = r"(?<![a-z])(training data|custom collection|data collection|physical ai|contributors)(?![a-z])"
DATASET = r"\b(dataset|datasets|training data|examples|corpus|samples)\b"


def bulk_vendor(card):
    return bool(re.search(BULK, " ; ".join(card.get("tags") or []).lower()))


def single_capture(req):
    """One new photo or clip (quantity 1, no dataset words) of a named place or by a stated time."""
    return req.mode == "commission" and (req.quantity or 1) == 1 and not re.search(DATASET, (req.need or "").lower()) \
        and bool({"photo", "video"} & set(req.modalities or [])) \
        and bool(req.country_code or req.place or req.within_minutes is not None)


# A small concept map so everyday words reach the vocabulary cards use. Your own LLM does this better.
CONCEPTS = {
    "bakery": "store storefront shop retail exterior", "cafe": "store storefront shop retail exterior", "restaurant": "store storefront retail exterior",
    "shop": "store storefront retail", "storefront": "exterior", "window": "storefront exterior", "door": "storefront exterior",
    "doors": "storefront exterior", "open": "storefront exterior hours", "grocery": "store retail", "supermarket": "store retail",
    "house": "property exterior", "home": "property", "building": "property exterior", "car": "auto vehicle", "truck": "auto fleet vehicle",
    "street": "street road", "road": "road street", "highway": "road freeway traffic", "bridge": "road highway", "flood": "river gauge streamflow",
    "flooded": "river gauge streamflow", "river": "river gauge streamflow", "creek": "river gauge streamflow", "smoke": "pm2.5 pm10",
    "smoky": "pm2.5 pm10", "wildfire": "pm2.5 pm10", "pollution": "pm2.5 no2 ozone",
    "construction": "site aerial survey", "roof": "aerial roof inspection", "crop": "aerial satellite", "field": "aerial satellite",
    "people": "human participants", "someone": "human humans participants crowd", "person": "human humans participants crowd",
    "survey": "survey questionnaire", "radar": "sar radar", "sar": "radar", "factory": "industrial", "plant": "industrial", "refinery": "industrial",
    "tubing": "river gauge streamflow", "kayak": "river gauge streamflow", "kayaking": "river gauge streamflow",
    "paddling": "river gauge streamflow", "swimming": "river gauge", "fishing": "river gauge", "rafting": "river gauge streamflow",
    "level": "gauge water", "height": "gauge water", "levels": "gauge water", "tide": "tides", "surge": "storm surge",
    "weather": "weather temperature humidity", "rain": "weather precipitation", "raining": "weather precipitation",
    "wind": "weather wind", "snow": "weather", "hot": "weather temperature", "cold": "weather temperature",
    "snowy": "snow weather", "foggy": "fog weather", "rainy": "rain weather precipitation", "icy": "ice weather",
    # driving scenes: road users, manoeuvres and road works are seen from the road; driving footage is a dashcam's
    "cyclist": "road", "bicycle": "road", "bike": "road", "pedestrian": "road", "turn": "road", "merge": "road",
    "intersection": "road", "crosswalk": "road", "roundabout": "road", "roadwork": "road", "driving": "dashcam driving",
}


def _stem(w):
    return re.sub(r"(ing|ies|es|s|ed)$", "", w) if len(w) > 4 else w


def _tokens(text):
    return {t for t in re.split(r"[^a-z0-9.]+", text.lower()) if t}


# Words too ambiguous to match literally ("open" data, "field" agents); match only through their concepts.
CONCEPT_ONLY = {"open", "field", "window", "door", "doors", "people", "level", "levels", "height", "someone", "person", "home"}


def _has(tokens, stems, w):
    con = CONCEPTS.get(w, CONCEPTS.get(_stem(w), "")).split()
    hit = lambda x: x in tokens or _stem(x) in stems  # noqa: E731
    if w in CONCEPT_ONLY or _stem(w) in CONCEPT_ONLY:
        return any(hit(x) for x in con)
    return hit(w) or any(hit(x) for x in con)


ALLWEATHER = r"\bthrough (?:the )?clouds?\b|\ball[- ]weather\b|\bcloud[- ]penetrating\b"  # same rewrite as need.PHRASES
OPTICAL = {"photo", "video"}  # modalities taken with an ordinary (optical) camera


def vocab(card):
    """The words a card uses about itself (name, summary, tags, tasks, modalities), and their stems."""
    mods = card["capabilities"]["modalities"]
    text = " ".join([card["name"], card["summary"], " ".join(card.get("tags", [])),
                     " ".join(card["capabilities"]["tasks"]), " ".join(mods)]).lower()
    tokens = _tokens(re.sub(ALLWEATHER, " allweather ", text))
    if OPTICAL & set(mods):
        tokens.add("optical")
    return tokens, {_stem(t) for t in tokens}


def mentions(card, w, v=None):
    """Does the card's text mention this word, or a concept it maps to?"""
    return _has(*(v or vocab(card)), w)


def custom_capture(card):
    """The card says it takes custom capture tasks (custom photo tasks, uploads, submissions) made to order."""
    return "commission" in ((card.get("delivery") or {}).get("modes") or []) and \
        bool(re.search(r"\b(custom|upload|submission)\b", " ".join(card["capabilities"]["tasks"]).lower()))


def covers(card, country_code, state=None, subdivision=None):
    """geo.covers, except a card whose regions say Global or Worldwide covers everywhere
    (so a list of dense cities such as "New York, Austin" isn't read as the only US states covered)."""
    if re.search(r"(?<![a-z])(global|worldwide)(?![a-z])", " ".join(card["coverage"]["regions"]).lower()):
        return True
    return geo.covers(card, country_code, state, subdivision)


def _codes(text, min_len=0):
    """ISO country codes named in a text by the region phrases geo knows ("Europe", "United States", "Euro-Mediterranean")."""
    out = set()
    for phrase, codes in geo._REGION_CODES.items():
        if codes != "*" and len(phrase) >= min_len and re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text):
            out |= codes
    return out


def regional(card, cc):
    """The card is a national or regional source for this country: its explicit areas list the country (not "*"), or its
    name, regions or tags name a region that includes it, or its summary names one in full ("Euro-Mediterranean").
    A tie-break only: a regional catalogue beats a global one at the same fit."""
    if not cc:
        return False
    cc = cc.lower()
    areas = [a.lower() for a in card["coverage"].get("areas") or []]
    if cc in areas or any(a.startswith(cc + "-") for a in areas):
        return True
    short = " ; ".join([card["name"]] + card["coverage"]["regions"] + card.get("tags", [])).lower()
    return cc in (_codes(short) | _codes((card.get("summary") or "").lower(), 5))


def sparse_here(card, cc):
    """The card's regions say it is dense only somewhere else ("Global (dense in Europe)") and this country is not there."""
    if not cc:
        return False
    for g in card["coverage"]["regions"]:
        for m in re.finditer(r"dense(?:st)? in ([a-z -]+?)(?=[),;]| and |$)", g.lower()):
            codes = _codes(m.group(1))
            if codes and cc.lower() not in codes:
                return True
    return False


SPEED_MAX_MIN = {"minutes": 60, "hours": 1440, "days": 10080, "weeks": 43200}


def expired(card, today=None):
    """True if the card's `expires` date (YYYY-MM-DD) is before today: brokers MUST skip it. An unreadable date is not guessed at."""
    import datetime
    e = card.get("expires")
    if not isinstance(e, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", e):
        return False
    try:
        return datetime.date.fromisoformat(e) < (today or datetime.date.today())
    except ValueError:
        return False


def in_scope(card, today=None):
    """IRLpls lists only NEW data: live, or commissioned. Archive-only, defunct and expired providers are out."""
    return card.get("status") != "defunct" and bool((card.get("delivery") or {}).get("modes")) and not expired(card, today)


def timing(card, req):
    """(0..1, gap or None): can this provider deliver new data in the mode and by the deadline asked?"""
    d = card.get("delivery") or {}
    modes, speed = d.get("modes", []), d.get("speed", "unknown")
    if req.mode == "live":
        if "live" in modes:
            return 1.0, None
        if speed == "minutes":
            return 1.0, None
        return (0.4, "not live; fastest stated capture is " + speed) if speed == "hours" else (0.0, "not live; capture takes " + speed)
    # commission: capture made to order. Live feeds can still supply a series from now on.
    base = 1.0 if "commission" in modes else 0.75
    if re.search(TONIGHT, req.need or "", re.I):
        # "tonight": a live feed answers it; otherwise only a provider whose card says minutes or hours of latency can
        if "live" in modes or speed == "minutes":
            return 1.0, None
        if speed == "hours":
            return base, None
        if speed == "unknown":
            return base * 0.6, "delivery time not published"
        return base * 0.25, f"can't deliver tonight; usually takes {speed}"
    if req.within_minutes is None or speed == "unknown":
        return (base if speed != "unknown" else base * 0.6), (None if speed != "unknown" else "delivery time not published")
    if SPEED_MAX_MIN[speed] <= req.within_minutes:
        return base, None
    return base * 0.25, f"usually takes {speed}; you need it within {_fmt(req.within_minutes)}"


def _fmt(m):
    return f"{m} min" if m < 60 else f"{m // 60} h" if m < 1440 else f"{m // 1440} d"


def describe(card, req):
    """(0..1 description match, matched, custom-credited terms)."""
    spec = set(getattr(req, "specifics", None) or [])
    cmods = card["capabilities"]["modalities"]
    custom = custom_capture(card) and bool(req.modalities) and all(m in cmods for m in req.modalities)
    v = vocab(card)
    num = den = 0.0
    hit, partial = [], []
    for w in req.terms:
        wt = SPECIFIC if w in spec else 1.0
        den += wt
        if mentions(card, w, v):
            num += wt
            hit.append(w)
        elif custom and w in spec and w not in SENSORS:  # a provider can take custom tasks, not change its sensor
            num += wt * CUSTOM
            partial.append(w)
    mh = [m for m in req.modalities if m in cmods and (hit or custom or not req.terms)]
    num += len(mh)
    den += len(req.modalities)
    return num / den, hit + mh, partial


def _global(card):
    return bool(re.search(r"(?<![a-z])(global|worldwide)(?![a-z])", " ".join(card["coverage"]["regions"]).lower()))


def _names_place(card, req):
    names = [w for w in re.split(r"[^a-z]+", (req.place or "").lower()) if len(w) > 3] + \
        ([req.state] if getattr(req, "state", None) else [])
    local_text = " ; ".join(card["coverage"]["regions"] + card.get("tags", [])).lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", local_text) for w in names)


def verdict(total):
    """strong >= 60, weak 30-59, none < 30 (None when nothing was scored)."""
    return None if total is None else "strong" if total >= STRONG else "weak" if total >= WEAK else "none"


def score(card, req):
    parts, why = {}, []
    if req.terms or req.modalities:
        d, matched, partial = describe(card, req)
        parts["description"], parts["matched"] = d, matched
        if partial:
            parts["custom"] = partial
            why.append("takes custom tasks; card doesn't mention " + ", ".join(partial))
        if getattr(req, "specifics", None):
            parts["specifics"] = list(req.specifics)
    if req.require:
        s, missing, claimed = 0.0, [], []
        for k in req.require:
            lvl = card["verification"][k]["level"]
            if lvl == "evidenced":
                s += 1
            elif lvl == "claimed":
                s += 0.5
                claimed.append(k)
            else:
                missing.append(k)
        parts["guarantees"] = s / len(req.require)
        parts["missing"], parts["claimed_only"] = missing, claimed
        if missing:
            why.append("no proof of " + ", ".join(missing))
        if claimed:
            why.append("self-reported " + ", ".join(claimed))
    cov = []
    if req.country_code:
        c = covers(card, req.country_code, getattr(req, "state", None), getattr(req, "subdivision", None))
        cov.append(0.5 if c is None else float(c))
        if c is None:
            parts["coverage_unknown"] = True  # the card doesn't say whether it works there
        if c is False:
            why.append("doesn't cover " + (getattr(req, "state", None) or req.place or req.country_code.upper()).title())
            parts["outside_coverage"] = True
    elif req.place and not req.regions and not _global(card):
        areas = [a.lower() for a in card["coverage"].get("areas") or []]
        if areas and "*" not in areas and not _names_place(card, req):
            cov.append(GEO_ELSEWHERE)   # the place has no known country, and this card says it works somewhere else
            why.append("covers " + ", ".join(a.upper() for a in areas[:4]) + "; may not be where you need it")
    elif req.regions:
        text = (" ".join(card["coverage"]["regions"]) + " " + card["coverage"].get("notes", "")).lower()
        glob = re.search(r"(?<![a-z])(global|worldwide)(?![a-z])", text)
        cov.append(1.0 if glob or any(w in text for w in req.regions) else 0.0)
    # A card whose coverage or tags name the place ("Seattle", "London", "Washington") wins ties against a global one.
    if _names_place(card, req) or regional(card, req.country_code):
        parts["local"] = True
    t = timing(card, req)
    if t is not None:
        cov.append(t[0])
        if t[1]:
            why.append(t[1])
    if req.max_price_usd is not None:
        p = (card.get("pricing") or {}).get("from_usd")
        if p is not None and type(p) in (int, float) and p == p and p not in (0, float("inf")) and p > 0:
            ok = p <= req.max_price_usd  # False for a NaN budget: fails closed
            cov.append(1.0 if ok else 0.0)
            if not ok:
                why.append("over budget")
        elif not (card.get("access") or {}).get("cost") in ("free", "free with key"):
            cov.append(0.5)  # a paid card with no usable price: unknown, not in budget
            why.append("price not published")
    if cov:
        parts["coverage"] = sum(cov) / len(cov)
    # The description gates everything: proof and coverage rank providers that do what you asked,
    # they can't rescue one that only half does (squared, so 2 of 3 terms keeps 44%).
    d = parts.get("description")
    num = sum((1.0 if k == "description" else parts[k]) * w for k, w in WEIGHTS.items() if k in parts)
    den = sum(w for k, w in WEIGHTS.items() if k in parts)
    total = int(100 * num / den * (d * d if d is not None else 1) + 0.5) if den else None  # rounds half up, like Math.round
    dl = card.get("delivery") or {}
    if total and req.mode == "live" and "live" not in (dl.get("modes") or []) and dl.get("speed") != "minutes":
        total = int(total * LIVE_ONLY + 0.5)  # asked for now: a provider that can't deliver now ranks below one that can
    w = req.within_minutes
    if total and req.mode != "live" and w is not None and "live" not in (dl.get("modes") or []):
        if dl.get("speed") == "weeks" and w <= 4320:
            total = int(total * LATE_WEEKS + 0.5)   # weeks of delivery against a deadline of days: materially worse
        elif dl.get("speed") == "days" and w <= 1440:
            total = int(total * LATE_DAYS + 0.5)
    if total and single_capture(req) and bulk_vendor(card):
        total = int(total * BULK_SINGLE + 0.5)  # one photo of one spot: on-site capture before dataset vendors
        parts["bulk"] = True
    if total and sparse_here(card, req.country_code):
        total = int(total * SPARSE + 0.5)  # few of its sensors or cameras are here
        parts["sparse"] = True
    if (d is not None and d < MIN_DESCRIPTION) or parts.get("outside_coverage"):
        total = 0  # does (almost) none of what you described, or not where you need it
    return {"id": card["id"], "name": card["name"], "fit": total, "verdict": verdict(total), "parts": parts, "gaps": why}


def rank(cards, req):
    out = [score(c, req) for c in cards if in_scope(c)]
    out = [s for s in out if s["fit"] is None or s["fit"] > 0]
    by_id = {c["id"]: c for c in cards}

    def key(s):  # fit first; ties go to providers that name your place, then ones you can use free, then to more proof,
        # and last to a card its provider publishes itself. Publishing adds no fit and nothing is ever paid for (STANDARD 7).
        c = by_id[s["id"]]
        proof = sum({"evidenced": 2, "claimed": 1}.get(v["level"], 0) for v in c["verification"].values())
        return (-(s["fit"] or 0), not s["parts"].get("local"), (c.get("access") or {}).get("cost") != "free", -proof,
                c.get("status") != "provider-maintained")
    return sorted(out, key=key)
