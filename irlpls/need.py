"""A structured request for NEW real-world data. Ideally your own LLM fills this in (see schema/request.schema.json);
parse() is a keyword stand-in so the SDK works with no model at all.

Two kinds of request:
  live        data reporting now, pulled within minutes ("river level near Austin right now")
  commission  you specify a capture and a provider makes it ("500 clips of people opening bakery doors within 5 days")

Terms are what the provider must be able to do. Some terms are only `specifics` of the shot or the spot: the words after
"photos of" / "clips of", after "at" / "near", and words no card mentions at all. Specifics count half (see match.py).
Place names, addresses, states and countries are not terms; they set coverage instead.
Time words ("last hour", "this morning", "today") are not terms either; they set timing (mode and deadline).
"today" is live for a question about conditions ("good for tubing today?") and a deadline when a capture is asked for
("photo of the storefront today"): capture nouns and verbs (CAPTURE), or a quantity or dataset, mean commission.
A bare deadline counts too: "500 clips, 7 days" is due within 7 days ("last 7 days", "every 7 days", "1 hour after",
"2 hours of footage" are not deadlines).
A road scene (a road user, a manoeuvre or road works, or a street under some condition: "cyclist at dusk", "right turn,
snowy afternoon", "flooded street") asked for as a capture is a driving scene: it asks softly (as a specific) for
"driving" footage, and its conditions (dusk, night, snow, flooding) are specifics of the shot, not capabilities.
Imaging sensor is part of the description: radar (SAR, "through clouds"), thermal (infrared, heat signature) or optical.
A radar image is overhead imagery, not a photo; a thermal image is not a photo; a plain satellite or drone "image" asks
softly (as a specific) for optical.
Mirrored in src/match.js; keep them in step.
"""
import re
import unicodedata
from dataclasses import dataclass, field, asdict

STOP = set("short record good bad okay ok safe enough nice decent worth go going right now just please there here whether currently a an the of in at for to and or on with me my some who can i need want data something get collect near around from by is are be that it this under below max less than when where what proof how high low much many does do did was were has have had its into over about see send show tell take their them our your you any outside inside beside off".split())
MEDIA = set("photo photos picture pictures pic pics image images imagery snap snaps shot shots video videos footage clip clips audio recording recordings".split())
# Everyday phrases rewritten into the words cards use.
PHRASES = [(r"\bfirst[- ]person\b|\bpoint[- ]of[- ]view\b|\bpov\b|\bhead[- ]mounted\b", "egocentric"),
           (r"\bstreet[- ]level\b", "street"), (r"\bbacked up\b", "traffic"), (r"\bcomputer vision\b", "computervision"),
           (r"\bthrough (?:the )?clouds?\b|\ball[- ]weather\b|\bcloud[- ]penetrating\b", "allweather")]  # same rewrite as match.ALLWEATHER
# Imaging sensor. Radar sees through cloud and at night; thermal measures heat; optical is an ordinary camera.
RADAR = r"\b(radar|sar|synthetic aperture)\b|\bthrough (the )?clouds?\b|\ball[- ]weather\b|\bcloud[- ]penetrating\b"
THERMAL = r"\b(thermal|infra-?red|heat signatures?)\b"
# Capitalised words are names (rivers, streets, venues), except these common nouns, which say what kind of place it is.
GENERIC = set("river creek lake beach bridge harbor harbour port coast airport".split())
SPOT = {"at", "near", "around", "outside", "inside", "beside", "off"}            # "... at a bakery": where, not what
BOUNDARY = set("of for with to and or in on by from that which when where while during within under so but".split())
MODALITIES = {
    "photo": r"\b(photo|picture|image|snap|shot)s?\b|imagery",
    "video": r"\b(video|footage|film|clip)s?\b",
    "audio": r"\b(audio|sound|recording|noise)\b",
    "aerial": r"\b(aerial|drone|satellite|roofs?|overhead)\b",
    "sensor": r"\b(sensor|measure|air quality|temperature|pm2|humidity|weather|river|stream|flood|water level|gauge|wind|rain)\b",
    "text-report": r"\b(report|inspect|audit|check|survey)\b",
}
EXPLICIT_SENSOR = r"\b(sensor|measure\w*|readings?|gauge|pm2)\b"
GUARANTEES = {
    "human": r"\b(real person|human|identity|id check|vetted)\b",
    "application": r"\b(app attest|integrity|genuine app)\b",
    "camera": r"\b(camera|original photo|not a screen|sensor-signed|unedited|authentic)\b",
    "time": r"\b(timestamps?|time|when)\b",
    "location": r"\b(gps|location|geotag|where|exact place|on[- ]site)\b",
    "content": r"\b(fabricat\w*|staged|fake|ai[- ]generated|labell?ed|authentic)\b",
    "corroboration": r"\b(corroborat\w*|cross[- ]?check|independent|multiple sources|second source)\b",
}
DEFAULT_PROOF = ("time", "location", "content")
VERIFY = r"\b(verified|verify|proof|prove|trusted|trustworthy|guaranteed?|provenance|real)\b"
GWORDS = re.compile(r"^(next|within|minutes?|hours?|days?|weeks?|tomorrow|examples?|dataset|samples?|verified|verify|proof|prove|trusted|guaranteed?|provenance|real|timestamps?|time|gps|location|geotag|authentic|recent|today|fresh|freshest|new|newest|hourly|now|current|currently|live|latest|"
                    r"last|past|ago|recently|since|yesterday|tonight|morning|afternoon|evening|months?|years?|daily|weekly|monthly|every)$")  # time words: timing, not description
LIVE = r"\b(now|right now|live|current(ly)?|latest|at the moment|real[- ]?time)\b"
# "tonight" / "overnight": a live feed answers it, anything else is a capture due within hours (match.timing)
TONIGHT = r"\b(tonight|overnight)\b"
TONIGHT_MINUTES = 720
# "today" is ambiguous. Asking for a capture (a photo, a clip, an inspection; or to send, take, record...) makes it the
# deadline for that capture: a commission. Otherwise it asks about conditions today, which a live feed answers.
CAPTURE = r"\b(photo|picture|image|snap|shot|video|footage|film|clip|recording|photograph|orthomosaic|inspection|selfie)s?\b|\bimagery\b|" \
          r"\b(send|take|capture|shoot|record|photograph|inspect|visit)\b"
