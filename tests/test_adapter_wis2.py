"""tools/adapters_bulk/wis2.py, fully offline: injected fetcher fixtures, no network."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from irlpls import events, safefetch  # noqa: E402
from irlpls.gate import gate_card  # noqa: E402
from tools.adapters_bulk import wis2  # noqa: E402
import datetime  # noqa: E402

NOW = datetime.datetime(2026, 11, 8, 13, 0, tzinfo=datetime.timezone.utc)
CID = "urn:wmo:md:xx-testmet:synop"


def node(host, rights="Free use with attribution", report="2026-11-08T12:00:00Z", policy="core", cid=CID):
    base = f"https://{host}/oapi"
    return {
        f"{base}/collections?f=json": {"collections": [
            {"id": "discovery-metadata", "title": "d"}, {"id": "stations", "title": "s"},
            {"id": cid, "title": "Obs", "extent": {"spatial": {"bbox": [[1.0, 2.0, 3.0, 4.0]]}}}]},
        f"{base}/collections/{cid}/items?f=json&limit=1&sortby=-reportTime": {
            "numberMatched": 1234, "features": [{"geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {
                "reportTime": report, "phenomenonTime": report, "wigos_station_identifier": "0-1-2-3"}}]},
        f"{base}/collections/discovery-metadata/items/{cid}?f=json": {"properties": {
            "title": "Hourly SYNOP of Testland", "rights": rights, "wmo:dataPolicy": policy,
            "contacts": [{"addresses": [{"country": "Testland"}]}]}},
        f"{base}/collections/stations/items?f=json&limit=1": {"numberMatched": 42, "features": []},
    }


def make_fetch(table, raw=None, blocked=()):
    raw = raw or {}

    def fetch(url, **kw):
        host = url.split("/")[2]
        if host in blocked:
            raise safefetch.BlockedAddress("non-public address")
        if url in raw:
            return SimpleNamespace(status=200, headers={}, body=raw[url], url=url)
        if url in table:
            return SimpleNamespace(status=200, headers={}, body=json.dumps(table[url]).encode(), url=url)
        return SimpleNamespace(status=404, headers={}, body=b"", url=url)
    return fetch


def rec(host, centre="xx-testmet"):
    return {"md_id": CID, "centre": centre, "origin": f"https://{host}", "base_url": f"https://{host}/oapi"}


def go(tmp_path, recs, table, **kw):
    return wis2.run(tmp_path / "q", fetch=make_fetch(table, **kw.pop("fx", {})), cards_dir=tmp_path / "cards",
                    now=NOW, discovered=recs, **kw)


def test_good_node_passes_gate_and_uses_only_returned_facts(tmp_path):
    rep = go(tmp_path, [rec("good.example")], node("good.example"))
    assert rep[0]["status"] == "accepted", rep
    card = json.loads((tmp_path / "q" / "xx-testmet-wis2-synop.json").read_text())
    assert gate_card(card).ok
    assert card["status"] == "unclaimed" and card["api"]["auth"][0]["type"] == "none"
    assert "Free use with attribution" in card["licensing"]["summary"]
    assert card["coverage"]["regions"] == ["Testland"] and card["coverage"]["areas"] == ["XX"]
    assert "1,234" in card["summary"] and "42 stations" in card["summary"]
    assert all(v["level"] in ("none", "claimed") for v in card["verification"].values())


def test_missing_license_is_not_stated(tmp_path):
    t = node("nolic.example", rights=None)
    t["https://nolic.example/oapi/collections/discovery-metadata/items/" + CID + "?f=json"]["properties"].pop("wmo:dataPolicy")
    rep = go(tmp_path, [rec("nolic.example")], t)
    assert rep[0]["status"] == "accepted" and "license/rights" in rep[0]["not_stated"]
    card = json.loads((tmp_path / "q" / "xx-testmet-wis2-synop.json").read_text())
    assert "states no rights text" in card["licensing"]["summary"]


def test_garbage_node_rejected_with_reason(tmp_path):
    u = "https://junk.example/oapi/collections?f=json"
    rep = go(tmp_path, [rec("junk.example")], {}, fx={"raw": {u: b"<html>nope</html>"}})
    assert rep[0]["status"] == "rejected" and "not JSON" in rep[0]["reasons"][0]
    assert not list((tmp_path / "q").glob("*.json"))


def test_private_ip_node_rejected_never_fetched_through(tmp_path):
    rep = go(tmp_path, [rec("internal.example")], node("internal.example"), fx={"blocked": ("internal.example",)})
    assert rep[0]["status"] == "rejected" and "BlockedAddress" in rep[0]["reasons"][0]


def test_default_fetcher_refuses_private_and_literal_hosts():
    import pytest
    f = safefetch.Fetcher(resolver=lambda h, p: ["10.0.0.5"])
    with pytest.raises(safefetch.SafeFetchError):
        f.fetch("https://intranet.example/oapi/collections?f=json")
    with pytest.raises(safefetch.SafeFetchError):
        f.fetch("https://192.0.2.1/oapi/collections?f=json")


def test_covered_nodes_skipped_and_cap(tmp_path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "a.json").write_text(json.dumps({"api": {"base_url": "https://good.example/oapi/"}}))
    t = {**node("good.example"), **node("other.example")}
    rep = go(tmp_path, [rec("good.example"), rec("other.example")], t)
    assert rep[0]["status"] == "skipped" and rep[1]["status"] == "accepted"
    rep = go(tmp_path, [rec("other.example", "yy-m")], t, cap=0)
    assert rep[0]["status"] == "skipped" and "cap" in rep[0]["reasons"][0]


def test_events_option_appends_submitted(tmp_path):
    log = tmp_path / "reg.ndjson"
    go(tmp_path, [rec("good.example")], node("good.example"), events_path=log)
    ev = events.read(log)
    assert [e["action"] for e in ev] == ["submitted"] and ev[0]["actor"] == "adapter:wis2"
    assert events.verify_chain(log) == (True, [])


def test_discover_from_gdc_page(tmp_path):
    page = {"numberMatched": 3, "features": [
        {"id": "urn:wmo:md:aa-m:synop", "properties": {"centre-id": "aa-m"},
         "links": [{"rel": "canonical", "href": "https://wis.aa.example/data/metadata/x.json"}]},
        {"id": "urn:wmo:md:bb-m:synop", "properties": {}, "links": []},
        {"id": "urn:wmo:md:cc-m:temp", "properties": {}, "links": []}]}
    out = wis2.discover(make_fetch({f"{wis2.GDC_URL}?f=json&limit=300&offset=0": page}))
    assert out[0]["base_url"] == "https://wis.aa.example/oapi" and out[0]["centre"] == "aa-m"
    assert len(out) == 2 and "no node URL" in out[1]["error"]
