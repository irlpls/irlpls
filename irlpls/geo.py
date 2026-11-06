"""Places: geocode a place name, and decide whether a card's coverage text includes it.

Geocoding uses OpenStreetMap Nominatim (free; keep to about one request per second and identify yourself).
Fallbacks: Photon, then the bundled offline gazetteer (marked approximate). Results are cached in memory and on disk.
"""
import json
import math
import os
import pathlib
import re
import threading

from . import http

_cache = {}

# Coverage text on cards is free-form ("USA", "US metros", "EU cities", "Global"). Map common phrases to ISO country codes.
_REGION_CODES = {
    "global": "*", "worldwide": "*", "world": "*",
    "us": {"us"}, "usa": {"us"}, "united states": {"us"}, "u.s.": {"us"}, "lower 48": {"us"}, "contiguous": {"us"},
    "canada": {"ca"}, "uk": {"gb"}, "united kingdom": {"gb"}, "britain": {"gb"},
    "eu": {"at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt", "ro", "sk", "si", "es", "se"},
    "europe": {"at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt", "ro", "sk", "si", "es", "se", "gb", "no", "ch", "is"},
    "portugal": {"pt"}, "spain": {"es"}, "germany": {"de"}, "france": {"fr"}, "australia": {"au"}, "mexico": {"mx"},
    "euro-mediterranean": {"at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "ie", "it", "lv", "lt", "lu", "mt", "nl", "pl", "pt", "ro", "sk", "si", "es", "se", "gb", "no", "ch", "is",
                           "al", "ba", "me", "mk", "rs", "xk", "tr", "il", "lb", "sy", "ps", "jo", "eg", "ly", "tn", "dz", "ma"},
    "oecd": {"au", "at", "be", "ca", "cl", "co", "cr", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "is", "ie", "il", "it", "jp", "kr", "lv", "lt", "lu", "mx", "nl", "nz", "no", "pl", "pt", "sk", "si", "es", "se", "ch", "tr", "gb", "us"},
}


US_STATES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida", "georgia",
             "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
             "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
             "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
             "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
             "wisconsin", "wyoming"]


US_ABBR = dict(zip("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
                   US_STATES[:7] + ["delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
                                    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
                                    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
                                    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
                                    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
                                    "wisconsin", "wyoming", "district of columbia"]))
_TRAILING_ABBR = re.compile(r",\s*([A-Za-z]{2})\.?\s*(?:\d{5}(?:-\d{4})?)?\s*$")


def _us_form(place):
    """"Boston, MA" -> "Boston, Massachusetts, USA" (a trailing two-letter US state, upper case), else None. A bare "Boston, MA"
    otherwise lets the geocoder pick Boston, Lincolnshire."""
    m = _TRAILING_ABBR.search(place.strip())
    if not m or m.group(1) not in US_ABBR:
        return None
    return place.strip()[:m.start()] + ", " + US_ABBR[m.group(1)].title() + ", USA"


_DISK_MAX = 2000
_lock = threading.Lock()


def _disk_path():
    return pathlib.Path(os.environ.get("IRLPLS_HOME", pathlib.Path.home() / ".irlpls")) / "geocode-cache.json"


def _disk_get(key):
    try:
        v = json.loads(_disk_path().read_text()).get(key)
        return v if isinstance(v, dict) and "lat" in v and "lon" in v else None
    except (OSError, ValueError, AttributeError):
        return None


def _disk_put(key, val):
    """Successful lookups only; best-effort (a read-only home just means no disk cache)."""
    try:
        p = _disk_path()
        with _lock:
            try:
                d = json.loads(p.read_text())
                d = d if isinstance(d, dict) else {}
            except (OSError, ValueError):
                d = {}
            d.pop(key, None)
            d[key] = val
            while len(d) > _DISK_MAX:
                d.pop(next(iter(d)))
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(d))
            os.replace(tmp, p)
    except OSError:
        pass


def _nominatim(q):
    """(record or None, failed). One request, no retry: http.get under provider="nominatim" is paced (1 req/s) and a
    429 pauses that provider process-wide (irlpls/ratelimit.py), so a rate-limited Nominatim is not hammered."""
    try:
        res = http.get("https://nominatim.openstreetmap.org/search",
                       params={"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en"},
                       provider="nominatim")
    except OSError:   # rate limit (HTTP 429), timeout or no network: the place is unknown for now, not "not found"
        return None, True
    if not res:
        return None, False
    r = res[0]
    addr = r.get("address") or {}
    iso = [addr[k] for k in ("ISO3166-2-lvl4", "ISO3166-2-lvl3", "ISO3166-2-lvl5", "ISO3166-2-lvl6") if addr.get(k)]
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "name": r.get("display_name", q),
            "country_code": addr.get("country_code", "").lower(), "state": (addr.get("state") or "").lower() or None,
            "iso": iso}, False