TODAY = r"\btoday\b"
# A question about conditions (a reading, a tide, a quake) with nothing to capture, no dataset and no deadline: what is
# reporting now answers it ("tide at Santa Monica", "earthquake Alaska", "air quality London").
CONDITION = r"\b(tides?|earthquakes?|quakes?|waves?|swell|surf|air|ships?|vessels?)\b"
LOOKBACK = r"\b(?:last|past)\s+(?:(?:\d+|few|couple of)\s+)?(?:minute|min|hour|hr|day|week)s?\b|\bago\b|\bthis (?:morning|afternoon|evening)\b|\byesterday\b|\bearlier today\b"  # "in the last hour": recent reports
# "7 days" on its own, as in "500 clips, 7 days"; not a lookback, a repeat, a lag or a duration of footage
BARE_DEADLINE = r"(?<![\w.$-])(?<!last )(?<!past )(?<!every )(\d{1,6})[\s-]*(minute|min|hour|hr|day|week)s?\b(?!\s+(?:ago|after|of|old|later|earlier|before|long)\b)"
# A road scene: a road user, a manoeuvre, road works; or a street or road under some condition (ROAD + CONDITIONS).
ROAD_SCENE = r"\b(cyclists?|bicycl\w*|bikes?|pedestrians?|turns?|merg(?:e|es|ing)|intersections?|crosswalks?|roundabouts?|roadworks?|work zones?)\b"
ROAD = r"\b(streets?|roads?|highways?|freeways?)\b"
CONDITIONS = set("dusk dawn twilight sunset sunrise night dark glare snow snowy icy ice fog foggy rain rainy wet flooded flooding".split())
MAX_QUANTITY = 10 ** 9
MAX_MINUTES = 5256000  # ten years
UNIT_MIN = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440, "week": 10080}
# A place after a preposition: "in Austin, TX", "near the Bay Bridge", "off Cape Cod, Massachusetts", "of the Port of
# Rotterdam, Netherlands", "in the Gulf of Finland, Finland". A name runs over capitalised words, commas and the small
# words inside names ("Port of Los Angeles", "Rio de Janeiro"); "the" may come first.
_NAME = r"[A-Z][\w'.-]*\.?"
PLACE = re.compile(r"\b(?:in|at|near|around|on|off|of)\s+(?:the\s+)?(" + _NAME
                   + r"(?:(?:,\s*|\s+(?:of(?:\s+the)?|de|del|da|di|la|le|du|am|upon|sur)\s+|\s+)" + _NAME + r")*)")
# Languages are not places ("in Spanish", "5000 voice recordings in Swahili"): a place made only of these words is dropped.
LANGUAGES = set("""afrikaans albanian amharic arabic armenian azerbaijani basque belarusian bengali bosnian bulgarian burmese
cantonese catalan cebuano chinese croatian czech danish dari dutch english estonian farsi filipino finnish flemish french
gaelic galician georgian german greek gujarati haitian hausa hebrew hindi hmong hungarian icelandic igbo indonesian irish
italian japanese javanese kannada kazakh khmer kinyarwanda korean kurdish kyrgyz lao latin latvian lingala lithuanian
luxembourgish macedonian malagasy malay malayalam maltese mandarin maori marathi mongolian nepali norwegian oromo pashto
persian polish portuguese punjabi quechua romanian russian serbian shona sindhi sinhala slovak slovenian somali spanish
sundanese swahili swedish tagalog tajik tamil tatar telugu thai tibetan tigrinya turkish turkmen twi ukrainian urdu uyghur
uzbek vietnamese welsh wolof xhosa yiddish yoruba zulu sign language languages accent accents dialect dialects""".split())
# No place limit: a dataset that can be captured anywhere. Never geocoded.
ANYWHERE = r"\b(?:anywhere(?: in the world)?|any (?:country|countries|location|locations|place|places|region|city|cities)|" \
           r"world-?wide|all over the world|around the world|globally|no particular (?:place|location)|" \
           r"(?:the )?(?:place|location) (?:doesn't|does not|don't|do not) matter|(?:doesn't|does not) matter where)\b"
# Asks a web page answers (a menu, opening hours, prices, a phone number), unless something is to be captured on site.
WEB_FACT = r"\b(?:menus?|opening hours|opening times|hours of operation|business hours|open hours|what time (?:does|do|is) [^?]{0,40}\b(?:open|close)s?|" \
           r"price list|prices?|pricing|phone numbers?|reviews?|ratings?|websites?|timetables?|schedules?|showtimes?|news)\b"
ON_SITE = r"\b(?:photo|picture|image|snap|shot|video|footage|film|clip|recording|photograph|selfie|scan)s?\b|\bimagery\b|" \
          r"\b(?:send|take|capture|shoot|record|inspect|visit|go|check on site|in[- ]person|on[- ]site|shelf|shelves|aisle|posted|sign|signs|" \
          r"board|boards|display|displays|tags?|labels?|queue|line|wait|crowd|crowded|busy|pump|in[- ]store|outside|inside)\b"
# ISO 3166-2 subdivisions cards may list (coverage.areas "GB-ENG", "KR-11"), with the name a geocoder gives (lower case).
SUBDIVISIONS = {"GB-ENG": "england", "GB-SCT": "scotland", "GB-WLS": "wales", "GB-NIR": "northern ireland",
                "KR-11": "seoul", "KR-26": "busan", "KR-41": "gyeonggi-do", "AU-NSW": "new south wales", "AU-VIC": "victoria",
                "AU-QLD": "queensland", "AU-WA": "western australia", "AU-SA": "south australia", "AU-TAS": "tasmania",
                "CA-ON": "ontario", "CA-QC": "quebec", "CA-BC": "british columbia", "CA-AB": "alberta",
                "DE-BY": "bavaria", "DE-BE": "berlin", "DE-NW": "north rhine-westphalia", "ES-CT": "catalonia",
                "FR-IDF": "ile-de-france", "IT-LOM": "lombardy", "JP-13": "tokyo", "IN-DL": "delhi", "IN-MH": "maharashtra",
                "MX-CMX": "mexico city", "BR-SP": "sao paulo", "CN-BJ": "beijing", "CN-SH": "shanghai"}
