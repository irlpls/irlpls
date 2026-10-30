#!/usr/bin/env python3
"""Snapshot the latest daylight frame from the demo USGS river cameras into src/media/live/.

USGS NIMS images are public domain but served without CORS headers, so the landing page's WebGL stage can't read
them directly. Run this before build.py (or on a schedule) to refresh the snapshots; each frame keeps its real
capture time in src/media/live/index.json.
"""
import json
import pathlib
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

NIMS = "https://api.waterdata.usgs.gov/nims/v0"
S3 = "https://usgs-nims-images.s3.amazonaws.com/720/"
OUT = pathlib.Path(__file__).resolve().parent.parent / "src" / "media" / "live"
UA = {"User-Agent": "irlpls/0.1 (+https://irlpls.com)"}
CAMS = {  # camId: label
    "TX_Guadalupe_Rv_at_Comfort": "Guadalupe River, Comfort TX",
    "CA_Merced_River_at_Happy_Isles_Bridge_Yosemite": "Merced River, Yosemite",
    "CO_Clear_Creek_at_Golden": "Clear Creek, Golden CO",
    "OR_McKenzie_River_near_Vida": "McKenzie River, Vida OR",
    "CO_Yampa_River_at_Steamboat_Springs": "Yampa River, Steamboat CO",
    "PA_Brandywine_Creek_at_Chadds_Ford": "Brandywine Creek, Chadds Ford PA",
}
OFFSET = {"US/Eastern": -4, "US/Central": -5, "US/Mountain": -6, "US/Pacific": -7, "America/New_York": -4, "America/Chicago": -5,
          "America/Denver": -6, "America/Los_Angeles": -7, "US/Arizona": -7, "America/Phoenix": -7}  # DST in effect until 2026-11-01


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        index = json.loads((OUT / "index.json").read_text())  # keep earlier snapshots if a camera can't be reached now
    except (OSError, ValueError):
        index = {}
    try:
        cams = {c["camId"]: c for c in json.loads(get(f"{NIMS}/cameras"))}
    except Exception as e:
        print("cameras list unavailable:", e)
        cams = {}
    for cam, label in CAMS.items():
        try:
            pick = daylight_frame(cam, OFFSET.get(cams.get(cam, {}).get("tz"), -5))
            if not pick:
                print("no daylight frame:", cam)
                continue
            img = get(S3 + cam + "/" + pick[0])
            if pick[2] and len(get(S3 + cam + "/" + pick[2])) == len(img):
                print("skipped stuck camera (two frames identical):", cam)
                index.pop(cam, None)
                continue
            (OUT / f"{cam}.jpg").write_bytes(img)
            nwis = cams.get(cam, {}).get("nwisId") or (index.get(cam, {}).get("site") or "").replace("USGS-", "") or None
            index[cam] = {"label": label, "captured": pick[1].isoformat().replace("+00:00", "Z"), "file": f"{cam}.jpg",
                          "site": f"USGS-{nwis}" if nwis else None, "source": f"https://apps.usgs.gov/hivis/camera/{cam}"}
            print(cam, pick[1].isoformat())
        except Exception as e:
            print("kept previous snapshot for", cam, "-", e)
        time.sleep(0.5)
    g = gauge("USGS-08167000")
    if g.get("flow") or "_gauge" not in index:
        index["_gauge"] = {**index.get("_gauge", {}), **g}
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    (OUT / "health.json").write_text(json.dumps(health(), indent=1))


def daylight_frame(cam, tz):
    """Newest daylight frame, plus the previous daylight frame so a stuck camera (same image again) can be skipped."""
    found = []
    for f in json.loads(get(f"{NIMS}/listFiles?camId={cam}&limit=120")):
        stamp = f.split("___")[-1].rsplit(".", 1)[0]
        try:
            t = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if 9 <= (t + timedelta(hours=tz)).hour <= 16:  # daylight only; no dusk or night frames
            found.append((f, t))
            if len(found) == 2:
                break
    if not found:
        return None
    return found[0][0], found[0][1], found[1][0] if len(found) > 1 else None


def gauge(site):
    """Latest flow and height at the landing page's example gauge, plus the long-term daily percentiles for today,
    so the page can answer 'is it normal?' from USGS's own statistics, and can fall back to a saved reading."""
    out = {"site": site}
    for code, key in (("00060", "flow"), ("00065", "height")):
        try:
            d = json.loads(get(f"https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/items?monitoring_location_id={site}&parameter_code={code}&f=json&limit=1"))
            p = d["features"][0]["properties"]
            out[key] = {"v": float(p["value"]), "u": p["unit_of_measure"], "t": p["time"]}
        except Exception as e:
            print("gauge", code, e)
    try:  # USGS Water Data Statistics API: day-of-year percentiles of the daily mean (the legacy nwis/stat shuts 2027-02-22)
        from irlpls.adapters import usgs_water
        today = datetime.now(timezone(timedelta(hours=-5)))
        day = (today.month, today.day)
        st = usgs_water.daily_stats([site.split("-")[1]], [day]).get((site.split("-")[1], "00060"), {}).get(day)
        if st:
            out["normal"] = {"p25": st["p25"], "p50": st["p50"], "p75": st["p75"], "count": st["count"], "date": today.strftime("%b %-d")}
    except Exception as e:
        print("stats", e)
    out["saved"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return out


def health():
    """Call each free, keyless adapter for real and record whether it answered. The landing page reports this, not a card count."""
    import irlpls
    checks = {"usgs-water": (29.9677, -98.905), "nws": (30.2669, -97.7729), "noaa-tides": (37.8063, -122.4659), "caltrans-cctv": (37.79, -122.39),
              "uk-ea-flood": (51.5, -0.12), "pegelonline": (50.94, 6.96), "sensor-community": (52.52, 13.405),
              "copernicus-sentinel": (50.94, 6.96), "fintraffic-weathercams": (60.17, 24.94), "alertcalifornia": (34.1, -118.3)}
    res = {}
    for cid, (lat, lon) in checks.items():
        t0 = time.time()
        try:
            n = len(irlpls.live(cid, lat=lat, lon=lon))
            res[cid] = {"ok": n > 0, "observations": n, "ms": round((time.time() - t0) * 1000)}
        except Exception as e:
            res[cid] = {"ok": False, "error": str(e)[:200]}
        print("health", cid, res[cid])
    return {"checked": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "results": res}


if __name__ == "__main__":
    main()
