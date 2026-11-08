"""Lane C bulk adapter: national WIS2 (wis2box) OGC API nodes -> quarantined candidate Capture Cards.

Discovery: the WMO WIS2 Global Discovery Catalogue (GDC) run by Environment Canada, an OGC API Records endpoint:
  https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items?f=json
Records whose id mentions "synop" and that carry a canonical link give the node origin; wis2box serves its OGC API
under <origin>/oapi. A hand-written seed file (--seed, JSON list of base URLs) is the fallback.

Every network call goes through irlpls.safefetch (injectable as `fetch` for offline tests). A card carries ONLY facts
the node returned (title, rights string, dataPolicy, collection ids, bbox, newest report time, counts); anything not
returned is written as "not stated". Candidates are capped at level Claimed, status unclaimed, never auto-accepted:
they go to a quarantine directory for human or agent review. Nothing here writes to cards/.

  python -m tools.adapters_bulk.wis2 [--out DIR] [--report FILE] [--events FILE] [--seed FILE] [--cap 200]
"""
import argparse
import datetime
import hashlib
import json
import tempfile
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from irlpls import events as _events  # noqa: E402
from irlpls import safefetch  # noqa: E402
from irlpls.gate import gate_card  # noqa: E402

GDC_URL = "https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items"
DEFAULT_OUT = os.path.join(tempfile.gettempdir(), "irlpls-quarantine", "wis2")
JSON_TYPES = ["application/json", "application/geo+json", "application/ld+json"]
NS = "not stated"
CAP = 200
SPEC_VERSION = "2026-10-26"
SYSTEM_COLLECTIONS = {"discovery-metadata", "stations", "messages"}


class Reject(Exception):
    pass