# A street address with its city and state: "123 Main St, Springfield, IL".
ADDRESS = re.compile(r"\b\d+[A-Za-z]?\s+(?:[A-Z0-9][\w'.-]*\s+){0,3}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Pl|Place|Ct|Court|Hwy|Highway|Pkwy|Parkway)\b\.?(?:,\s*[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)*)*")
ADDRESS_LC = re.compile(r"\b\d+[a-z]?\s+(?:[a-z0-9'.-]+\s+){1,3}(?:st|ave|rd|blvd|dr|ln|hwy|pkwy)\b\.?(?:,\s*[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)*)*")  # "123 main st"
STATES = {"al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado", "ct": "connecticut",
          "dc": "district of columbia", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois",
          "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
          "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska",
          "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
          "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania", "ri": "rhode island",
          "sc": "south carolina", "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia",
          "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming"}
SUBDIVISIONS.update({"US-" + k.upper(): v for k, v in STATES.items()})


def subdivision_code(country_code, state):
    """The ISO 3166-2 code ("GB-ENG", "US-GA", "KR-11") for a geocoder's state name in a country, or None."""
    if not country_code or not state:
        return None
    pre, state = country_code.upper() + "-", state.lower()
    return next((k for k, v in SUBDIVISIONS.items() if k.startswith(pre) and v == state), None)
# Big cities named without a state or country (offline; geocoding, when on, overrides): (country, US state).
CITIES = {"Los Angeles": ("us", "california"), "San Francisco": ("us", "california"), "Seattle": ("us", "washington"),
          "New York City": ("us", "new york"), "Chicago": ("us", "illinois"), "Houston": ("us", "texas"),
          "Miami": ("us", "florida"), "London": ("gb", None), "Manchester": ("gb", None), "Paris": ("fr", None),
          "Berlin": ("de", None), "Madrid": ("es", None), "Rome": ("it", None), "Amsterdam": ("nl", None),
          "Tokyo": ("jp", None), "Osaka": ("jp", None), "Seoul": ("kr", "seoul"), "Beijing": ("cn", None),
          "Shanghai": ("cn", None), "Delhi": ("in", None), "Mumbai": ("in", None), "Sydney": ("au", None),
          "Melbourne": ("au", None), "Toronto": ("ca", None), "Vancouver": ("ca", None), "Mexico City": ("mx", None),
          "São Paulo": ("br", None), "Nairobi": ("ke", None), "Lagos": ("ng", None), "Cairo": ("eg", None),
          "Dubai": ("ae", None), "Helsinki": ("fi", None), "Espoo": ("fi", None), "Tampere": ("fi", None),
          "Oxford": ("gb", None), "Cambridge": ("gb", None), "Leeds": ("gb", None), "Birmingham": ("gb", None),
          "Liverpool": ("gb", None), "Glasgow": ("gb", None), "Edinburgh": ("gb", None), "Bristol": ("gb", None),
          "Cornwall": ("gb", None), "Yorkshire": ("gb", None), "Thames": ("gb", None), "Severn": ("gb", None),
          "Cologne": ("de", None), "Munich": ("de", None), "Hamburg": ("de", None), "Frankfurt": ("de", None),
          "Rhine": ("de", None), "Danube": ("at", None), "Rotterdam": ("nl", None), "Venice": ("it", None),
          "Milan": ("it", None), "Lisbon": ("pt", None), "Porto": ("pt", None), "Barcelona": ("es", None),
          "Vienna": ("at", None), "Zurich": ("ch", None), "Stockholm": ("se", None), "Oslo": ("no", None),
          "Copenhagen": ("dk", None), "Dublin": ("ie", None), "Athens": ("gr", None), "Istanbul": ("tr", None),
          "Bangalore": ("in", None), "Ganges": ("in", None), "Kyoto": ("jp", None), "Hong Kong": ("hk", None),
          "Johannesburg": ("za", None), "Cape Town": ("za", None), "Accra": ("gh", None), "Nile": ("eg", None),
          "Buenos Aires": ("ar", None), "Santiago": ("cl", None), "Bogota": ("co", None), "Lima": ("pe", None),
          "Monterey": ("us", "california"), "LA": ("us", "california"), "Austin": ("us", "texas"),
          "Denver": ("us", "colorado"), "Boston": ("us", "massachusetts"), "Portland": ("us", "oregon"),
          "Lake Michigan": ("us", None), "Great Lakes": ("us", None), "Baltic Sea": ("se", None), "North Sea": ("gb", None),
          "Kyiv": ("ua", None), "Kiev": ("ua", None), "Kharkiv": ("ua", None), "Lviv": ("ua", None), "Odesa": ("ua", None),
          "Odessa": ("ua", None), "Moscow": ("ru", None), "Bangkok": ("th", None), "Prague": ("cz", None),
          "Budapest": ("hu", None), "Beirut": ("lb", None), "Gaza": ("ps", None), "Tel Aviv": ("il", None),
          "Jerusalem": ("il", None),
          # landmarks and US cities named without a state ("traffic cam on the Golden Gate Bridge", "Tampa after the storm")
          "Golden Gate Bridge": ("us", "california"), "Golden Gate": ("us", "california"), "Bay Bridge": ("us", "california"),
          "Hollywood": ("us", "california"), "Yosemite": ("us", "california"), "San Diego": ("us", "california"),
          "San Jose": ("us", "california"), "Oakland": ("us", "california"), "Sacramento": ("us", "california"),
          "Space Needle": ("us", "washington"), "Bellevue": ("us", "washington"), "Tacoma": ("us", "washington"),
          "Times Square": ("us", "new york"), "Central Park": ("us", "new york"), "Brooklyn Bridge": ("us", "new york"),
          "Statue of Liberty": ("us", "new york"), "Manhattan": ("us", "new york"), "Brooklyn": ("us", "new york"),
          "Tampa": ("us", "florida"), "Orlando": ("us", "florida"), "Jacksonville": ("us", "florida"),
          "Key West": ("us", "florida"), "Atlanta": ("us", "georgia"), "Dallas": ("us", "texas"),
          "San Antonio": ("us", "texas"), "Phoenix": ("us", "arizona"), "Grand Canyon": ("us", "arizona"),
          "Las Vegas": ("us", "nevada"), "Philadelphia": ("us", "pennsylvania"), "Pittsburgh": ("us", "pennsylvania"),
          "New Orleans": ("us", "louisiana"), "Nashville": ("us", "tennessee"), "Detroit": ("us", "michigan"),
          "Minneapolis": ("us", "minnesota"), "Salt Lake City": ("us", "utah"), "Honolulu": ("us", "hawaii"),
          "Anchorage": ("us", "alaska"), "Charlotte": ("us", "north carolina"), "Baltimore": ("us", "maryland"),
          "Cleveland": ("us", "ohio"), "Columbus": ("us", "ohio"), "St. Louis": ("us", "missouri"),
          "Kansas City": ("us", "missouri"), "Indianapolis": ("us", "indiana"), "Milwaukee": ("us", "wisconsin"),
          "Big Ben": ("gb", None), "Tower Bridge": ("gb", None), "Eiffel Tower": ("fr", None), "Colosseum": ("it", None),
          "Brandenburg Gate": ("de", None), "Sydney Opera House": ("au", None),
          # coastal and other US cities often named alone ("tide at Santa Monica")
          "Santa Monica": ("us", "california"), "Long Beach": ("us", "california"), "Malibu": ("us", "california"),
          "Santa Cruz": ("us", "california"), "Berkeley": ("us", "california"), "Pasadena": ("us", "california"),
          "Santa Barbara": ("us", "california"), "Galveston": ("us", "texas"), "Savannah": ("us", "georgia"),
          "Miami Beach": ("us", "florida"), "Cape Cod": ("us", "massachusetts"), "Myrtle Beach": ("us", "south carolina")}
