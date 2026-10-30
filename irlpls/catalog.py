"""The agent-facing catalog: five question types, ask families, one compact record per provider.

Shared by tools/build_catalog.py (writes catalog.json / catalog.index.json) and irlpls/mcp_server.py
(irlpls_list_capabilities, irlpls://catalog). build() generates it in-process from Capture Cards plus the adapter
sources; nothing here is hand-written per provider except the FAMILIES membership table. No network.
"""
import datetime
import glob
import json
import re
from pathlib import Path

from . import health as _health
from .readiness import LEVELS, agent_may_act, readiness, readiness_for

HERE = Path(__file__).resolve().parent
SPEC = "2026-10-26"

# ---------------------------------------------------------------- question types
TYPES = [
    {"id": "read", "ask": "What is the value right now?", "route": "live",
     "means": "A gauge, station, monitor or feed is already reporting. Free or cheap, answer in seconds to minutes.",
     "need_shape": "<quantity> at <place> now", "needs": ["place", "(optional) why"]},
    {"id": "watch", "ask": "What does it look like right now?", "route": "live",
     "means": "A public camera or webcam already points at it. A still image, minutes old.",
     "need_shape": "<camera kind> at <place> now", "needs": ["place"]},
    {"id": "send", "ask": "Can someone go and look?", "route": "commission",
     "means": "A person (or a street-level dashcam) is sent to a place to photograph or check it. Hours to days, priced per task.",
     "need_shape": "<photo|video|check> of <thing> at <address> <by when>", "needs": ["address", "deadline", "budget"]},
    {"id": "overhead", "ask": "Can it be imaged from above?", "route": "commission",
     "means": "A satellite is tasked, or a drone pilot flies it. Days; priced per km2 or per mission. Radar works at night and through cloud.",
     "need_shape": "<optical|radar|thermal|drone> image of <area> <by when>", "needs": ["area or address", "deadline", "budget"]},
    {"id": "collect", "ask": "Can I get N examples of X for AI training?", "route": "commission",
     "means": "A dataset is captured to order by contributors. Days to weeks; quote or per-unit price; rights vary per provider.",
     "need_shape": "<N> <clips|photos|recordings> of <scene>, <deadline>", "needs": ["quantity", "deadline", "AI-training rights", "budget"]},
]

