"""Matcher benchmark: realistic requests, and what the Capture Cards say should win.

Every expectation below comes only from what the cards in cards/ state (capabilities, tags, coverage, delivery).
`top` is the set of acceptable top-1 providers; `not3` must not appear in the top 3 (out of coverage, wrong modality,
or a provider whose card says it can't do what was asked). Parsing runs offline (no geocoding).

The parity test runs the same queries through src/match.js under node and requires the same top-1 and fit within 2.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

from irlpls.match import in_scope, rank
from irlpls.need import parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = [c for c in (json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json")))
         if in_scope(c) and c.get("status") != "sample"]

BENCH = [
    # ---- live: sensors ----
    ("is the Guadalupe River at Comfort, TX flooding right now", {"usgs-water"}, {"caltrans-cctv", "skyfi", "planet", "proxypics"}),
    ("river level on Shoal Creek in Austin, TX right now", {"usgs-water"}, {"caltrans-cctv", "openaq", "skyfi"}),
    ("is the Potomac high enough for kayaking at Washington, DC right now", {"usgs-water"}, {"caltrans-cctv", "prolific", "umbra"}),
    ("air quality in Fresno, CA right now", {"airnow", "sensor-community"}, {"usgs-water", "proxypics", "skyfi"}),
    ("PM2.5 readings near Delhi, India right now", {"openaq", "sensor-community"}, {"nws", "usgs-water", "noaa-tides"}),
    ("how smoky is the air in Portland, OR right now", {"airnow", "sensor-community"}, {"caltrans-cctv", "proxypics", "umbra"}),
    ("current temperature and wind in Denver, CO", {"nws"}, {"caltrans-cctv", "skyfi", "proxypics"}),
    ("is it raining in Chicago, IL right now", {"nws"}, {"caltrans-cctv", "skyfi", "prolific"}),
    ("tide level at Galveston, TX right now", {"noaa-tides"}, {"caltrans-cctv", "skyfi", "openaq"}),
    ("storm surge at the coast in Charleston, SC right now", {"noaa-tides"}, {"caltrans-cctv", "openaq", "prolific"}),
    ("water temperature at the beach in San Diego, CA right now", {"noaa-tides"}, {"openaq", "purpleair", "skyfi"}),
    ("wind gusts at the harbor in Boston, MA right now", {"noaa-tides"}, {"caltrans-cctv", "skyfi", "proxypics"}),
    ("how big are the waves offshore of San Francisco, CA right now", {"ndbc-buoys"}, {"caltrans-cctv", "skyfi", "proxypics"}),
    ("sea surface temperature at the buoy off Cape Cod, MA right now", {"ndbc-buoys"}, {"caltrans-cctv", "openaq", "umbra"}),
    ("official AQI in Fresno, CA right now", {"airnow"}, {"usgs-water", "proxypics", "skyfi"}),
    ("air quality forecast for tomorrow in Denver, CO", {"airnow"}, {"caltrans-cctv", "proxypics", "umbra"}),
    ("are there any wildfires burning near Paradise, CA right now", {"nasa-firms"}, {"proxypics", "caltrans-cctv", "skyfi"}),
    ("active fire hotspots in Alberta, Canada today", {"nasa-firms"}, {"proxypics", "wegolook", "nws"}),
    ("latest earthquakes in Japan", {"usgs-earthquakes"}, {"rentahuman", "skyfi", "windy-webcams"}),
    ("magnitude of the earthquake in Alaska this morning", {"usgs-earthquakes"}, {"nws", "noaa-tides", "usgs-water"}),
    ("tide at Santa Monica, California", {"noaa-tides"}, {"nws", "hivemapper", "satvu"}),  # "tide" is tides, not any water or level
    ("earthquakes near Anchorage, Alaska today", {"usgs-earthquakes", "emsc-seismicportal"}, {"nws", "noaa-tides", "usgs-water"}),
    ("river level in Vienna, Austria", {"pegelonline"}, {"usgs-water", "hubeau-hydrometrie", "uk-ea-flood"}),  # viadonau gauges on PEGELONLINE
    ("river level in Paris, France", {"hubeau-hydrometrie"}, {"usgs-water", "pegelonline", "uk-ea-flood"}),
    ("weather in London, UK right now", {"windy-webcams", "openaq", "sensor-community", "ndbc-buoys"}, {"nws", "noaa-tides", "usgs-water"}),
    # ---- live: cameras ----
    ("is traffic backed up on the Bay Bridge in San Francisco, CA right now", {"caltrans-cctv", "empiric-earth"}, {"usgs-water", "noaa-tides", "nws"}),
    ("snow on the road at Lake Tahoe, CA right now", {"caltrans-cctv", "empiric-earth"}, {"usgs-water", "openaq", "skyfi"}),
    ("live traffic camera on the highway in Austin, TX", {"windy-webcams"}, {"caltrans-cctv", "usgs-water", "nws"}),
    ("live webcam of the beach in Lisbon, Portugal", {"windy-webcams"}, {"caltrans-cctv", "nws", "noaa-tides"}),
    ("river camera photo of the Potomac at Washington, DC right now", {"usgs-water"}, {"caltrans-cctv", "openaq", "skyfi"}),
    ("mountain pass road conditions camera at Stevens Pass, WA right now", {"wsdot-cameras"}, {"caltrans-cctv", "usgs-water", "skyfi"}),
    ("highway camera of a mountain pass in Washington state", {"wsdot-cameras"}, {"caltrans-cctv", "tfl-jamcams", "nws"}),
    ("traffic camera at a junction in London, UK right now", {"tfl-jamcams"}, {"caltrans-cctv", "wsdot-cameras", "usgs-water"}),
    # ---- commission: people with phones ----
    ("photo of the storefront at 123 Main St, Springfield, IL today", {"proxypics", "getterdone"}, {"caltrans-cctv", "usgs-water", "windy-webcams", "nws"}),
    ("is the bakery on Elm St in Springfield, IL open? send a photo of the door today", {"proxypics", "getterdone"}, {"caltrans-cctv", "usgs-water", "nws"}),
    ("exterior photos of a house for a mortgage appraisal in Columbus, OH", {"proxypics"}, {"skyfi", "umbra", "caltrans-cctv"}),
    ("night photos of a property in Tampa, FL tomorrow", {"proxypics"}, {"caltrans-cctv", "noaa-tides", "umbra"}),
    ("inspection photos of a damaged car for an insurance claim in Atlanta, GA tomorrow", {"wegolook", "proxypics"}, {"umbra", "nws", "caltrans-cctv"}),
    ("vacant land inspection in Memphis, TN this week", {"wegolook"}, {"umbra", "nws", "noaa-tides"}),
    ("photos of the cereal shelf display at a grocery store in Dallas, TX", {"field-agent"}, {"caltrans-cctv", "skyfi", "usgs-water"}),
    ("check shelf prices at grocery stores in Toronto, Canada", {"field-agent"}, {"proxypics", "wegolook", "caltrans-cctv"}),
    ("photo of a storefront in Nairobi, Kenya today", {"rentahuman"}, {"proxypics", "wegolook", "field-agent"}),
    ("someone to stand in line at a bakery in Toronto, Canada", {"rentahuman"}, {"proxypics", "wegolook", "caltrans-cctv"}),
    ("someone to attend an event in London, UK with timed selfie check-ins", {"rentahuman"}, {"proxypics", "wegolook", "nws"}),
    # ---- commission: datasets from people ----
    ("500 first-person video clips of a hand opening a push-bar door within 7 days", {"prolific", "kled"},
     {"usgs-water", "caltrans-cctv", "skyfi", "umbra", "windy-webcams"}),
    ("200 participants to record a short video of their kitchen within 3 days", {"prolific"}, {"usgs-water", "caltrans-cctv", "umbra"}),
    ("audio recordings of 300 people saying a wake word", {"prolific", "kled", "shaip"}, {"noaa-tides", "nws", "caltrans-cctv"}),
    ("first-person video of household tasks for robotics training", {"toloka", "unidata", "claru"}, {"caltrans-cctv", "hivemapper", "proxypics"}),
    ("video collection with AR/VR glasses and wearables in rented homes", {"appen"}, {"caltrans-cctv", "proxypics", "hivemapper"}),
    ("2000 images of gestures and facial expressions for a computer vision dataset", {"defined-ai"},
     {"caltrans-cctv", "usgs-water", "hivemapper"}),
    ("consented landmark images with documented participant releases", {"shaip"}, {"caltrans-cctv", "usgs-water", "skyfi"}),
    ("1000 photos of pets and animals taken by contributors", {"lxt-clickworker"}, {"caltrans-cctv", "usgs-water", "windy-webcams"}),
    # Known gap (card audit 2026-10-26): no listed provider verifiably does store visits in Mexico; best fit is ~34.
    # ---- commission: streets, drones, satellites ----
    ("fresh dashcam video of Congress Avenue in Austin, TX within 2 days", {"hivemapper"}, {"caltrans-cctv", "nws", "umbra"}),
    ("street-level video of a road in Tokyo, Japan this week", {"hivemapper", "vangrid"}, {"proxypics", "caltrans-cctv", "wegolook"}),
    ("dashcam video of near-miss edge cases for autonomous vehicle training", {"empiric-earth"},
     {"proxypics", "caltrans-cctv", "prolific"}),
    ("drone photos of a roof in Phoenix, AZ this week", {"spexi", "zeitview"}, {"umbra", "nws", "caltrans-cctv"}),
    ("drone orthomosaic of a construction site in Toronto, Canada", {"spexi"}, {"proxypics", "wegolook", "caltrans-cctv"}),
    ("drone inspection of a wind turbine in Germany", {"zeitview"}, {"spexi", "proxypics", "nws"}),
    ("thermal inspection of a solar farm in Arizona", {"zeitview"}, {"proxypics", "nws", "caltrans-cctv"}),
    # a plain satellite "image" is optical; radar only when asked (radar, SAR, through clouds)
    ("satellite image of a farm field in Iowa within 2 weeks", {"planet", "satellogic", "skyfi"}, {"capella", "iceye", "umbra", "satvu"}),
    ("radar satellite image through clouds at night of the port of Rotterdam, Netherlands", {"umbra", "capella", "iceye"},
     {"skyfi", "satvu", "planet", "satellogic"}),
    ("satellite video of a stadium in Doha, Qatar", {"skyfi", "planet"}, {"proxypics", "spexi", "caltrans-cctv"}),
    ("radar satellite image of a port in Singapore with repeat tasking every day", {"capella"}, {"proxypics", "spexi", "nws"}),
    ("SAR satellite image with GRD and SLC products of a port in Greece", {"iceye"}, {"proxypics", "spexi", "caltrans-cctv"}),
    ("radar satellite image of a ship in the Baltic Sea with products available 1 hour after acquisition", {"iceye"},
     {"spexi", "proxypics", "ndbc-buoys"}),
    ("rush tasking of a high resolution optical satellite image of a mine in Chile", {"satellogic"}, {"proxypics", "spexi", "nws"}),
    ("satellite heat signature of a refinery in Texas at night", {"satvu"}, {"proxypics", "zeitview", "nasa-firms"}),
    ("thermal infrared satellite image of a power plant in Germany", {"satvu"}, {"proxypics", "zeitview", "caltrans-cctv"}),
    # ---- modality: radar / optical / thermal ----
    ("radar image of a port through clouds", {"capella", "iceye", "umbra"}, {"planet", "satellogic", "proxypics", "windy-webcams"}),
    ("satellite photo of a vineyard in France through clouds", {"capella", "iceye", "umbra"}, {"planet", "satellogic", "proxypics"}),
    ("optical satellite photo of a farm", {"planet", "satellogic", "skyfi", "open-cosmos-datacosmos"}, {"capella", "iceye", "umbra", "satvu"}),
    ("thermal image of a factory at night", {"satvu"}, {"proxypics", "prolific", "wegolook", "rentahuman"}),
    ("thermal infrared image of a factory in Tokyo at night", {"satvu"}, {"proxypics", "capella", "zeitview"}),
    # ---- time words set timing, never description; non-US places ----
    ("earthquake near Los Angeles in the last hour", {"usgs-earthquakes"}, {"noaa-tides", "ndbc-buoys", "nasa-firms", "caltrans-cctv"}),
    ("earthquakes near Tokyo in the past 24 hours", {"usgs-earthquakes"}, {"noaa-tides", "nws", "hivemapper"}),
    ("what was the magnitude of the quake 10 minutes ago near Seattle", {"usgs-earthquakes"}, {"wsdot-cameras", "noaa-tides", "nws"}),
    ("wildfire hotspots in California in the past 2 days", {"nasa-firms"}, {"caltrans-cctv", "proxypics", "skyfi"}),
    ("air quality sensor readings in London this morning", {"openaq", "purpleair", "sensor-community"}, {"airnow", "tfl-jamcams", "nws"}),
    ("live river gauge readings in Seattle in the last hour", {"usgs-water"}, {"wsdot-cameras", "noaa-tides", "ndbc-buoys"}),
    ("wave height and wind at an offshore buoy near Seattle right now", {"ndbc-buoys"}, {"wsdot-cameras", "caltrans-cctv", "skyfi"}),
    # ---- video and cameras in named cities ----
    ("traffic camera video of Tower Bridge in London right now", {"tfl-jamcams"}, {"caltrans-cctv", "wsdot-cameras", "proxypics"}),
    ("highway camera photo of I-5 in Seattle right now", {"wsdot-cameras"}, {"caltrans-cctv", "tfl-jamcams", "proxypics"}),
    ("street-level video of a road in Tokyo this week", {"hivemapper", "vangrid"}, {"proxypics", "caltrans-cctv", "tfl-jamcams"}),
    # ---- bare driving scenes (a model's hard cases): dashcam networks, never tides, gauges or property photos ----
    ("cyclist at dusk", {"hivemapper", "empiric-earth"}, {"noaa-tides", "usgs-water", "proxypics", "prolific"}),
    ("right turn on a snowy afternoon", {"hivemapper", "empiric-earth"}, {"nws", "noaa-tides", "proxypics"}),
    ("flooded street", {"hivemapper", "empiric-earth"}, {"noaa-tides", "usgs-water", "nws"}),
    ("roadwork at night", {"hivemapper", "empiric-earth"}, {"proxypics", "capella", "iceye", "skyfi"}),
    # ---- the same scenes as a training loop phrases them ----
    ("photo: right turn, snowy afternoon", {"hivemapper", "empiric-earth"}, {"nws", "noaa-tides", "usgs-water"}),
    ("photo: cyclist at dusk", {"hivemapper", "empiric-earth"}, {"noaa-tides", "usgs-water", "prolific"}),
    ("photo: roadwork at night", {"hivemapper", "empiric-earth"}, {"capella", "iceye", "skyfi"}),
    ("fresh dashcam video of flooded street", {"hivemapper", "empiric-earth", "natix"}, {"noaa-tides", "usgs-water", "proxypics"}),
    # ---- a bare deadline ("7 days") ----
    ("500 first-person clips of a hand opening a door, 7 days", {"prolific", "kled"}, {"usgs-water", "caltrans-cctv", "skyfi", "umbra"}),
]

DEADLINES = [("500 clips, 7 days", 10080), ("500 clips within 7 days", 10080), ("200 photos, 3-day turnaround", 4320),
             ("earthquakes near Tokyo in the past 24 hours", None), ("radar image available 1 hour after acquisition", None),
             ("2 hours of driving footage", None), ("repeat tasking every 7 days", None)]


@pytest.mark.parametrize("q,minutes", DEADLINES, ids=[d[0] for d in DEADLINES])
def test_bare_deadline(q, minutes):
    r = parse(q, CARDS)
    if minutes is None:
        assert r.within_minutes in (None, 60), f"{q!r}: {r.within_minutes} is not a deadline"
    else:
        assert r.within_minutes == minutes


def py_rank(q):
    return rank(CARDS, parse(q, CARDS))


@pytest.mark.parametrize("q,top,not3", BENCH, ids=[b[0][:60] for b in BENCH])
def test_bench(q, top, not3):
    res = py_rank(q)
    ids = [r["id"] for r in res]
    assert ids and ids[0] in top, f"{q!r}: top {[(r['id'], r['fit']) for r in res[:4]]}"
    assert not (set(ids[:3]) & not3), f"{q!r}: top 3 {ids[:3]} includes {set(ids[:3]) & not3}"
    assert res[0]["fit"] >= 40, f"{q!r}: best fit only {res[0]['fit']}"
    assert res[0]["parts"].get("matched"), "the fit must be explainable"


NODE = r"""
const fs=require('fs'),path=require('path');global.window={};
eval(fs.readFileSync(path.join(process.argv[1],'src/match.js'),'utf8'));
const M=window.IRLMatch,dir=path.join(process.argv[1],'cards');
const C=fs.readdirSync(dir).filter(f=>f.endsWith('.json')).sort().map(f=>JSON.parse(fs.readFileSync(path.join(dir,f),'utf8'))).filter(c=>M.inScope(c)&&c.status!=='sample');
const Q=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(Q.map(q=>{const r=M.parse(q,C);
  const l=C.map(c=>({c:c,s:M.score(c,r)})).filter(x=>x.s.total==null||x.s.total>0).sort(M.compare);
  return l.slice(0,3).map(x=>[x.c.id,x.s.total])})));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_parity():
    qs = [b[0] for b in BENCH]
    out = subprocess.run(["node", "-e", NODE, str(ROOT)], input=json.dumps(qs), capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    bad = []
    for q, j in zip(qs, js):
        p = py_rank(q)
        if not j or not p or j[0][0] != p[0]["id"] or abs(j[0][1] - p[0]["fit"]) > 2:
            bad.append((q, [(r["id"], r["fit"]) for r in p[:3]], j))
    assert not bad, "\n".join(map(str, bad))