# Countries named in the text (offline; geocoding, when on, overrides): every country and territory by its English name and
# common short forms, as an ISO 3166-1 alpha-2 code. Keys are matched as written; a name inside a longer one that is
# elsewhere ("Wales" in "New South Wales", "Guinea" in "Papua New Guinea") comes after it. Kept in parity with src/match.js.
COUNTRIES = {"New South Wales": "au", "New England": "us", "USA": "us", "United States Minor Outlying Islands": "um",
             "United States": "us", "U.S. Virgin Islands": "vi", "U.S.": "us", "Canada": "ca", "Mexico": "mx", "UK": "gb",
             "United Kingdom": "gb", "England": "gb", "Scotland": "gb", "Wales": "gb", "Britain": "gb",
             "Northern Ireland": "gb", "Ireland": "ie", "France": "fr", "Germany": "de", "Spain": "es", "Portugal": "pt",
             "Italy": "it", "Netherlands": "nl", "Belgium": "be", "Switzerland": "ch", "Austria": "at", "Poland": "pl",
             "Sweden": "se", "Norway": "no", "Denmark": "dk", "Finland": "fi", "Greece": "gr", "Turkey": "tr", "Japan": "jp",
             "China": "cn", "India": "in", "Singapore": "sg", "Indonesia": "id", "Philippines": "ph", "South Korea": "kr",
             "North Korea": "kp", "Korea": "kr", "Australia": "au", "New Zealand": "nz", "Brazil": "br", "Argentina": "ar",
             "Chile": "cl", "Colombia": "co", "Peru": "pe", "Kenya": "ke", "Nigeria": "ng", "South Africa": "za",
             "Egypt": "eg", "Qatar": "qa", "UAE": "ae", "United Arab Emirates": "ae", "Israel": "il", "Saudi Arabia": "sa",
             "Afghanistan": "af", "Åland Islands": "ax", "Aland Islands": "ax", "Albania": "al", "Algeria": "dz",
             "American Samoa": "as", "Andorra": "ad", "Angola": "ao", "Anguilla": "ai", "Antarctica": "aq",
             "Antigua and Barbuda": "ag", "Antigua": "ag", "Armenia": "am", "Aruba": "aw", "Azerbaijan": "az", "Bahamas": "bs",
             "The Bahamas": "bs", "Bahrain": "bh", "Bangladesh": "bd", "Barbados": "bb", "Belarus": "by", "Belize": "bz",
             "Benin": "bj", "Bermuda": "bm", "Bhutan": "bt", "Bolivia": "bo", "Bonaire": "bq", "Bosnia and Herzegovina": "ba",
             "Bosnia": "ba", "Botswana": "bw", "Bouvet Island": "bv", "British Indian Ocean Territory": "io", "Brunei": "bn",
             "Bulgaria": "bg", "Burkina Faso": "bf", "Burundi": "bi", "Cabo Verde": "cv", "Cape Verde": "cv", "Cambodia": "kh",
             "Cameroon": "cm", "Cayman Islands": "ky", "Central African Republic": "cf", "Chad": "td",
             "People's Republic of China": "cn", "PRC": "cn", "Christmas Island": "cx", "Cocos Islands": "cc",
             "Cocos (Keeling) Islands": "cc", "Comoros": "km", "Democratic Republic of the Congo": "cd", "DR Congo": "cd",
             "DRC": "cd", "Congo-Kinshasa": "cd", "Republic of the Congo": "cg", "Congo-Brazzaville": "cg", "Congo": "cg",
             "Cook Islands": "ck", "Costa Rica": "cr", "Côte d'Ivoire": "ci", "Cote d'Ivoire": "ci", "Ivory Coast": "ci",
             "Croatia": "hr", "Cuba": "cu", "Curaçao": "cw", "Curacao": "cw", "Cyprus": "cy", "Czechia": "cz",
             "Czech Republic": "cz", "Djibouti": "dj", "Dominica": "dm", "Dominican Republic": "do", "Ecuador": "ec",
             "El Salvador": "sv", "Equatorial Guinea": "gq", "Eritrea": "er", "Estonia": "ee", "Eswatini": "sz",
             "Swaziland": "sz", "Ethiopia": "et", "Falkland Islands": "fk", "Falklands": "fk", "Faroe Islands": "fo",
             "Faroes": "fo", "Fiji": "fj", "French Guiana": "gf", "French Polynesia": "pf", "Tahiti": "pf",
             "French Southern Territories": "tf", "Gabon": "ga", "Gambia": "gm", "The Gambia": "gm",
             "South Georgia and the South Sandwich Islands": "gs", "Georgia": "ge", "Ghana": "gh", "Gibraltar": "gi",
             "Greenland": "gl", "Grenada": "gd", "Guadeloupe": "gp", "Guam": "gu", "Guatemala": "gt", "Guernsey": "gg",
             "Guinea-Bissau": "gw", "Papua New Guinea": "pg", "Guinea": "gn", "Guyana": "gy", "Haiti": "ht",
             "Heard Island and McDonald Islands": "hm", "Vatican City": "va", "Vatican": "va", "Holy See": "va",
             "Honduras": "hn", "Hong Kong": "hk", "Hungary": "hu", "Iceland": "is", "Iran": "ir", "Iraq": "iq",
             "Republic of Ireland": "ie", "Eire": "ie", "Isle of Man": "im", "Jamaica": "jm", "Jordan": "jo",
             "Kazakhstan": "kz", "Kiribati": "ki", "DPRK": "kp", "Republic of Korea": "kr", "Kosovo": "xk", "Kuwait": "kw",
             "Kyrgyzstan": "kg", "Laos": "la", "Latvia": "lv", "Lebanon": "lb", "Lesotho": "ls", "Liberia": "lr",
             "Libya": "ly", "Liechtenstein": "li", "Lithuania": "lt", "Luxembourg": "lu", "Macau": "mo", "Macao": "mo",
             "Madagascar": "mg", "Malawi": "mw", "Malaysia": "my", "Maldives": "mv", "Mali": "ml", "Malta": "mt",
             "Marshall Islands": "mh", "Martinique": "mq", "Mauritania": "mr", "Mauritius": "mu", "Mayotte": "yt",
             "Micronesia": "fm", "Moldova": "md", "Monaco": "mc", "Mongolia": "mn", "Montenegro": "me", "Montserrat": "ms",
             "Morocco": "ma", "Mozambique": "mz", "Myanmar": "mm", "Burma": "mm", "Namibia": "na", "Nauru": "nr",
             "Nepal": "np", "Holland": "nl", "The Netherlands": "nl", "New Caledonia": "nc", "Nicaragua": "ni", "Niger": "ne",
             "Niue": "nu", "Norfolk Island": "nf", "North Macedonia": "mk", "Macedonia": "mk",
             "Northern Mariana Islands": "mp", "Oman": "om", "Pakistan": "pk", "Palau": "pw", "Palestine": "ps",
             "West Bank": "ps", "Gaza Strip": "ps", "Panama": "pa", "Paraguay": "py", "Pitcairn Islands": "pn",
             "Puerto Rico": "pr", "Réunion": "re", "Romania": "ro", "Russia": "ru", "Russian Federation": "ru", "Rwanda": "rw",
             "Saint Barthélemy": "bl", "St Barts": "bl", "Saint Helena": "sh", "Saint Kitts and Nevis": "kn",
             "St Kitts and Nevis": "kn", "Saint Lucia": "lc", "St Lucia": "lc", "Saint Martin": "mf",
             "Saint Pierre and Miquelon": "pm", "Saint Vincent and the Grenadines": "vc", "St Vincent": "vc", "Samoa": "ws",
             "San Marino": "sm", "São Tomé and Príncipe": "st", "Sao Tome and Principe": "st", "Senegal": "sn", "Serbia": "rs",
             "Seychelles": "sc", "Sierra Leone": "sl", "Sint Maarten": "sx", "Slovakia": "sk", "Slovenia": "si",
             "Solomon Islands": "sb", "Somalia": "so", "South Sudan": "ss", "Sri Lanka": "lk", "Sudan": "sd", "Suriname": "sr",
             "Svalbard": "sj", "Syria": "sy", "Taiwan": "tw", "Tajikistan": "tj", "Tanzania": "tz", "Thailand": "th",
             "Timor-Leste": "tl", "East Timor": "tl", "Togo": "tg", "Tokelau": "tk", "Tonga": "to",
             "Trinidad and Tobago": "tt", "Trinidad": "tt", "Tunisia": "tn", "Türkiye": "tr", "Turkiye": "tr",
             "Turkmenistan": "tm", "Turks and Caicos Islands": "tc", "Turks and Caicos": "tc", "Tuvalu": "tv", "Uganda": "ug",
             "Ukraine": "ua", "U.K.": "gb", "Great Britain": "gb", "United States of America": "us", "U.S.A.": "us",
             "US Virgin Islands": "vi", "US": "us", "Uruguay": "uy", "Uzbekistan": "uz", "Vanuatu": "vu", "Venezuela": "ve",
             "Vietnam": "vn", "Viet Nam": "vn", "British Virgin Islands": "vg", "Wallis and Futuna": "wf",
             "Western Sahara": "eh", "Yemen": "ye", "Zambia": "zm", "Zimbabwe": "zw"}