# ---------------------------------------------------------------- families: (id, type, title, examples, providers)
FAMILIES = [
    ("river-water-level", "read", "River and stream level, flow, flood",
     ["river level at Comfort, TX now", "is the Thames at Oxford in flood", "water level of the Rhine at Cologne now"],
     ["usgs-water", "uk-ea-flood", "pegelonline", "hubeau-hydrometrie"]),
    ("coast-tide-wave", "read", "Tide, sea level, waves, buoys",
     ["tide height at Galveston, TX now", "wave height off Monterey, CA now"],
     ["noaa-tides", "ndbc-buoys"]),
    ("weather-station", "read", "Weather at a station: temperature, wind, rain, lightning",
     ["temperature and wind at Denver, CO now", "weather in Oulu, Finland now"],
     ["nws", "fmi-open-data", "ndbc-buoys"]),
    ("air-quality", "read", "Air quality, PM2.5, smoke, AQI",
     ["air quality in Lagos, Nigeria now", "PM2.5 near Portland, OR now", "is the smoke bad in Delhi right now"],
     ["airnow", "openaq", "purpleair", "sensor-community", "fmi-open-data"]),
    ("earthquake", "read", "Earthquakes and volcano alert levels",
     ["earthquakes near Tokyo in the last hour", "volcano alert level in New Zealand now"],
     ["usgs-earthquakes", "emsc-seismicportal", "geonet-nz"]),
    ("wildfire-detection", "read", "Active fire detections from satellites",
     ["active fires near Athens, Greece in the last day"],
     ["nasa-firms"]),
    ("ship-positions", "read", "Ship positions (AIS)",
     ["current ship positions in the Gulf of Finland", "vessels near the port of Helsinki now"],
     ["digitraffic-marine-ais"]),
    ("traffic-road-camera", "watch", "Traffic and road cameras",
     ["traffic camera in Helsinki, Finland now", "highway camera on I-5 near Seattle now", "traffic on the Westway, London now"],
     ["fintraffic-weathercams", "tfl-jamcams", "caltrans-cctv", "wsdot-cameras", "windy-webcams"]),
    ("wildfire-camera", "watch", "Wildfire-watch cameras",
     ["camera view of smoke near Lake Tahoe, CA now"],
     ["alertcalifornia"]),
    ("webcam-anywhere", "watch", "Public webcams worldwide",
     ["webcam of Piazza San Marco, Venice now", "beach webcam in Tarifa, Spain now"],
     ["windy-webcams"]),
    ("photo-of-address", "send", "Photo or check of a specific address",
     ["photo of the storefront at 123 Main St, Springfield, IL today", "is the ramp at 40 High St, Leeds blocked, photo please",
      "exterior photos of the building at 500 Market St, San Francisco, CA this week"],
     ["proxypics", "getterdone", "precisely-ground-level-images", "wegolook", "truepic-vision", "doordash-tasks", "snappr",
      "field-nation", "hire-a-human", "mundane", "human-for-ai"]),
    ("store-shelf-check", "send", "Shelf photos, price checks, mystery shopping",
     ["shelf photo of the cereal aisle at a Tesco in Leeds this week", "mystery shop of 20 stores in Berlin, DE"],
     ["field-agent", "pospulse-streetspotr", "mundane", "premise"]),
    ("errand-anywhere", "send", "A person does an errand or checks something in person",
     ["someone to check if the shop at 14 rue Oberkampf, Paris is open, with a photo", "a person in Seoul to photograph a menu board today"],
     ["rentahuman", "airtasker", "freelancer", "execution-market", "errand", "quest", "vangrid", "hire-a-human", "mundane",
      "human-for-ai", "lxt-clickworker"]),
    ("address-verification", "send", "Address verification by field agent",
     ["verify the address 12 Allen Avenue, Ikeja, Lagos with GPS photos"],
     ["dojah-scout"]),
    ("property-scan-3d", "send", "Homeowner phone scan to a 3D property model",
     ["3D model and measurements of the house at 8 Elm St, Dallas, TX"],
     ["hover"]),
    ("street-level-imagery", "send", "Fresh street-level imagery from a dashcam network",
     ["fresh street-level photos of Rue de Rivoli, Paris this week", "current video of the junction at 5th and Main, Austin, TX within 2 days"],
     ["hivemapper", "empiric-earth", "natix"]),
    ("drone-capture", "overhead", "Drone photos, surveys, inspections",
     ["drone photos of my roof at 45 Oak Ave, Austin, TX within 3 days", "drone survey of a 20-hectare site near Perth, AU next week",
      "thermal drone inspection of a solar farm in Spain"],
     ["globhe", "spexi", "dronedeploy-flight-services", "flyguys", "zeitview", "up42"]),
    ("satellite-optical", "overhead", "New optical satellite image of an area",
     ["new satellite image of the Port of Los Angeles within 3 days", "optical satellite photo of a field near Fresno, CA this week"],
     ["skyfi", "planet", "satellogic", "vantor-hub", "skywatch", "up42", "open-cosmos-datacosmos"]),
    ("satellite-radar", "overhead", "Radar (SAR) image, through cloud and at night",
     ["SAR radar image of the port of Rotterdam tonight", "radar image of the flood near Dhaka within 2 days"],
     ["umbra", "capella", "iceye", "skyfi", "copernicus-sentinel"]),
    ("satellite-thermal-hyperspectral", "overhead", "Thermal infrared or hyperspectral satellite image",
     ["thermal satellite image of a city heat island, Phoenix, AZ", "hyperspectral image of a mine site in Chile"],
     ["satvu", "pixxel"]),
    ("free-latest-satellite", "overhead", "Newest free satellite pass (Sentinel)",
     ["latest free Sentinel image of Lake Mead"],
     ["copernicus-sentinel"]),
    ("dataset-egocentric", "collect", "First-person (egocentric) video and robot-learning data",
     ["500 first-person clips of a hand opening a push-bar door, within 7 days", "200 hours of egocentric kitchen video from India",
      "teleoperation demonstrations of folding laundry"],
     ["toloka", "kled", "awign-robotics", "claru", "encord", "human-archive", "lightwheel", "mecka", "micro1", "objectways",
      "unidata", "xdof", "truelabel", "defined-ai"]),
    ("dataset-driving", "collect", "Driving and dashcam video for AI training",
     ["1000 dashcam clips of cyclists at dusk, within 14 days", "driving video of snow on German highways"],
     ["natix", "empiric-earth", "hivemapper"]),
    ("dataset-crowd-media", "collect", "Custom photos, video, audio or speech from a contributor crowd",
     ["2000 photos of handwritten shopping lists, 50 countries", "5000 voice recordings in Swahili, within 3 weeks"],
     ["appen", "defined-ai", "telus-digital", "lxt-clickworker", "shaip", "premise", "kled", "toloka", "prolific"]),
    ("survey-participants", "collect", "Survey or study participants",
     ["300 responses to a 5-minute survey from US adults, within 3 days"],
     ["prolific", "premise", "toloka"]),
]

