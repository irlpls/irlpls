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
from . import isotime
import re

from . import geo
from .need import PROBLEM_MSG, TONIGHT, ask_problem

WEIGHTS = {"description": 0.4, "guarantees": 0.4, "coverage": 0.2}
SPECIFIC = 0.5   # weight of a term that only describes the shot or the spot
MIN_DESCRIPTION = 0.35  # matching less than about a third of the request is no match (fit 0)
STRONG, WEAK = 60, 30  # verdict: fit >= 60 strong, 30-59 weak, below 30 none (nothing here does what you asked)
FLOOR = 29  # at a named place, a fit below WEAK (verdict "none") is never offered as a plan option: it names a provider for something nobody listed can do
LATE_WEEKS = 0.7   # fit multiplier: "weeks" delivery against a deadline of 3 days or less
LATE_DAYS = 0.85   # fit multiplier: "days" delivery against a deadline of a day or less
GEO_ELSEWHERE = 0.3  # coverage value of a card whose explicit areas are elsewhere, when the ask's place has no known country
LIVE_ONLY = 0.85  # fit multiplier for a "now" request on a provider with no live feed
SENSORS = {"optical", "radar", "thermal", "allweather"}  # imaging-sensor terms: custom-task credit never covers them
CUSTOM = 0.75    # credit for an unmentioned specific, from a provider that takes custom capture tasks in the requested format
BULK_SINGLE = 0.5  # fit multiplier: a dataset vendor, for one new photo or clip of a named place or time
AI_UNKNOWN = 0.75  # fit multiplier: the ask wants AI-training rights and the card doesn't state them (forbidden: 0)
SPARSE = 0.85    # fit multiplier: a card whose regions say it is dense only somewhere else ("Global (dense in Europe)")
# A dataset vendor: its capture is datasets made to order by contributors at scale (training-data collection), not one
# visit to one spot. Read from the card's tags.
BULK = r"(?<![a-z])(training data|custom collection|data collection|physical ai|contributors)(?![a-z])"
DATASET = r"\b(dataset|datasets|training data|examples|corpus|samples)\b"


REMOTE_PANEL = r"wherever contributors|says nothing about where a capture is made|where a participant lives"


def bulk_vendor(card):
    return bool(re.search(BULK, " ; ".join(card.get("tags") or []).lower()))


def remote_capture(card):
    """A dataset vendor or an online panel: its people record where they live, not at a spot the requester picks."""
    return bulk_vendor(card) or bool(re.search(REMOTE_PANEL, (card["coverage"].get("notes") or "").lower()))


# An ask only a person on site can answer: whether a place is open or busy, a head count, "send a human". A satellite, a
# dataset vendor (its contributors record wherever they live) or any provider with no live feed to look at can't.
ON_SITE_STATUS = (r"\b(?:is|are|was)\b[^?.]{0,80}\b(?:open|closed|busy|crowded|full)\b|\b(?:count|counting)\b[^?.]{0,40}\b(?:people|persons|cars|visitors|customers|crowds?)\b|"
                  r"\bhow many (?:people|cars|visitors|customers)\b|\bsend (?:a |an )?(?:human|person|someone|people)\b")


def on_site_ask(req):
    return bool(re.search(ON_SITE_STATUS, (req.need or "").lower()))


def place_bound(req):
    """The ask is for one spot's own state, now or by someone there: a dataset vendor's contributors, who record wherever they
    live, can't serve it. A photo or clip due "now", or an on-site question."""
    placed = bool(req.place or req.country_code or getattr(req, "lat", None) is not None)
    return placed and (on_site_ask(req) or (req.mode == "live" and bool({"photo", "video"} & set(req.modalities or []))))


QUESTION = r"^\W*(?:is|are|was|were|will|does|do|can|how|what|where|which)\b|\?\s*$"


def question(req):
    """The ask is phrased as a question about conditions somewhere ("is it raining"); bare keywords ("river") are a directory
    search (Find data), which lists providers whatever the place."""
    return bool(re.search(QUESTION, (req.need or "").lower()))


def country_locked(card):
    """The card's explicit areas name countries (not "*") and it isn't Global: it works only there."""
    areas = [a.lower() for a in card["coverage"].get("areas") or []]
    return bool(areas) and "*" not in areas and not _global(card)


NO_PROVIDER = ("No provider in the catalog covers this: nothing listed can do it where and when you ask. "
               "irlpls will not name a weak default; see `unsupported` in the catalog (irlpls_list_capabilities) for what is not offered.")


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
        return datetime.date.fromisoformat(e) < (today or isotime.today_utc())
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


