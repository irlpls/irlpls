"""Places: geocode a place name, and decide whether a card's coverage text includes it.

Geocoding uses OpenStreetMap Nominatim (free; keep to about one request per second and identify yourself).
Results are cached in memory for the session.
"""
import math
import re

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


def geocode(place):
    """Return {lat, lon, name, country_code} for a place name, or None."""
    key = place.strip().lower()
    if key in _cache:
        return _cache[key]
    out = None
    for q in candidates(place):
        res = http.get("https://nominatim.openstreetmap.org/search",
                       params={"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en"},
                       provider="nominatim")
        if res:
            r = res[0]
            addr = r.get("address") or {}
            iso = [addr[k] for k in ("ISO3166-2-lvl4", "ISO3166-2-lvl3", "ISO3166-2-lvl5", "ISO3166-2-lvl6") if addr.get(k)]
            out = {"lat": float(r["lat"]), "lon": float(r["lon"]), "name": r.get("display_name", place),
                   "country_code": addr.get("country_code", "").lower(), "state": (addr.get("state") or "").lower() or None,
                   "subdivision": iso[0].upper() if iso else None, "subdivisions": [x.upper() for x in iso]}
            break
    _cache[key] = out
    return out


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