# ---------------------------------------------------------------- asks irlpls cannot serve (checked against the cards)
UNSUPPORTED = [
    {"ask": "Historical or archive imagery, or past readings ('the river last March')", "why": "irlpls returns only observations made or reported after you ask.",
     "instead": "Use the data provider's own archive API or a search engine."},
    {"ask": "Facts that are published: prices, opening hours on a website, news", "why": "Nothing needs to be observed.", "instead": "Web search."},
    {"ask": "Follow, watch or photograph a specific person", "why": "Refused by default; policy allow_person_targeting is off and an agent must not enable it for a standing mandate.", "instead": "None."},
    {"ask": "Ship or vessel positions outside Finnish waters", "why": "The only AIS card (digitraffic-marine-ais) covers Finland.", "instead": "A global AIS provider is not listed."},
    {"ask": "Aircraft or flight positions", "why": "No card lists a flight-tracking feed.", "instead": "None listed."},
    {"ask": "Traffic cameras outside Finland, London, California, Washington/Oregon", "why": "Only those camera cards are listed.", "instead": "windy-webcams for a public webcam anywhere (not traffic-specific)."},
    {"ask": "River level outside the US, England, Germany/NL/AT/CH/CZ, France", "why": "No other gauge network is listed.", "instead": "A person sent to photograph the river (send), or a satellite pass (overhead)."},
    {"ask": "Official air-quality monitors in a city with no listed station", "why": "openaq and sensor-community are global in name only; station density is uneven, notably in much of Africa.", "instead": "Ask for the nearest station; if none within 25 km, say so."},
    {"ask": "Continuous satellite video, or a satellite image in minutes", "why": "Tasking takes hours to days; Sentinel passes are on a fixed schedule.", "instead": "Radar or optical tasking with a realistic deadline."},
    {"ask": "Inside a private home, or anything needing the occupant's consent", "why": "Providers photograph from public places or with the occupant's cooperation (hover).", "instead": "hover (homeowner scans)."},
    {"ask": "Medical, biometric or identity data about a person", "why": "Out of scope for observations of the physical world.", "instead": "None."},
]



def adapters():
    """What the reference SDK can call, read from the adapter sources (no import, no network)."""
    res = {}
    for f in glob.glob(str(HERE / "adapters" / "*.py")):
        s = Path(f).read_text()
        m = re.search(r'card_id\s*=\s*"([^"]+)"', s)
        if not m or Path(f).name == "base.py":
            continue
        env = re.search(r'\benv\s*=\s*"([^"]+)"', s)
        note = re.search(r'^\s+readiness_note\s*=\s*"([^"]+)"', s, re.M)
        res[m.group(1)] = {"live": bool(re.search(r"def live\(", s)), "commission": bool(re.search(r"def commission\(", s)),
                           "status": bool(re.search(r"def status\(", s)), "env": env.group(1) if env else None,
                           "wallet": bool(re.search(r"^\s+needs_wallet\s*=\s*True", s, re.M)),
                           "note": note.group(1) if note else None}
    return res


def adapter_state(c, ad):
    a = ad.get(c["id"])
    if not a:
        return {"ready": False, "state": "directory-only", "note": "Call the provider's own API; the reference SDK has no adapter."}
    ops = [k for k in ("live", "commission", "status") if a[k]]
    if a.get("wallet"):
        return {"ready": True, "state": "sdk-callable-wallet", "ops": ops, "env": None,
                "note": "No API key: paid calls are signed by your own wallet. The SDK prepares them; your wallet signs."}
    keyed = bool(a["env"])
    return {"ready": True, "state": "sdk-callable-with-key" if keyed else "sdk-callable-no-key", "ops": ops, "env": a["env"],
            "note": ("Set %s." % a["env"]) if keyed else "No key needed."}