def antarctic(card):
    """The card's areas or regions name Antarctica."""
    areas = [a.lower() for a in card["coverage"].get("areas") or []]
    return "aq" in areas or "antarctic" in " ".join(card["coverage"]["regions"]).lower()


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
    ai = (card.get("licensing") or {}).get("ai_training")
    if getattr(req, "need_ai_training", False) and ai != "allowed":
        why.append("AI training " + ("forbidden" if ai == "forbidden" else "rights not stated"))
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
    if total and getattr(req, "need_ai_training", False) and ai != "allowed":
        total = 0 if ai == "forbidden" else int(total * AI_UNKNOWN + 0.5)  # asked for AI-training rights: a card that grants them first
        parts["ai_training"] = ai or "unknown"
    # Hard rule-outs: a live question with no place can't use a live feed or a card that works only in some countries; a spot's own state can't
    # come from a dataset vendor; an on-site question can't be answered by a satellite or any card with no live feed and no people.
    if question(req) and not (req.place or req.country_code or getattr(req, "lat", None) is not None
                                   or getattr(req, "anywhere", False) or req.regions) and (country_locked(card) or (req.mode == "live" and "live" in (dl.get("modes") or []))):
        total, parts["needs_place"] = 0, True   # a live feed is read at a place: with none given, nothing can be read yet
        why.append("a live read needs a place; none given")
    if total and place_bound(req) and remote_capture(card):
        total, parts["bulk_elsewhere"] = 0, True
        why.append("dataset vendor: contributors record where they live, not at the spot you name")
    if total and on_site_ask(req) and "live" not in (dl.get("modes") or []) and (card.get("capture") or {}).get("by") == "devices":
        total, parts["no_one_on_site"] = 0, True
        why.append("no one on site and no live feed: can't tell you what is happening there")
    if total and req.mode == "live" and (req.country_code or "").lower() == "aq" and not antarctic(card):
        total, parts["outside_coverage"] = 0, True   # nothing listed reads sensors at the poles
        why.append("no listed sensor or camera covers Antarctica")
    if total and total <= FLOOR and (req.place or req.country_code or getattr(req, "lat", None) is not None) \
            and (req.mode == "live" or not (custom_capture(card) or bulk_vendor(card))) \
            and not (on_site_ask(req) and (card.get("capture") or {}).get("by") == "people"):   # a person on site is who answers "is it open?"   # a commission to a provider that takes custom tasks may be what its card does not list
        total, parts["below_floor"] = 0, True
        why.append("fit too low to offer: it matches too little of what you asked")
    if (d is not None and d < MIN_DESCRIPTION) or parts.get("outside_coverage"):
        total = 0  # does (almost) none of what you described, or not where you need it
    return {"id": card["id"], "name": card["name"], "fit": total, "verdict": verdict(total), "parts": parts, "gaps": why}


def about_provider(text, cards):
    """(card, aspect) when the ask is a question about a provider itself ("Is Prolific verified?", "how much does GetterDone
    cost?"), else None. aspect is "verification" or "price". Such a question is answered from the card, never by routing the
    person to another provider."""
    t = (text or "").lower()
    if not re.match(r"\W*(?:is|are|does|do|how|what|can|who|why|ist|est)\b", t):
        return None
    aspect = "verification" if re.search(r"\b(?:verified|verify|trusted|trustworthy|legit|reliable)\b", t) else \
        "price" if re.search(r"\b(?:cost|costs|price|pricing|how much|fees?|expensive)\b", t) else None
    if not aspect:
        return None
    for c in cards:
        names = {c["id"].lower(), c["id"].lower().replace("-", " "), c["name"].lower(), c["name"].lower().split(" (")[0]}
        if any(len(n) > 3 and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t) for n in names):
            return c, aspect
    return None


def about_answer(card, aspect):
    """What the card itself states about verification or price: the levels it claims (never upgraded) or its price line."""
    if aspect == "price":
        p = card.get("pricing") or {}
        return (f"{card['name']} states: {p.get('model') or 'no price model'}"
                + (f" (from ${p['from_usd']} {p.get('unit') or ''})" if p.get("from_usd") else "") + ". The card page has the full terms.")
    v = card["verification"]
    lv = ", ".join(f"{k} {v[k]['level']}" for k in ("human", "application", "camera", "time", "location", "content", "corroboration") if k in v)
    return (f"{card['name']} states: {lv}. 'claimed' is the provider's own statement; irlpls verifies nothing itself. "
            "The card page lists the method and what is still uncertain for each.")


def unsupported(req, ranked):
    """NO_PROVIDER when nothing ranked fits (no result, or the best fit is below WEAK): say so instead of naming a weak default."""
    if ask_problem(req):
        return PROBLEM_MSG[ask_problem(req)]
    return NO_PROVIDER if not ranked or max((s["fit"] or 0) for s in ranked) < WEAK else None


def rank(cards, req):
    if about_provider(req.need, [c for c in cards if in_scope(c)]):
        return []   # a question about a provider is answered from its card, never routed to a provider
    if ask_problem(req):
        return []   # not an ask irlpls can route (see need.ask_problem): unsupported() says why
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
