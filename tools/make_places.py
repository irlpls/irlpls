"""Build src/places.json: the browser's world gazetteer (src/places.js reads it).

    python3 tools/make_places.py [SRC_DIR]

The landing's screen 3 and Find data put a typed place on the map without sending the text anywhere (the privacy promise):
they look it up in this table, in the browser. Source: GeoNames (https://www.geonames.org/, CC BY 4.0) cities15000,
countryInfo and admin1CodesASCII. Kept: cities with population >= 50,000 plus every national capital (PPLC).

SRC_DIR: a folder that already holds cities15000.zip (or cities15000.txt), countryInfo.txt and admin1CodesASCII.txt;
without it, the three files are downloaded from download.geonames.org into a temporary folder.

Format (compact, gzip-friendly):
  {"v":1, "source": "...", "date": "YYYY-MM-DD",
   "countries": {"germany": "de", ...},          lower-case country names and common aliases -> ISO 3166-1 alpha-2
   "admin1": {"us.TX": "Texas", ...},              only the codes the cities use
   "cities": [[name, other names "a|b" or 0, cc, admin1 ("" outside ADMIN_CC), lat*100, lon*100, population in thousands], ...]}
                                                   (biggest first; coordinates in hundredths of a degree, as integers)
"""
import datetime
import io
import json
import pathlib
import sys
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "places.json"
BASE = "https://download.geonames.org/export/dump/"
FILES = ("cities15000.zip", "countryInfo.txt", "admin1CodesASCII.txt")
MIN_POP = 50000
# countries whose first-level divisions people write after a city ("Springfield, Illinois", "Cambridge, MA"); elsewhere the
# division is left out to keep the file small
ADMIN_CC = {"us", "ca", "au", "gb", "in", "br", "mx", "de", "fr", "es", "it", "cn", "jp", "ru", "ng", "za"}

# common English names and abbreviations for countries, beyond countryInfo's own names
ALIASES = {
    "usa": "us", "us": "us", "u.s.": "us", "u.s.a.": "us", "united states of america": "us", "america": "us",
    "uk": "gb", "u.k.": "gb", "britain": "gb", "great britain": "gb", "england": "gb", "scotland": "gb", "wales": "gb",
    "northern ireland": "gb", "holland": "nl", "the netherlands": "nl", "czech republic": "cz", "czechia": "cz",
    "south korea": "kr", "korea": "kr", "north korea": "kp", "russia": "ru", "turkey": "tr", "türkiye": "tr",
    "ivory coast": "ci", "uae": "ae", "emirates": "ae", "vietnam": "vn", "deutschland": "de", "österreich": "at",
    "schweiz": "ch", "suisse": "ch", "españa": "es", "italia": "it", "brasil": "br", "méxico": "mx", "nippon": "jp",
    "burma": "mm", "congo": "cd", "drc": "cd", "palestine": "ps", "taiwan": "tw", "macedonia": "mk", "eswatini": "sz",
    "swaziland": "sz", "cape verde": "cv", "east timor": "tl", "vatican": "va",
}


# the English name people type for a city GeoNames lists under its own name: shown as the name; the GeoNames name stays
# searchable
ENGLISH = {"Köln": "Cologne", "Hannover": "Hanover", "Sevilla": "Seville", "Luzern": "Lucerne", "Brugge": "Bruges",
           "Frankfurt am Main": "Frankfurt", "Makkah": "Mecca", "Fes": "Fez", "Nürnberg": "Nuremberg", "Braunschweig": "Brunswick",
           "Gent": "Ghent", "Den Haag": "The Hague"}
# other names in common English use, searchable only
OTHER = {"Kyiv": ["Kiev"], "Mumbai": ["Bombay"], "Kolkata": ["Calcutta"], "Chennai": ["Madras"], "Beijing": ["Peking"],
         "Bengaluru": ["Bangalore"], "The Hague": ["Den Haag", "Hague"], "Ho Chi Minh City": ["Saigon", "Ho Chi Minh"],
         "Yangon": ["Rangoon"], "New York City": ["New York", "NYC"], "Köln": ["Koln"], "Odesa": ["Odessa"],
         "Kharkiv": ["Kharkov"], "Guangzhou": ["Canton"], "Xi'an": ["Xian"], "Washington": ["Washington DC", "Washington D.C."]}


def fetch(src):
    if src:
        d = pathlib.Path(src)
    else:
        d = pathlib.Path(tempfile.mkdtemp(prefix="geonames-"))
        for f in FILES:
            with urllib.request.urlopen(BASE + f, timeout=120) as r:
                (d / f).write_bytes(r.read())
    txt = d / "cities15000.txt"
    if txt.exists():
        cities = txt.read_text(encoding="utf-8")
    else:
        with zipfile.ZipFile(d / "cities15000.zip") as z:
            cities = io.TextIOWrapper(z.open("cities15000.txt"), encoding="utf-8").read()
    return cities, (d / "countryInfo.txt").read_text(encoding="utf-8"), (d / "admin1CodesASCII.txt").read_text(encoding="utf-8")


def build(cities_txt, country_txt, admin_txt):
    countries = {}
    for line in country_txt.splitlines():
        if not line or line.startswith("#"):
            continue
        c = line.split("\t")
        if len(c) > 4 and c[0] and c[4]:
            countries[c[4].lower()] = c[0].lower()
    for k, v in ALIASES.items():
        countries.setdefault(k, v)
    admin_names = {}
    for line in admin_txt.splitlines():
        c = line.split("\t")
        if len(c) >= 3:
            admin_names[c[0]] = c[1]  # "US.TX" -> "Texas"
    rows, used = [], set()
    for line in cities_txt.splitlines():
        c = line.split("\t")
        if len(c) < 15:
            continue
        name, ascii_, lat, lon, code, cc, adm, pop = c[1], c[2], float(c[4]), float(c[5]), c[7], c[8], c[10], int(c[14] or 0)
        if pop < MIN_POP and code != "PPLC" or code == "PPLX":  # PPLX: a section of a city, not a city
            continue
        adm = adm if cc.lower() in ADMIN_CC else ""
        alts = [n for n in [ascii_, name] + OTHER.get(name, []) if n]
        shown = ENGLISH.get(name, name)
        alts = [n for i, n in enumerate(alts) if n != shown and n not in alts[:i]]
        rows.append([shown, "|".join(alts) or 0, cc.lower(), adm, round(lat * 100), round(lon * 100), round(pop / 1000)])
        if adm:
            used.add(cc + "." + adm)
    rows.sort(key=lambda r: -r[6])  # the biggest first: ties break toward the city people mean
    admin1 = {k[:2].lower() + k[2:]: admin_names[k] for k in sorted(used) if k in admin_names}
    return {"v": 1, "source": f"GeoNames cities15000 (CC BY 4.0, https://www.geonames.org/), population >= {MIN_POP:,} plus national capitals",
            "date": datetime.date.today().isoformat(), "countries": dict(sorted(countries.items())), "admin1": admin1, "cities": rows}


def main():
    data = build(*fetch(sys.argv[1] if len(sys.argv) > 1 else None))
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(data['cities'])} cities, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