def route(c):
    m = c["delivery"]["modes"]
    return "live+commission" if len(m) > 1 else m[0]


def price(c):
    p = c.get("pricing") or {}
    cost = (c.get("access") or {}).get("cost")
    fu = p.get("from_usd") or 0
    return {"access": cost, "free": cost in ("free", "free with key"), "from_usd": fu if fu > 0 else None,
            "published": fu > 0 or cost in ("free", "free with key"), "unit": (p.get("unit") or "")[:80] or None}


def coverage(c):
    cv = c.get("coverage") or {}
    areas = cv.get("areas") or []
    regions = cv.get("regions") or []
    return {"areas": areas if len(areas) <= 12 else areas[:12] + ["+%d more" % (len(areas) - 12)],
            "global": "*" in areas, "listed": bool(areas),
            "regions": [r[:60] for r in regions[:4]] if not areas or len(areas) > 12 else None}


def proof(c):
    v = c.get("verification") or {}
    lv = {k: (v.get(k) or {}).get("level", "none") for k in ("human", "application", "camera", "time", "location", "content", "corroboration")}
    return {"levels": lv, "evidenced": sorted(k for k, x in lv.items() if x == "evidenced")}


def api(c):
    a = c.get("api") or {}
    mcp = a.get("mcp") or {}
    auth = [x.get("type") for x in a.get("auth", [])]
    return {"docs_url": a.get("docs_url"), "base_url": a.get("base_url"), "openapi_url": a.get("openapi_url"),
            "auth": auth, "key_required": (c.get("access") or {}).get("key_required"),
            "oauth_cimd": any(x.get("cimd") for x in a.get("auth", [])),
            "oauth_dynamic_registration": any(x.get("dynamic_registration") for x in a.get("auth", [])),  # deprecated (MCP 2026-07-28)
            "mcp_remote": mcp.get("remote"), "mcp_first_party": bool(mcp.get("first_party")) if mcp else None,
            "mcp_registry": mcp.get("registry_name"), "self_serve": a.get("self_serve")}


_HEALTH = {}


def provider(c, ad, fams):
    acc = c.get("access") or {}
    return {"id": c["id"], "name": c["name"], "short": c.get("short") or c.get("summary", "")[:140], "route": route(c),
            "speed": (c.get("delivery") or {}).get("speed"), "modalities": c["capabilities"]["modalities"],
            "coverage": coverage(c), "price": price(c), "proof": proof(c),
            "gate": acc.get("gate"), "automation_allowed": acc.get("automation_allowed"),
            "ai_training": (c.get("licensing") or {}).get("ai_training"), "resale": (c.get("licensing") or {}).get("resale"),
            "api": api(c), "adapter": adapter_state(c, ad), "readiness": readiness_for(c, ad.get(c["id"])),
            "agent_rails": (c.get("payment") or {}).get("agent_rails") or [], "account": account(c),
            "families": fams, "card": f"/cards/{c['id']}.json",
            "health": _health.summary(c["id"], _HEALTH.get("data"))}


def account(c):
    """The card's account facts, flattened: headless_signup, kyc, agent_may_act (true/false/null). Absent = not stated."""
    a = c.get("account") or {}
    return {"headless_signup": a.get("headless_signup"), "kyc": a.get("kyc"), "agent_may_act": agent_may_act(c)}




def usable(cards):
    """Cards the catalog covers: not defunct, with a delivery mode."""
    return [c for c in cards if c.get("status") != "defunct" and (c.get("delivery") or {}).get("modes")]


def row(p):
    cv = p["coverage"]
    where = "global" if cv["global"] else ",".join(cv["areas"][:6]) if cv["listed"] else "unlisted"
    pr = "free" if p["price"]["free"] else ("$%g" % p["price"]["from_usd"] if p["price"]["from_usd"] else "quote/unpublished")
    return (f'{p["id"]} | {p["route"]} | {where} | {pr} | proof:{",".join(p["proof"]["evidenced"]) or "claimed-only"} | {p["adapter"]["state"]}'
            f' | {p["readiness"]["level"]}')