# A lower-case ask ("drone photos of kharkiv", "flooding in ukraine") still names its place: country names and these places
# match in any case, except abbreviations and names that are also everyday words or part of a US place ("turkey",
# "china", "canada geese", "new mexico", "georgia", "the west bank of the river").
_AMBIGUOUS = {"bermuda", "brazil", "canada", "chad", "chile", "china", "georgia", "guernsey", "guinea", "india", "japan",
              "jordan", "mexico", "panama", "turkey", "west bank"}
_CASELESS_PLACES = ("Kyiv", "Kiev", "Kharkiv", "Lviv", "Odesa", "Odessa", "Moscow", "Bangkok", "Hong Kong", "Prague", "Budapest",
                    "Beirut", "Gaza", "Lagos", "Nairobi", "Cairo", "Tel Aviv", "Jerusalem", "Golden Gate Bridge", "Golden Gate",
                    "Times Square", "Statue of Liberty", "Space Needle", "Eiffel Tower", "Big Ben", "Tampa")
CASELESS = [(n.lower(), c) for n, c in COUNTRIES.items() if len(n) > 3 and n != n.upper() and "." not in n
            and n.lower() not in _AMBIGUOUS] + [(n.lower(), CITIES[n][0]) for n in _CASELESS_PLACES]


# A capture aimed at a specific person (the stalking case). Kept in parity with src/match.js.
# The text is normalised first (NFKC, zero-width and bidi characters removed, look-alike letters folded, lower case).
_INVISIBLE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0]")
_CONFUSABLES = dict(zip("аеорсхуіјѕԁԛѵһɡⅼ" "ονρκιαεγ", "aeopcxyijsdqvhgl" "ovpkiaey"))
VERBS = (r"follow(?:ing)?|track(?:ing)?|tail(?:ing)?|stalk(?:ing)?|spy(?:ing)? on|watch(?:ing)?|photograph(?:ing)?|film(?:ing)?|record(?:ing)?|"
         r"keep(?:ing)? (?:an )?eye on|surveil\w*|monitor(?:ing)?|document(?:ing)?|snap(?:ping)? (?:photos?|pictures?) of|take photos? of|photos? of|"
         r"report when|tell me when|see if|find out (?:where|when|if)|whereabouts|comings and goings|movements")