def _get_json(fetch, url, max_bytes=1 << 20):
    try:
        r = fetch(url, max_bytes=max_bytes, timeout=15.0, content_types=JSON_TYPES)
    except safefetch.SafeFetchError as e:
        raise Reject(f"fetch failed ({type(e).__name__}): {e}")
    if r.status != 200:
        raise Reject(f"HTTP {r.status} for {url}")
    try:
        data = json.loads(r.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Reject(f"not JSON: {url}")
    if not isinstance(data, dict):
        raise Reject(f"unexpected JSON shape: {url}")
    return data


def _s(v, n=300):
    """Plain single-line string from a node-supplied value, or None."""
    if not isinstance(v, str):
        return None
    v = re.sub(r"[\x00-\x1f\x7f-\x9f  ​-‏‪-‮⁦-⁩﻿]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v[:n] or None


# discovery -------------------------------------------------------------------
def covered_bases(cards_dir):
    out = set()
    for p in Path(cards_dir).glob("*.json"):
        try:
            b = json.loads(p.read_text(encoding="utf-8")).get("api", {}).get("base_url")
        except (ValueError, OSError):
            continue
        if isinstance(b, str):
            out.add(b.rstrip("/").lower())
    return out


def discover(fetch, pages=6):
    """GDC -> list of {md_id, centre, origin, base_url, title, rights, policy, country, error}. Never raises."""
    found, off = [], 0
    for _ in range(pages):
        try:
            d = _get_json(fetch, f"{GDC_URL}?f=json&limit=300&offset={off}", max_bytes=8 << 20)
        except Reject as e:
            found.append({"error": f"GDC: {e}"})
            break
        feats = d.get("features")
        if not isinstance(feats, list) or not feats:
            break
        for f in feats:
            if not isinstance(f, dict) or "synop" not in str(f.get("id", "")).lower():
                continue
            found.append(_gdc_record(f))
        off += len(feats)
        if off >= (d.get("numberMatched") or 0):
            break
    return found


def _gdc_record(f):
    p = f.get("properties") if isinstance(f.get("properties"), dict) else {}
    md_id = str(f.get("id"))
    rec = {"md_id": md_id, "centre": _s(p.get("centre-id")) or (md_id.split(":")[3] if md_id.count(":") >= 3 else None)}
    canon = [l.get("href") for l in f.get("links", []) if isinstance(l, dict) and l.get("rel") == "canonical"]
    if not canon or not isinstance(canon[0], str):
        rec["error"] = "GDC record names no node URL (no canonical link)"
        return rec
    sp = urlsplit(canon[0])
    if sp.scheme != "https" or not sp.hostname:
        rec["error"] = f"node URL is not https ({sp.scheme or 'none'})"
        return rec
    rec["origin"] = f"https://{sp.netloc}"
    rec["base_url"] = rec["origin"] + "/oapi"
    return rec


def seed_records(seed_file):
    out = []
    for item in json.loads(Path(seed_file).read_text(encoding="utf-8")):
        b = item if isinstance(item, str) else item.get("base_url")
        out.append({"md_id": None if isinstance(item, str) else item.get("md_id"), "centre": None,
                    "base_url": b.rstrip("/"), "origin": b.rstrip("/").rsplit("/oapi", 1)[0]})
    return out


# one node --------------------------------------------------------------------
def _facts(fetch, rec, cache):
    base = rec["base_url"]
    if base not in cache:
        try:
            cache[base] = ("ok", _get_json(fetch, f"{base}/collections?f=json"))
        except Reject as e:
            cache[base] = ("err", str(e))
    kind, cols = cache[base]
    if kind == "err":
        raise Reject(cols)
    lst = cols.get("collections")
    if not isinstance(lst, list):
        raise Reject("/collections has no collections list")
    ids = [c["id"] for c in lst if isinstance(c, dict) and isinstance(c.get("id"), str)]
    if not ids:
        raise Reject("/collections lists no usable collection ids")
    cid = rec.get("md_id") if rec.get("md_id") in ids else None
    if not cid:
        syn = [i for i in ids if "synop" in i.lower() and i not in SYSTEM_COLLECTIONS]
        if not syn:
            raise Reject("no SYNOP observation collection in /collections")
        cid = syn[0]
    col = next(c for c in lst if c.get("id") == cid)
    f = {"cid": cid, "collection_ids": ids, "col_title": _s(col.get("title")), "bbox": None}
    bb = ((col.get("extent") or {}).get("spatial") or {}).get("bbox")
    if isinstance(bb, list) and bb and isinstance(bb[0], list) and len(bb[0]) == 4 and all(isinstance(x, (int, float)) for x in bb[0]):
        f["bbox"] = [round(x, 2) for x in bb[0]]
    items = _get_json(fetch, f"{base}/collections/{cid}/items?f=json&limit=1&sortby=-reportTime")
    feats = items.get("features")
    if not isinstance(feats, list) or not feats or not isinstance(feats[0], dict):
        raise Reject("sample of the SYNOP collection returned no features")
    pr = feats[0].get("properties") if isinstance(feats[0].get("properties"), dict) else {}
    rt = _s(pr.get("reportTime"), 40)
    try:
        f["newest"] = datetime.datetime.fromisoformat(rt.replace("Z", "+00:00")) if rt else None
    except ValueError:
        f["newest"] = None
    if f["newest"] is None:
        raise Reject("sample feature has no parseable reportTime")
    f["newest_s"] = rt
    f["has_time"] = bool(pr.get("phenomenonTime"))
    f["has_coords"] = isinstance(feats[0].get("geometry"), dict)
    f["has_wigos"] = bool(pr.get("wigos_station_identifier"))
    f["matched"] = items.get("numberMatched") if isinstance(items.get("numberMatched"), int) else None
    # the node's own discovery record: title, rights, data policy, contact country
    f.update(title=None, rights=None, policy=None, country=None)
    try:
        md = _get_json(fetch, f"{base}/collections/discovery-metadata/items/{cid}?f=json")
        mp = md.get("properties") if isinstance(md.get("properties"), dict) else {}
        f["title"], f["rights"], f["policy"] = _s(mp.get("title")), _s(mp.get("rights"), 600), _s(mp.get("wmo:dataPolicy"), 40)
        for c in mp.get("contacts") or []:
            for a in (c.get("addresses") or []) if isinstance(c, dict) else []:
                if isinstance(a, dict) and _s(a.get("country"), 60):
                    f["country"] = f["country"] or _s(a.get("country"), 60)
    except Reject:
        pass
    f["stations"] = None
    try:
        st = _get_json(fetch, f"{base}/collections/stations/items?f=json&limit=1")
        f["stations"] = st.get("numberMatched") if isinstance(st.get("numberMatched"), int) else None
    except Reject:
        pass
    return f


def _card_id(rec, cid):
    centre = rec.get("centre") or (cid.split(":")[3] if cid.count(":") >= 3 else None)
    if not centre:
        host = urlsplit(rec["base_url"]).hostname or "node"
        centre = host.split(".")[0]
    s = re.sub(r"[^a-z0-9]+", "-", centre.lower()).strip("-")
    return f"{s}-wis2-synop"[:80]


def build_card(rec, f, now):
    base, cid = rec["base_url"], f["cid"]
    today, host = now.date(), urlsplit(rec["base_url"]).hostname
    centre = rec.get("centre") or NS
    name = f"{f['title'] or f['col_title'] or cid} (WIS2 node {host})"
    age_h = (now - f["newest"]).total_seconds() / 3600 if f["newest"].tzinfo else None
    fresh = age_h is not None and 0 <= age_h <= 6
    counts = []
    if f["matched"] is not None:
        counts.append(f"the collection held {f['matched']:,} features")
    if f["stations"] is not None:
        counts.append(f"the node's station list {f['stations']:,} stations")
    summary = (f"WIS2 node at {host} (WMO centre {centre}) lists a SYNOP observation collection '{cid}' on its OGC API "
               f"Features endpoint. A keyless GET sorted by -reportTime on {today} UTC returned a report stamped "
               f"{f['newest_s']}" + ("; " + " and ".join(counts) if counts else "") + ". Compiled automatically from "
               "the node's own responses; not reviewed by a person.")
    rights = f["rights"]
    lic = (f"The node's discovery record gives rights: '{rights}'. " if rights else "The node's discovery record states no rights text. ")
    lic += (f"WMO data policy declared: '{f['policy']}'. " if f["policy"] else "No WMO data policy was returned. ")
    lic += "Attribution, resale and AI training are not stated by the provider."
    country = f["country"]
    areas = []
    m = re.match(r"^([a-z]{2})-", (rec.get("centre") or "").lower())
    if country and m and m.group(1) not in ("uk",):
        areas = [m.group(1).upper()]  # ISO code taken from the WMO centre id prefix, only when the node also names a country
    cov_notes = (f"Bounding box of the collection as returned by the node: {f['bbox']}. " if f["bbox"] else "No bounding box returned by the node. ") \
        + "Station density and which stations report were not audited."
    claimed = lambda ok, how: ({"level": "claimed", "method": how,
                                "unknown": "Compiled from one sample; accuracy not checked."} if ok else
                               {"level": "none", "unknown": "Not seen in the sample."})
    card = {
        "family": "weather-station", "status": "unclaimed",
        "pricing": {"model": "free", "from_usd": 0, "unit": "request"}, "capture": {"by": "devices"},
        "spec_version": SPEC_VERSION, "capability": "com.irlpls.capture.card",
        "updated": str(today), "expires": str(today.replace(year=today.year + 1)),
        "id": _card_id(rec, cid), "name": name[:200], "provider_url": rec["origin"],
        "short": f"Latest synoptic weather observations from the WIS2 node at {host}: OGC API, no key.",
        "summary": summary,
        "capabilities": {"modalities": ["sensor"], "tasks": ["latest surface weather observations at national stations",
                                                           "station list with WIGOS identifiers"]},
        "coverage": {"regions": [country or NS], "areas": areas, "notes": cov_notes},
        "access": {"cost": "free", "key_required": False, "tested_live": str(today), "gate": "none"},
        "delivery": {"modes": ["live"], "speed": "hours" if fresh else "unknown",
                     "basis": f"Live GET {today} UTC: newest report {f['newest_s']}."},
        "latency": {"typical": NS, "notes": "One sample only."},
        "freshness": {"mode": "live", "typical_age": NS, "notes": "One sample only."},
        "licensing": {"summary": lic},
        "verification": {
            "human": {"level": "none", "unknown": "Not applicable: automated instruments."},
            "application": {"level": "none", "unknown": "No signing or attestation documented."},
            "camera": {"level": "none", "unknown": "Not applicable: no camera frames."},
            "corroboration": {"level": "none", "unknown": "Nothing documented independently confirms readings."},
            "time": claimed(f["has_time"], "phenomenonTime and reportTime fields in each feature."),
            "location": claimed(f["has_coords"], "Station geometry in each feature."),
            "content": claimed(f["has_wigos"], "Values as published by the node, keyed by WIGOS station identifier."),
        },
        "api": {"available": True, "self_serve": True, "base_url": base,
                "auth": [{"type": "none", "how_to_get": "No key sent; none required."}],
                "endpoints": [
                    {"action": "fetch", "method": "GET", "path": f"/collections/{cid}/items",
                     "params": "f=json|csv, limit, offset, sortby=-reportTime", "notes": "Decoded observations; sort by -reportTime for newest."},
                    {"action": "search", "method": "GET", "path": "/collections/stations/items", "params": "f=json, limit",
                     "notes": "Station list."}],
                "rate_limits": NS,
                "notes": f"Collection ids returned by the node: {', '.join(f['collection_ids'][:12])}."},
        "tags": ["weather station", "weather observations", "wis2", "wmo", "synop", "government", "free", "hourly"],
        "sources": [
            {"claim": "Node collection list and sample", "url": f"{base}/collections?f=json"},
            {"claim": f"Newest report {f['newest_s']}", "url": f"{base}/collections/{cid}/items?f=json&limit=1&sortby=-reportTime"}],
    }
    return card


def not_stated_fields(f):
    out = [k for k, v in (("license/rights", f["rights"]), ("data policy", f["policy"]), ("country", f["country"]),
                          ("bbox", f["bbox"]), ("station count", f["stations"])) if not v]
    return out


# run -------------------------------------------------------------------------
def run(out_dir, fetch=None, cards_dir=None, cap=CAP, seed=None, events_path=None, now=None, discovered=None):
    fetch = fetch or safefetch.fetch
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cards_dir = cards_dir if cards_dir is not None else ROOT / "cards"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    recs = seed_records(seed) if seed else (discovered if discovered is not None else discover(fetch))
    skip = covered_bases(cards_dir)
    report, cache, seen_ids, seen_pair, n_cards = [], {}, set(), set(), 0
    for rec in recs:
        entry = {"md_id": rec.get("md_id"), "base_url": rec.get("base_url")}
        report.append(entry)
        try:
            if rec.get("error"):
                raise Reject(rec["error"])
            if rec["base_url"].rstrip("/").lower() in skip:
                entry.update(status="skipped", reasons=["already covered by a card in cards/ (base_url match)"])
                continue
            if (rec["base_url"], rec.get("md_id")) in seen_pair:
                entry.update(status="skipped", reasons=["duplicate record"])
                continue
            seen_pair.add((rec["base_url"], rec.get("md_id")))
            if n_cards >= cap:
                entry.update(status="skipped", reasons=[f"cap of {cap} candidates reached"])
                continue
            f = _facts(fetch, rec, cache)
            card = build_card(rec, f, now)
            if card["id"] in seen_ids:
                raise Reject(f"second dataset for centre already emitted as {card['id']}")
            entry.update(card_id=card["id"], not_stated=not_stated_fields(f))
            g = gate_card(card)
            if not g.ok:
                entry.update(status="rejected", reasons=[f"gate: {e['code']} {e['pointer']} {e['message']}"[:300] for e in g.errors])
                continue
            seen_ids.add(card["id"])
            n_cards += 1
            data = json.dumps(card, indent=2, ensure_ascii=False) + "\n"
            (out / f"{card['id']}.json").write_text(data, encoding="utf-8")
            entry.update(status="accepted", reasons=[], gate_warnings=len(g.warnings))
            if events_path:
                _events.append(events_path, "submitted", domain=urlsplit(rec["origin"]).hostname, card_id=card["id"],
                               version_sha256=hashlib.sha256(data.encode("utf-8")).hexdigest(), actor="adapter:wis2",
                               evidence=[card["sources"][0]["url"]])
        except Reject as e:
            entry.update(status="rejected", reasons=[str(e)])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--report", help="JSON report path (default: <out>/_report.json)")
    ap.add_argument("--events", help="append 'submitted' events to this registry log")
    ap.add_argument("--seed", help="JSON list of node base URLs instead of GDC discovery")
    ap.add_argument("--cap", type=int, default=CAP)
    a = ap.parse_args(argv)
    rep = run(a.out, cap=min(a.cap, CAP), seed=a.seed, events_path=a.events)
    summary = {s: sum(1 for r in rep if r.get("status") == s) for s in ("accepted", "rejected", "skipped")}
    path = Path(a.report) if a.report else Path(a.out) / "_report.json"
    path.write_text(json.dumps({"summary": summary, "nodes": rep}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary), "report:", path)


if __name__ == "__main__":
    main()