def index_of(cat):
    """catalog.index.json: types, families with one-line provider rows, unsupported; no full records."""
    provs = cat["providers"]
    idx = {k: cat[k] for k in ("irlpls_catalog", "spec_version", "generated", "what_this_is", "how_to_read", "stats", "legend", "question_types")}
    idx["row_format"] = "id | route | coverage | price | evidenced guarantees | sdk state | readiness"
    idx["families"] = [{**{k: f[k] for k in ("id", "type", "title", "routes", "free_now", "coverage")},
                        "example_needs": [e["need"] for e in f["example_needs"]],
                        "provider_rows": [row(provs[i]) for i in f["providers"]]} for f in cat["families"]]
    idx["unsupported"] = cat["unsupported"]
    idx["full"] = "/catalog.json"
    return idx


def build(cards, top=None):
    """(catalog, index) from a list of Capture Cards. top: optional {need: [[card_id, score]...]} matcher check.
    Raises ValueError if a family names an unknown card or a card belongs to no family."""
    cards = usable(cards)
    _HEALTH["data"] = _health.load()   # the last daily read check, read once per build
    by = {c["id"]: c for c in cards}
    ad = adapters()
    fam_of = {}
    for fid, typ, title, ex, pids in FAMILIES:
        for pid in pids:
            if pid not in by:
                raise ValueError(f"family {fid} names unknown or out-of-scope card {pid}")
            fam_of.setdefault(pid, []).append(fid)
    orphans = sorted(set(by) - set(fam_of))
    if orphans:
        raise ValueError("cards in no family (add them to FAMILIES): " + ", ".join(orphans))
    fams = []
    for fid, typ, title, ex, pids in FAMILIES:
        ps = [by[p] for p in pids]
        areas = sorted({x for c in ps for x in (c["coverage"].get("areas") or []) if x != "*"})
        fams.append({
            "id": fid, "type": typ, "title": title,
            "routes": sorted({m for c in ps for m in c["delivery"]["modes"]}),
            "example_needs": [{"need": n, **({"matcher_top3": top[n], "matcher_agrees": any(x[0] in pids for x in top[n][:1])} if top and n in top else {})} for n in ex],
            "providers": pids,
            "free_now": [c["id"] for c in ps if (c.get("access") or {}).get("cost") in ("free", "free with key")],
            "callable_in_sdk": [c["id"] for c in ps if c["id"] in ad],
            "coverage": {"global": any("*" in (c["coverage"].get("areas") or []) for c in ps), "areas_listed": areas[:40],
                         "note": "Areas are the union of listed ISO codes; cards with no listed areas are not counted. 'global' means at least one card says no country limit, not that every place has a station or an operator."},
        })
    provs = {c["id"]: provider(c, ad, fam_of[c["id"]]) for c in sorted(cards, key=lambda c: c["id"])}
    cat = {
        "irlpls_catalog": 1, "spec_version": SPEC, "generated": datetime.date.today().isoformat(),
        "what_this_is": "The index of what irlpls can be asked. Read question_types, pick a family, take its providers, then fetch only those cards at /cards/<id>.json. Do not load /cards.json whole (about 1 MB).",
        "how_to_read": [
            "1. Classify the ask into one of question_types (read, watch, send, overhead, collect).",
            "2. Find the closest family; its example_needs show the sentence shape that works.",
            "3. For each provider id in the family, check providers[id]: coverage, price, proof, gate, adapter, readiness (how close to one yes).",
            "4. Fetch /cards/<id>.json only for the top two or three, to read the api block and the guarantees' `unknown` text.",
            "5. If no family fits, or coverage excludes the place, answer from `unsupported` and say plainly that nothing listed can do it.",
        ],
        "stats": {"providers": len(provs), "families": len(fams), "live_only": sum(1 for p in provs.values() if p["route"] == "live"),
                  "free_now": sum(1 for p in provs.values() if p["price"]["free"]),
                  "sdk_callable": sum(1 for p in provs.values() if p["adapter"]["ready"]),
                  "with_any_evidenced_guarantee": sum(1 for p in provs.values() if p["proof"]["evidenced"]),
                  "with_listed_coverage_areas": sum(1 for p in provs.values() if p["coverage"]["listed"]),
                  "readiness": {k: sum(1 for p in provs.values() if p["readiness"]["level"] == k) for k in LEVELS},
                  "read_check": {"ok": sum(1 for p in provs.values() if (p["health"] or {}).get("ok") is True),
                                 "failed": sum(1 for p in provs.values() if (p["health"] or {}).get("ok") is False)},
                  "matcher_checked": bool(top)},
        "legend": {"route": "live = already reporting, answer now; commission = captured to order; live+commission = both",
                   "price.from_usd": "null means not published (quote or unknown); never read it as free; price.free is true only when access is free or free with key",
                   "proof": "evidenced = public checkable method; claimed = provider's word; none = not offered",
                   "gate": "none = self-serve; approval/contract/enterprise = needs a sales or approval step before first order",
                   "adapter.state": "sdk-callable-no-key | sdk-callable-with-key | sdk-callable-wallet (your wallet signs paid calls) | directory-only (call the provider yourself using its card's api block)",
                   "readiness": "how close to 'your person just says yes', derived from the card: ready_now (no key, or pays from the person's wallet) | one_consent (one sign-in or one agent sign-up the first time, terms let an agent act) | account_first (make an account and copy a key first) | contract (a sales or approval step first). readiness.line says it in plain words. Only a near-tie breaker, never a fit score",
                   "account": "agent_may_act true/false: the provider's terms say an agent may (not) act for the person, with a source on the card; null: not stated",
                   "agent_rails": "x402 | mpp | spt | link_card | card_on_file_api, as the card states; empty means not stated",
                   "health": "the last daily read check (tools/healthcheck.py): one minimal real read through the SDK adapter. ok, checked_at, status (HTTP) or error (class), reason; null = not checked (no keyless read, or no adapter). Says the call worked that day, nothing about the data's quality",
                   "automation_allowed": "false means do not use; null means the card does not say (treat as unknown, not permitted)"},
        "question_types": TYPES, "families": fams, "providers": provs, "unsupported": UNSUPPORTED,
    }
    return cat, index_of(cat)