# A family member's place ("my mom's house", "my dad's farm") is a place, not the person: checking on it is not watching them.
# Partners, exes, neighbours and the rest keep no such exemption ("watch my ex's apartment" is still refused).
PLACE_NOUNS = r"(?:house|home|apartment|flat|condo|place|property|yard|garden|lawn|porch|roof|driveway|garage|barn|farm|land|lot|cabin|cottage|building|street|shop|store|restaurant|bakery|cafe|office|business|storefront|fence|grave|church)s?"
_FAMILY = r"(?:stepdad|stepmom|daughter|son|mother|father|mom|dad|sister|brother)(?!(?:'s|s'|\u2019s)\s+" + PLACE_NOUNS + r"\b)"
_REL_OWNED = r"\b(?:my|our) (?:ex|ex[- ]?(?:wife|husband|partner|girlfriend|boyfriend|spouse|fianc\w*)|wife|husband|partner|girlfriend|boyfriend|spouse|fianc\w*|neighbou?rs?|boss|landlord|tenant|roommate|flatmate|coworker|co-worker|colleague|" + _FAMILY + r")(?:'s|s')?\b"
_PERSON = _REL_OWNED + r"|\bthis (?:person|man|woman|guy|girl)\b|\bsomeone(?:'s)?\b|\bwhoever (?:leaves|exits|enters|arrives|lives)\b|\bthe (?:person|man|woman|guy|girl|lady|boy|couple) (?:leaving|exiting|entering|arriving|who|at|living|in|on|near|outside|inside|next door|across)\b"
# a question about where a named relation is or what they are doing ("where is my ex tonight", "is my husband at the bar")
_ASK_ABOUT = (r"\bwhere(?:'s| is| are| was| were)\s+(?:" + _REL_OWNED + r")|\b(?:what|who)(?:'s| is| are| was| were)\s+(?:" + _REL_OWNED
              + r")[^\n]{0,40}?\b(?:doing|up to|with|seeing|meeting)\b|\b(?:is|are|was|were)\s+(?:" + _REL_OWNED
              + r")\s+(?:really |still |actually |currently |secretly )?(?:at|in|with|doing|home|out|inside|there|seeing|meeting|cheating|going)\b")
# a pronoun counts only when something says where or when the person is watched, or the verb is plainly stalking
_PRONOUN = r"\b(?:follow|stalk|tail|spy on)\w*\b[^.]{0,30}\b(?:him|her|them)\b|\b(?:track|watch|photograph|film|record)\w*\b[^.]{0,30}\b(?:him|her|them)\s+(?:and\b|then\b|every\b|each\b|daily\b|everywhere\b|when\b|as (?:he|she|they)\b|leav|arriv|go(?:es)?\b|home\b|house\b|at home\b|at night\b)"
TARGETS_PERSON = (r"\b(?:" + VERBS + r")\b[^\n]{0,120}?(?:" + _PERSON + r")|(?:" + _REL_OWNED + r")[^\n]{0,120}?\b(?:" + VERBS + r")\b|"
                  + _ASK_ABOUT + r"|" + _PRONOUN + r"|\b(?:photograph|film|record|watch|follow|track)\w*\b[^\n]{0,80}[.;]\s*(?:him|her|them)\s*[.!]|\b(?:whereabouts|comings and goings|movements) of\b|"
                  r"\bsubject:\s*(?:" + _REL_OWNED + r"|a person)|\b(?:" + _REL_OWNED + r")[^\n]{0,60}\b(?:is really|really at|arrives?|leaves?|goes)\b|"
                  r"\bwhoever (?:leaves|exits|enters|arrives)\b[^\n]{0,80}\b(?:my|it's my)\b|\bit's my (?:ex|wife|husband|partner|girlfriend|boyfriend)\b")


def normalise(text):
    """The text as the safety checks see it: NFKC, no invisible characters, look-alike letters folded, lower case."""
    t = _INVISIBLE.sub("", unicodedata.normalize("NFKC", str(text)))
    return "".join(_CONFUSABLES.get(ch, ch) for ch in t).lower()


def detect_targets_person(*texts):
    """True if any of the texts, alone or joined (a need split across need, why and spec), aims a capture at a specific person."""
    parts = [normalise(x) for x in texts if isinstance(x, str) and x]
    return bool(parts) and any(re.search(TARGETS_PERSON, x) for x in parts + [" ".join(parts)])


def text_fields(obj):
    """Every string inside a dict/list, for the targeting check over a Request document or a spec."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [t for v in obj.values() for t in text_fields(v)]
    if isinstance(obj, (list, tuple)):
        return [t for v in obj for t in text_fields(v)]
    return []


@dataclass
class Request:
    need: str
    terms: list = field(default_factory=list)
    specifics: list = field(default_factory=list)  # terms that only describe the shot or the spot; they count half
    modalities: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    require: list = field(default_factory=list)
    place: str = None
    lat: float = None
    lon: float = None
    country_code: str = None
    state: str = None
    mode: str = "commission"   # "live" (reporting now) or "commission" (a provider captures what you specify)
    within_minutes: int = None  # deadline for delivery, if stated
    quantity: int = 1
    max_price_usd: float = None
    default_proof: bool = False
    parts: list = field(default_factory=list)  # distinct asks joined by "and"; a hint only, matching is not split
    targets_person: bool = False  # watches, follows or photographs a specific person: never sent to people-based providers by default
    subdivision: str = None  # ISO 3166-2 code of the place when known ("GB-ENG", "US-GA", "KR-11"): matches cards listing one
    anywhere: bool = False   # no place limit ("anywhere", "worldwide"): never geocoded, never asked "Where?"

    def to_dict(self):
        # 0 is a value (lat 0, max_price_usd 0); only None, False and empty lists are left out
        return {k: v for k, v in asdict(self).items() if not (v is None or v is False or v == [])}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _where(text, r):
    """Offline place hints: a US state ("Austin, TX", "in Iowa") or a country ("Lisbon, Portugal")."""
    # A two-letter code is a US state only in capitals after a comma ("Austin, TX"): "in" is a word, never India or Indiana,
    # and a non-US gazetteer city before it ("Delhi, IN") keeps its own country.
    m = re.search(r"(?:([A-Z][\w.'-]*)\s*)?,\s*([A-Z]{2})\b", text)
    if m and m.group(2).lower() in STATES and not (m.group(1) in CITIES and CITIES[m.group(1)][0] != "us"):
        r.state, r.country_code = STATES[m.group(2).lower()], "us"
        r.subdivision = subdivision_code("us", r.state)
        return
    for name in STATES.values():
        if re.search(r"\b" + name.title() + r"\b", text):
            r.state, r.country_code = name, "us"
            r.subdivision = subdivision_code("us", name)
            return
    for name, code in COUNTRIES.items():
        if name in text and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", text):
            r.country_code = code
            sub = subdivision_code(code, name)  # a nation of the UK ("Oxford, England") is a subdivision too
            if sub:
                r.state, r.subdivision = name.lower(), sub
            return
    for name, (code, state) in CITIES.items():
        if name in text and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", text):
            r.country_code, r.state = code, state
            r.subdivision = subdivision_code(code, state)
            return
    low = text.lower()
    for name, code in CASELESS:  # a plain substring test first: hundreds of names, and the ask may be huge
        if name in low and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", low):
            r.country_code = code
            return


def _named_place(text):
    """The first city, landmark or country the offline gazetteer finds in the text, as written there; or None."""
    for name in STATES.values():
        if re.search(r"\b" + name.title() + r"\b", text):
            return name.title()
    for table in (CITIES, COUNTRIES):
        for name in table:
            if name in text and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", text) and not (len(name) <= 3 and name.isupper() and table is COUNTRIES and name not in ("UK", "USA", "UAE")):
                return name
    low = text.lower()
    for name, _ in CASELESS:
        if name in low and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", low):
            m = re.search(re.escape(name), low)
            return text[m.start():m.end()]
    return None


def _language(name):
    """True if a captured "place" is only a language ("Spanish", "Swahili", "Sign Language"): languages are not places."""
    words = re.findall(r"[a-z]+", name.lower())
    return bool(words) and all(w in LANGUAGES for w in words)


def anywhere(text):
    """True if the text says the place doesn't matter ("anywhere", "any country", "worldwide")."""
    return bool(re.search(ANYWHERE, str(text or ""), re.I))