def _photon(q):
    """Second keyless service: photon.komoot.io (OSM data; fair use, throttled when extensive, no key). (record, failed)."""
    try:
        res = http.get("https://photon.komoot.io/api/", params={"q": q, "limit": 1, "lang": "en"}, provider="photon")
    except OSError:
        return None, True
    try:
        f = (res.get("features") or [])[0]
        pr, (lon, lat) = f.get("properties") or {}, f["geometry"]["coordinates"][:2]
        cc = (pr.get("countrycode") or "").lower()
        state = (pr.get("state") or "").lower() or None
        iso = []
        if cc == "us" and state in US_STATES:
            iso = ["US-" + list(US_ABBR)[US_STATES.index(state)]]
        name = ", ".join(x for x in (pr.get("name"), pr.get("state"), pr.get("country")) if x) or q
        return {"lat": float(lat), "lon": float(lon), "name": name, "country_code": cc, "state": state, "iso": iso}, False
    except (IndexError, KeyError, TypeError, ValueError, AttributeError):
        return None, False


def _offline(place):
    """Last resort: the bundled GeoNames table (irlpls/places.py). Approximate: big cities only, no street level."""
    try:
        from . import places
        g = places.lookup(place)
    except Exception:   # a missing or corrupt table must never break planning
        return None
    if not g:
        return None
    g = {k: v for k, v in g.items() if k != "source"}
    g["approximate"] = True
    return g


def geocode(place):
    """Return {lat, lon, name, country_code, state, subdivision, subdivisions} for a place name, or None.

    Chain: memory cache, disk cache, Nominatim, Photon, then the bundled offline gazetteer. A gazetteer answer adds
    "approximate": True and is never cached (a live answer should replace it once a service recovers)."""
    key = place.strip().lower()
    if key in _cache:
        return _cache[key]
    hit = _disk_get(key)
    if hit:
        _cache[key] = hit
        return hit
    out, failed = None, False
    us = _us_form(place)
    qs = candidates(place)
    while qs:
        q = qs.pop(0)
        r, f = _nominatim(q)
        if r is None and f:
            r, f2 = _photon(q)
            f = f and f2
        failed = failed or f
        if r:
            if us and q != us and r["country_code"] != "us":
                qs.insert(0, us)   # "Boston, MA" came back as Boston, England: ask again with the state spelt out
                us = None
                continue
            iso = r["iso"]
            out = {"lat": r["lat"], "lon": r["lon"], "name": r["name"], "country_code": r["country_code"],
                   "state": r["state"], "subdivision": iso[0].upper() if iso else None,
                   "subdivisions": [x.upper() for x in iso]}
            break
    if out:
        _cache[key] = out
        _disk_put(key, out)
        return out
    off = _offline(place)
    if off:
        return off
    if not failed:   # a geocoder outage must not be remembered as "no such place"
        _cache[key] = None
    return None


# A landmark prefix a geocoder may not know as written ("Port of Los Angeles, CA", "Gulf of Finland, Finland").
_LANDMARK = re.compile(r"^(?:the\s+)?(?:port|harbou?r|gulf|bay|lake|cape|mount|mt\.?|strait|straits|sea|isle|island|river|"
                       r"coast|airport|beach)\s+(?:of\s+(?:the\s+)?)?", re.I)


def candidates(place):
    """What to ask the geocoder, in order: the place as written, then without a landmark prefix ("Port of"), then without
    its first part ("Joe's Diner, 50 Elm St, Burlington, VT" -> "50 Elm St, Burlington, VT"). Never a bare country code."""
    out, p = [], place.strip()
    for q in (p, _LANDMARK.sub("", p)):
        if q and q not in out:
            out.append(q)
    parts = [x.strip() for x in p.split(",") if x.strip()]
    while len(parts) > 2:
        parts = parts[1:]
        q = ", ".join(parts)
        if q not in out:
            out.append(q)
    return out


def covers(card, country_code, state=None, subdivision=None):
    """True / False if the card's coverage clearly does / doesn't include the place, None if unclear.
    coverage.areas (ISO 3166 codes, "*" = no country limit) decides when present; otherwise the text is read.
    A card listing subdivisions (ISO 3166-2: "GB-ENG", "US-GA", "KR-11") covers a place in one of them: known from the
    geocoder's ISO code (`subdivision`) or its state name ("england", "georgia", "seoul"), in any country."""
    areas = [x.lower() for x in card["coverage"].get("areas") or []]
    if areas and country_code:
        country_code = country_code.lower()
        if "*" in areas:
            return True
        from .need import SUBDIVISIONS, subdivision_code
        subs = [x for x in areas if x.startswith(country_code + "-")]
        if country_code in areas:
            return True
        if subs:
            sub = (subdivision or subdivision_code(country_code, state) or "").lower()
            if sub:
                return sub in subs
            if state:
                return any(SUBDIVISIONS.get(x.upper()) == state.lower() for x in subs)
            return None
        return False
    regions = " ".join(card["coverage"]["regions"]).lower()
    if country_code == "us" and state:
        named = [s for s in US_STATES if re.search(r"(?<![a-z])" + s + r"(?![a-z])", regions)]
        if named and not re.search(r"(?<![a-z])(nationwide|all states|lower 48|united states(?! \())", regions):
            return state in named
    text = " ".join(card["coverage"]["regions"] + [card["coverage"].get("notes", "")]).lower()
    found = set()
    for phrase, codes in _REGION_CODES.items():
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text):
            if codes == "*":
                return True
            found |= codes
    if not found:
        return None
    return country_code in found


def bbox(lat, lon, radius_km):
    """(west, south, east, north) around a point."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def distance_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))