_CACHE = {}


def load():
    """The full catalog: irlpls/data/catalog.json if the build copied it, else generated in-process from the registry. Cached."""
    if "cat" not in _CACHE:
        p = HERE / "data" / "catalog.json"
        if p.exists():
            _CACHE["cat"] = json.loads(p.read_text())
        else:
            from .registry import Registry
            _CACHE["cat"] = build(Registry(include_samples=True).all)[0]
    return _CACHE["cat"]


# ---------------------------------------------------------------- query (irlpls_list_capabilities)
TYPE_IDS = [t["id"] for t in TYPES]
ROUTES = ["live", "commission"]
MAX_PROVIDERS = 40
_NAMES = {"united states": "US", "usa": "US", "america": "US", "united kingdom": "GB", "uk": "GB", "britain": "GB", "england": "GB-ENG",
          "finland": "FI", "germany": "DE", "france": "FR", "netherlands": "NL", "austria": "AT", "switzerland": "CH",
          "czechia": "CZ", "czech republic": "CZ", "singapore": "SG", "nigeria": "NG", "india": "IN", "canada": "CA",
          "australia": "AU", "japan": "JP", "china": "CN", "brazil": "BR", "mexico": "MX", "spain": "ES", "italy": "IT",
          "sweden": "SE", "norway": "NO", "denmark": "DK", "ireland": "IE", "poland": "PL", "south africa": "ZA",
          "kenya": "KE", "egypt": "EG", "turkey": "TR", "israel": "IL", "south korea": "KR", "indonesia": "ID",
          "philippines": "PH", "argentina": "AR", "chile": "CL", "colombia": "CO", "new zealand": "NZ", "belgium": "BE",
          "portugal": "PT", "greece": "GR", "ukraine": "UA", "pakistan": "PK", "bangladesh": "BD", "vietnam": "VN",
          "thailand": "TH", "united arab emirates": "AE", "saudi arabia": "SA", "puerto rico": "PR"}


def place_code(place):
    p = (place or "").strip()
    return _NAMES.get(p.lower()) or p.upper()


def _area_hit(areas, code):
    return any(a == code or a.startswith(code + "-") or code.startswith(a + "-") for a in areas if not a.startswith("+"))