def web_fact(text):
    """True if the ask is for something a web page already publishes (a menu, opening hours, prices, a phone number) and
    nothing is to be captured on site: not an irlpls ask, a web search answers it."""
    t = str(text or "").lower()
    return bool(re.search(WEB_FACT, t)) and not re.search(ON_SITE, t)


MAX_REQUEST_QUANTITY = 10000   # items per request (STANDARD 14.5); split a bigger dataset into several orders
MODES = ("live", "commission")
HINTS = ("place", "lat", "lon", "country_code", "state", "subdivision", "within_minutes", "quantity", "mode", "terms",
         "modalities", "require", "max_price_usd", "anywhere", "targets_person", "specifics", "regions", "parts", "default_proof")


def _number(x):
    import math
    return type(x) in (int, float) and math.isfinite(x)


def check_hints(hints):
    """Hints a requester passes beside the need (SDK keyword arguments, MCP fields): a misspelt name is an error, never
    silently ignored."""
    import difflib
    for k in hints:
        if k not in HINTS:
            near = difflib.get_close_matches(k, HINTS, n=1)
            raise ValueError(f"unknown request field {k!r}" + (f" (did you mean {near[0]}?)" if near else "")
                             + f"; fields: {', '.join(HINTS)}")


def check_request(r):
    """Reject a Request no provider could act on, with a plain message: an empty need, coordinates out of range or half
    given, a quantity below 1 or above MAX_REQUEST_QUANTITY, a deadline that isn't a positive number of minutes, an
    unknown mode, a negative budget hint."""
    if not isinstance(r.need, str) or not r.need.strip():
        raise ValueError("need is empty: say what to observe or capture, with the place and the time "
                         "('river level at Comfort, TX now')")
    for k, lo, hi in (("lat", -90, 90), ("lon", -180, 180)):
        v = getattr(r, k)
        if v is not None and (not _number(v) or not lo <= v <= hi):
            raise ValueError(f"{k} must be a number between {lo} and {hi} (got {v!r})")
    if (r.lat is None) != (r.lon is None):
        raise ValueError("give lat and lon together (got only " + ("lat" if r.lat is not None else "lon") + ")")
    q = r.quantity
    if type(q) is not int or q < 1:
        raise ValueError(f"quantity must be a whole number, 1 or more (got {q!r})")
    if q > MAX_REQUEST_QUANTITY:
        raise ValueError(f"quantity {q} is more than {MAX_REQUEST_QUANTITY:,} items in one request: split it into several orders")
    w = r.within_minutes
    if w is not None and (not _number(w) or isinstance(w, bool) or w <= 0 or w > MAX_MINUTES):
        raise ValueError(f"within_minutes must be a positive number of minutes, at most {MAX_MINUTES} (got {w!r})")
    if r.mode not in MODES:
        raise ValueError(f"mode must be 'live' or 'commission' (got {r.mode!r})")
    if r.max_price_usd is not None and (not _number(r.max_price_usd) or r.max_price_usd < 0):
        raise ValueError(f"max_price_usd must be a number, 0 or more (got {r.max_price_usd!r})")
    return r


def _content(piece):
    """Content words of a piece of the ask: not stop, media, time or number words."""
    return [w for w in re.findall(r"[a-zà-ÿ0-9]+", piece.lower())
            if len(w) > 2 and not w[0].isdigit() and w not in STOP and w not in MEDIA and not GWORDS.match(w)]


def split_parts(text):
    """Distinct asks joined by "and" / ";" / "plus" / "as well as" ("river level near Austin and photos of the dam").
    A piece needs two content words of its own, so "bakery and cafe" or "Austin and Dallas" stay one ask.
    Returns the pieces when there are two or more, else []. A hint only: matching is not split."""
    pieces = []
    for p in re.split(r"\s*(?:;|,?\s*\band\b|,?\s*\bplus\b|,?\s*\bas well as\b)\s*", text):
        p = p.strip(" ,.")
        if not p:
            continue
        if pieces and len(_content(p)) < 2:
            pieces[-1] += " and " + p   # too thin to be an ask of its own: part of the previous one
        elif pieces and len(_content(pieces[-1])) < 2:
            pieces[-1] += " and " + p
        else:
            pieces.append(p)
    return pieces if len(pieces) > 1 else []


def parse(text, cards=()):
    t = text.lower()
    r = Request(need=text)
    r.modalities = [m for m, rx in MODALITIES.items() if re.search(rx, t)]
    if "sensor" in r.modalities and set(r.modalities) & {"photo", "video", "audio"} and not re.search(EXPLICIT_SENSOR, t):
        r.modalities.remove("sensor")  # "photo of the river" asks for a photo, not a gauge
    if "aerial" in r.modalities and "photo" in r.modalities:
        r.modalities.remove("photo")   # a satellite or drone image is the aerial modality
    radar, thermal = re.search(RADAR, t), re.search(THERMAL, t)
    if (radar or thermal) and "photo" in r.modalities:
        r.modalities.remove("photo")   # a radar or thermal "image" is not a photo
        if radar and "aerial" not in r.modalities:
            r.modalities.append("aerial")  # radar imagery is taken from orbit or the air
    r.require = [k for k, rx in GUARANTEES.items() if re.search(rx, t)]
    # The core question is "is this the right thing, at the right place and time?", so always ask for that.
    added = [k for k in DEFAULT_PROOF if k not in r.require]
    r.require = r.require + added
    r.default_proof = bool(added)
    r.targets_person = detect_targets_person(text)
    w = re.search(r"\b(?:in the next|within|in under|in)\s+(\d{1,6})\s*(minute|min|hour|hr|day|week)s?\b", t)
    w = w or re.search(BARE_DEADLINE, t)
    if w:
        r.within_minutes = min(int(w.group(1)) * UNIT_MIN[w.group(2)], MAX_MINUTES)
    elif re.search(r"\btoday\b", t):
        r.within_minutes = 720
    elif re.search(r"\b(by )?tomorrow\b", t):
        r.within_minutes = 1440
    elif re.search(r"\bthis week\b", t):
        r.within_minutes = 10080
    elif re.search(TONIGHT, t):
        r.within_minutes = TONIGHT_MINUTES
    q = re.search(r"(?<![\w.])(\d[\d,]{0,15})\s+(?:[\w-]+\s+){0,2}(examples?|photos?|images?|videos?|clips?|samples?|observations?|recordings?|responses?|people|participants?|scenes?|readings?)\b", t)
    if q:
        r.quantity = min(int(q.group(1).replace(",", "") or 1), MAX_QUANTITY)
    dataset = r.quantity > 1 or re.search(r"\b(dataset|training data|examples|corpus)\b", t)
    if re.search(LIVE, t) or re.search(LOOKBACK, t) or (r.within_minutes is not None and r.within_minutes <= 60) \
            or (re.search(TODAY, t) and not re.search(CAPTURE, t)):
        r.mode = "live"
        r.within_minutes = r.within_minutes or 60
    elif r.within_minutes is None and not dataset and not re.search(CAPTURE, t) \
            and ("sensor" in r.modalities or re.search(CONDITION, t)):
        r.mode, r.within_minutes = "live", 60   # a reading of conditions: what is reporting now
    if dataset:
        r.mode = "commission"
    r.parts = split_parts(text)
    # "under $2,500" is 2500, not 2; "under $2.5k" is 2500
    m = re.search(r"(?:under|below|max|less than|<)\s*\$?(\d{1,3}(?:,\d{3})+|\d{1,9})(?!\d)(\.\d{1,6})?\s*(k\b)?", t)
    if m:
        r.max_price_usd = float(m.group(1).replace(",", "") + (m.group(2) or "")) * (1000 if m.group(3) else 1)
    r.anywhere = bool(re.search(ANYWHERE, t))
    _where(text, r)
    addresses, rest = [], re.sub(ANYWHERE, " ", text, flags=re.I)
    for rx in (ADDRESS, ADDRESS_LC):
        addresses += [a.group(0).strip(" ,.") for a in rx.finditer(rest)]
        rest = rx.sub(" , ", rest)
    places = addresses + [p for p in (m.group(1).strip(" ,.") for m in PLACE.finditer(rest)) if not _language(p)]
    if places:
        r.place = ", ".join(places)
    elif r.country_code and not r.anywhere:
        named = _named_place(text)   # "air, Delhi", "kharkiv drone photos": the gazetteer's name is the place
        if named:
            r.place = named
    place_words = set(re.split(r"[^a-z]+", (r.place or "").lower()))
    regions = set()
    for c in cards:
        for g in c["coverage"]["regions"]:
            regions |= {w for w in re.split(r"[^a-zà-ÿ]+", g.lower()) if len(w) > 2}
    # Capitalised words that aren't sentence-initial are names (rivers, streets, venues), not subject matter.
    names = {m.group(0).lower() for m in re.finditer(r"(?<!^)(?<![.?!]\s)\b[A-Z][a-zà-ÿ'-]+", rest)} - GENERIC
    s = rest.lower()
    for rx, sub in PHRASES:
        s = re.sub(rx, sub, s)
    spec, prev = False, ""
    for w in re.findall(r"[a-zà-ÿ0-9]+(?:\.\d+)?|[,;:?!.]", s):
        if not w[0].isalnum() or w in BOUNDARY:
            # "photos of X" and "at X": X describes the shot or the spot
            spec = (w == "of" and prev in MEDIA) or w in SPOT
        elif w in SPOT:
            spec = True
        prev = w
        if not w[0].isalnum() or w in STOP or w in MEDIA or GWORDS.match(w) or w in names:
            continue
        if w in place_words and w not in GENERIC:  # "Golden Gate Bridge" is a place; "bridge" still says what kind
            if w in regions:
                r.regions.append(w)
            continue
        if len(w) > 2 and not w[0].isdigit() and w not in r.terms:
            r.terms.append(w)
            if spec:
                r.specifics.append(w)
    if "aerial" in r.modalities and re.search(MODALITIES["photo"], t) and not (radar or thermal) and "optical" not in r.terms:
        r.terms.append("optical")      # a plain satellite or drone image: optical, softly
        r.specifics.append("optical")
    words = set(re.findall(r"[a-z]+", t))
    if r.mode == "commission" and (re.search(ROAD_SCENE, t) or (re.search(ROAD, t) and words & CONDITIONS)):
        r.specifics += [w for w in r.terms if w in CONDITIONS and w not in r.specifics]  # the scene's conditions
        if "driving" not in r.terms:
            r.terms.append("driving")  # a road scene to capture: driving footage, softly
            r.specifics.append("driving")
    if cards:  # words no card mentions are details of the shot, not a capability anyone lists
        from .match import mentions, vocab
        vs = [vocab(c) for c in cards]
        r.specifics += [w for w in r.terms if w not in r.specifics and not any(mentions(None, w, v) for v in vs)]
    return r