def _provider_in(p, code):
    if p["coverage"]["global"]:
        return True
    areas = p["coverage"]["areas"]
    if any(a.startswith("+") for a in areas):       # list was truncated in the catalog: read the whole list from the card
        try:
            from . import card
            areas = (card(p["id"]) or {}).get("coverage", {}).get("areas") or areas
        except Exception:
            pass
    return _area_hit(areas, code)


def _relevant_unsupported(cat, words):
    """Unsupported entries whose text mentions one of the words (place names, family words)."""
    stems = set()
    for w in words:
        stems.update(x for x in re.split(r"[^a-z0-9]+", w.lower()) if len(x) > 3)
    return [u for u in cat["unsupported"]
            if any(re.search(r"(?<![a-z])" + re.escape(x) + r"(?![a-z]|s\b)", (u["ask"] + " " + u["why"]).lower()) for x in stems)]


def query(type=None, family=None, place=None, route=None, free_only=False, detail="index", cat=None):
    """Filter the catalog. Returns a dict; a bad filter value returns {"error", "valid"}; no match is a valid answer."""
    cat = cat or load()
    fam_ids = [f["id"] for f in cat["families"]]
    for name, val, valid in (("type", type, TYPE_IDS), ("family", family, fam_ids), ("route", route, ROUTES),
                             ("detail", detail, ["index", "providers"])):
        if val is not None and val not in valid:
            return {"error": f"unknown {name} {val!r}", "valid": valid}
    provs = cat["providers"]
    code = place_code(place) if place else None
    filtered = bool(type or family or place or route or free_only)
    fams, rows_for = [], {}
    for f in cat["families"]:
        if (type and f["type"] != type) or (family and f["id"] != family) or (route and route not in f["routes"]):
            continue
        keep = [i for i in f["providers"]
                if (not code or _provider_in(provs[i], code))
                and (not free_only or provs[i]["price"]["free"])
                and (not route or route in provs[i]["route"].split("+"))]
        if keep or not (code or free_only):
            fams.append(f)
            rows_for[f["id"]] = keep
    idx = index_of(cat)
    by_idx = {f["id"]: f for f in idx["families"]}
    words = []
    if place:
        iso = code.split("-")[0]
        words += [place] + [n for n, c in _NAMES.items() if c.split("-")[0] == iso]
    if family:
        words += [family.replace("-", " ")] + [f["title"] for f in cat["families"] if f["id"] == family]
    relevant = _relevant_unsupported(cat, words) if words else cat["unsupported"]
    if not fams:
        same_type = [f for f in cat["families"] if (not type or f["type"] == type) and (not family or f["id"] == family) and (not route or route in f["routes"])]
        why = "No listed provider covers this"
        bits = [f"type {type}" if type else None, f"family {family}" if family else None, f"in {place}" if place else None,
                f"route {route}" if route else None, "free to use" if free_only else None]
        why += " (" + ", ".join(b for b in bits if b) + ")." if any(bits) else "."
        return {"spec_version": cat["spec_version"], "question_types": [], "families": [], "message": why,
                "unsupported": relevant or cat["unsupported"],
                "nearest": [{"family": f["id"], "areas_listed": f["coverage"]["areas_listed"], "global": f["coverage"]["global"]} for f in same_type]}
    out_fams = []
    for f in fams:
        g = dict(by_idx[f["id"]])
        if filtered:
            g["provider_rows"] = [row(provs[i]) for i in rows_for[f["id"]]]
            g["free_now"] = [i for i in g["free_now"] if i in rows_for[f["id"]]]
        out_fams.append(g)
    tids = {f["type"] for f in out_fams} if filtered else set(TYPE_IDS)
    out = {"spec_version": cat["spec_version"], "generated": cat["generated"], "stats": cat["stats"],
           "question_types": [t for t in cat["question_types"] if t["id"] in tids],
           "families": out_fams, "unsupported": relevant if filtered or not words else cat["unsupported"]}
    if filtered:
        out.pop("stats")
    if detail == "providers":
        ids = [i for f in fams for i in rows_for[f["id"]]]
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_PROVIDERS:
            ids, out["truncated"] = ids[:MAX_PROVIDERS], True
        out["providers"] = {i: provs[i] for i in ids}
    return out
