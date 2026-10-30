"""Three closed protocol items: the receipt binding for value items (PROTOCOL-DRAFT 5.4, STANDARD 14.7), the ProxyPics
status_map, and registrable-domain checks from the vendored Public Suffix List (STANDARD 14.2)."""
import hashlib
import json
import urllib.parse

import pytest

from irlpls import conformance, psl
from irlpls.adapters import get as adapter
from irlpls.protocol import client as C
from irlpls.protocol.receipts import (ReceiptSigner, expected_media_sha256, jcs, value_document, value_media)

ISS = "https://provider.example.com"
READING = {"id": "itm_1", "provider": "x", "kind": "gage-height", "phenomenon_time": "2026-10-28T19:30:00Z",
           "value": 3.4, "unit": "ft", "label": "Gage height", "lat": 30.2, "lon": -97.7}
DOC_SHA = "383d9858481e67f712e4d74234c637a71a17a8a2f42a3bf87b75fdfaf6340406"   # the example in PROTOCOL-DRAFT and STANDARD


# ------------------------------------------------------------------------------------------- value-item receipt binding

def test_value_document_is_exactly_four_members_in_rfc8785_form():
    assert value_document(READING) == b'{"kind":"gage-height","phenomenon_time":"2026-10-28T19:30:00Z","unit":"ft","value":3.4}'
    assert value_media(READING) == {"sha256": DOC_SHA, "media_type": "application/json", "bytes": 87}
    assert value_media({**READING, "value": 3.40})["sha256"] == DOC_SHA                       # same number, same bytes
    assert value_media({**READING, "label": "other", "id": "itm_9", "lat": 0})["sha256"] == DOC_SHA   # not bound here


def test_value_document_nulls_missing_members_and_binds_unit_and_time():
    bare = {"kind": "count", "value": 7}
    assert json.loads(value_document(bare)) == {"kind": "count", "value": 7, "unit": None, "phenomenon_time": None}
    for k, v in (("unit", "m"), ("phenomenon_time", "2026-10-28T19:31:00Z"), ("value", 3.5), ("kind", "discharge")):
        assert value_media({**READING, k: v})["sha256"] != DOC_SHA, k
    with pytest.raises(ValueError):
        value_document({**READING, "value": float("nan")})


def test_expected_media_hash_uses_sha256_for_files_and_the_value_document_otherwise():
    assert expected_media_sha256({**READING, "sha256": "ab" * 32}) == "ab" * 32
    assert expected_media_sha256(READING) == DOC_SHA


def _signed(item, signer, **over):
    payload = {"iss": ISS, "order_id": "ord_1", "item_id": item["id"], "nonce": "n" * 22, "request_sha256": "0" * 64,
               "media": value_media(item), "phenomenon_time": item["phenomenon_time"], "issued_at": "2026-10-28T19:32:00Z",
               **{k: item[k] for k in ("lat", "lon", "precision_m") if k in item}}   # place is bound to the item too
    payload.update(over)
    return {**item, "receipt": signer.sign(payload)}


def test_client_verifies_value_items_and_catches_a_changed_reading():
    signer = ReceiptSigner.generate(kid="k1")
    jwks = {"keys": [signer.public_jwk()]}
    item = _signed(READING, signer)
    ok = C.verify_receipt(item, jwks, ISS, order_id="ord_1")
    assert ok["verified"] is True, ok
    assert C.verify_receipt({**item, "label": "relabelled"}, jwks, ISS)["verified"] is True
    for k, v in (("value", 9.9), ("unit", "m"), ("phenomenon_time", "2026-10-27T00:00:00Z")):
        bad = C.verify_receipt({**item, k: v}, jwks, ISS)
        assert bad["verified"] is False and ("media" in bad["reason"]), (k, bad)
    old = _signed(READING, signer, media={"sha256": hashlib.sha256(jcs({"kind": "gage-height", "value": 3.4, "unit": "ft"})).hexdigest(),
                                          "media_type": "application/json", "bytes": 0})
    assert C.verify_receipt(old, jwks, ISS)["verified"] is False      # the pre-spec {kind,value,unit} binding no longer passes


def test_conformance_checks_value_item_receipts_with_the_same_binding():
    signer = ReceiptSigner.generate(kid="k1")
    jwks = {"keys": [signer.public_jwk()]}
    item = _signed(READING, signer)
    expect = {"iss": ISS, "item_id": "itm_1", "media_sha256": conformance._expected_media(item)}
    assert not [r for r in conformance.verify_receipt(item["receipt"], jwks, expect) if r[0] == conformance.FAIL]
    expect["media_sha256"] = conformance._expected_media({**item, "value": 1})
    assert [r for r in conformance.verify_receipt(item["receipt"], jwks, expect) if r[0] == conformance.FAIL]


def test_server_shim_client_and_conformance_share_one_binding():
    import inspect
    from irlpls.protocol import server, shim
    assert "value_media(item)" in inspect.getsource(server.ProviderServer._add_item)
    assert "value_media(it)" in inspect.getsource(shim.AdapterShim._receipt)
    from irlpls.protocol import receipts
    assert "item=item" in inspect.getsource(C.verify_receipt)              # one verifier, with the item bindings
    assert "value_media(item)" in inspect.getsource(receipts.check_item_bindings)
    assert "R.verify_receipt" in inspect.getsource(conformance.verify_receipt)
    assert "expected_media_sha256" in inspect.getsource(conformance._expected_media)


# ----------------------------------------------------------------------------------------------- ProxyPics status_map

@pytest.mark.parametrize("raw,state", [("unassigned", "in_flight"), ("assigned", "in_flight"),
                                       ("completed", "in_flight"),   # uploaded, awaiting the client's approval
                                       ("rejected", "in_flight"),    # client rejected: re-captured, job continues
                                       ("fulfilled", "delivered"), ("expired", "failed"), ("cancelled", "failed"),
                                       ("FULFILLED", "delivered"), ("on_hold", None), (None, None)])
def test_proxypics_status_map(raw, state):
    assert adapter("proxypics").map_status(raw) == state


def test_proxypics_status_map_covers_the_cards_documented_statuses():
    card = json.loads((psl.DATA.parents[2] / "cards" / "proxypics.json").read_text())
    notes = next(e["notes"] for e in card["api"]["endpoints"] if e.get("action") == "status")
    words = ("unassigned", "assigned", "completed", "rejected", "expired", "fulfilled")
    assert all(w in notes for w in words)
    assert all(adapter("proxypics").map_status(w) for w in words)


def test_proxypics_fulfilled_delivers_photos_and_report_without_inventing_a_time():
    a = adapter("proxypics")
    out = a.deliverables({"status": "fulfilled", "latitude": 39.8, "longitude": -89.6,
                          "download_photos_url": "https://cdn.proxypics.com/p.zip",
                          "download_report_url": "https://cdn.proxypics.com/r.pdf"})
    assert [(o["kind"], o["url"]) for o in out] == [("photo", "https://cdn.proxypics.com/p.zip"),
                                                    ("report", "https://cdn.proxypics.com/r.pdf")]
    assert all(o["phenomenon_time"] is None and o["lat"] == 39.8 for o in out)
    assert a.deliverables({"status": "completed"}) == [] and a.deliverables(None) == []


# ------------------------------------------------------------------------------------------- registrable domain (PSL)

def test_psl_snapshot_is_vendored_with_date_and_licence():
    head = psl.DATA.read_text(encoding="utf-8")[:1200]
    assert "Fetched 2026-10-28" in head and "MPL-2.0" in head and "Mozilla Public" in head
    assert "public_suffix_list.dat" in (psl.DATA.parents[2] / "docs" / "LICENSING.md").read_text()


@pytest.mark.parametrize("host,want", [
    ("api.example.co.uk", "example.co.uk"), ("example.co.uk", "example.co.uk"), ("co.uk", None), ("uk", None),
    ("alice.github.io", "alice.github.io"), ("x.alice.github.io", "alice.github.io"), ("github.io", None),
    ("api.example.com", "example.com"), ("EXAMPLE.COM.", "example.com"), ("com", None),
    ("a.b.ck", "a.b.ck"), ("www.ck", "www.ck"),                       # wildcard *.ck and exception !www.ck
    ("city.kawasaki.jp", "city.kawasaki.jp"), ("x.y.kawasaki.jp", "x.y.kawasaki.jp"),
    ("shop.example.com.au", "example.com.au"), ("münchen.de", "xn--mnchen-3ya.de"),
    ("127.0.0.1", None), ("::1", None), ("", None), (None, None)])
def test_registrable_domain(host, want):
    assert psl.registrable_domain(host) == want


@pytest.mark.parametrize("a,b,same", [
    ("api.example.co.uk", "www.example.co.uk", True), ("a.co.uk", "b.co.uk", False), ("co.uk", "co.uk", True),
    ("alice.github.io", "bob.github.io", False), ("api.alice.github.io", "alice.github.io", True),
    ("api.example.com", "example.com", True), ("evil-example.com", "example.com", False),
    ("localhost", "localhost", True), ("127.0.0.1", "127.0.0.1", True), ("127.0.0.1", "127.0.0.2", False),
    ("", "", False), ("example.com", None, False)])
def test_same_site(a, b, same):
    assert psl.same_site(a, b) is same
    assert C.same_site(a, b) is same and conformance._same_site(a, b) is same


def _card(endpoint, provider_url):
    return {"id": "p", "provider_url": provider_url, "protocol": {"versions": [C.VERSION], "endpoint": endpoint}}


@pytest.mark.parametrize("endpoint,provider_url,ok", [
    ("https://api.shop.co.uk/irlpls", "https://www.shop.co.uk", True),
    ("https://api.other.co.uk/irlpls", "https://www.shop.co.uk", False),       # the old heuristic said "co.uk" == "co.uk"
    ("https://bob.github.io/irlpls", "https://alice.github.io", False),        # the old heuristic said "github.io" == "github.io"
    ("https://alice.github.io/irlpls", "https://alice.github.io", True),
    ("https://api.example.com/irlpls", "https://example.com", True)])
def test_client_check_card_uses_the_psl(endpoint, provider_url, ok):
    card = _card(endpoint, provider_url)
    if ok:
        assert C.check_card(card)
    else:
        with pytest.raises(C.UnsafeEndpoint):
            C.check_card(card)
    fetched = "https://cards.other.co.uk/p.json"
    with pytest.raises(C.UnsafeEndpoint):
        C.check_card(_card("https://api.shop.co.uk/irlpls", "https://www.shop.co.uk"), fetched_from=fetched)
    assert C.registrable("api.shop.co.uk") == "shop.co.uk" and C.registrable("10.0.0.1") == "10.0.0.1"
    assert C.registrable("irlpls.com") in C.HOSTED_ROUTER_DOMAINS and C.registrable("api.irlpls.com") == "irlpls.com"


def test_conformance_site_uses_the_psl():
    assert conformance._site("api.example.co.uk") == "example.co.uk"
    assert conformance._site("alice.github.io") == "alice.github.io"
    assert conformance._site("localhost") == "localhost"


# ------------------------------------------------------------------- card ownership: exact host or its "www." twin

OWNER = [
    ("example.com", "example.com", True), ("EXAMPLE.com.", "example.com", True),           # exact host, normalised
    ("www.example.com", "example.com", True), ("example.com", "www.example.com", True),   # the www twin
    ("www.shop.co.uk", "shop.co.uk", True), ("alice.github.io", "alice.github.io", True),
    ("sub.example.com", "example.com", False), ("example.com", "sub.example.com", False),  # a subdomain is not the owner
    ("www.sub.example.com", "example.com", False),
    ("example.com.evil.net", "example.com", False), ("evil-example.com", "example.com", False),
    ("evil-proxypics.com", "proxypics.com", False),
    ("bar.github.io", "foo.github.io", False), ("foo.github.io", "github.io", False),
    ("www.github.io", "github.io", False), ("www.co.uk", "co.uk", False),                  # no registrable domain
    ("münchen.de", "xn--mnchen-3ya.de", True), ("www.MÜNCHEN.de", "xn--mnchen-3ya.de", True),  # IDN to punycode
    ("xn--mnchen-3ya.de", "münchen.de", True), ("sub.münchen.de", "münchen.de", False),
    ("127.0.0.1", "127.0.0.1", True), ("", "", False), ("example.com", None, False), (None, "example.com", False)]


@pytest.mark.parametrize("host,domain,ok", OWNER)
def test_same_owner(host, domain, ok):
    assert psl.same_owner(host, domain) is ok


def _owned_card(provider_url):
    return {"id": "p", "provider_url": provider_url, "status": "provider-maintained",
            "protocol": {"versions": [C.VERSION], "endpoint": provider_url + "/irlpls"}}


@pytest.mark.parametrize("fetched,provider_url,ok", [
    ("https://example.com/.well-known/irlpls", "https://example.com", True),
    ("https://www.example.com/.well-known/irlpls", "https://example.com", True),
    ("https://sub.example.com/.well-known/irlpls", "https://example.com", False),
    ("https://example.com.evil.net/.well-known/irlpls", "https://example.com", False),
    ("https://evil-proxypics.com/.well-known/irlpls", "https://proxypics.com", False),
    ("https://bar.github.io/.well-known/irlpls", "https://foo.github.io", False),
    ("https://xn--mnchen-3ya.de/.well-known/irlpls", "https://münchen.de", True)])
def test_card_ownership_is_one_rule_everywhere(fetched, provider_url, ok):
    from irlpls import validate
    card = _owned_card(provider_url)
    errs = [p for p in validate.problems(card, fetched_from=fetched) if p[1] == "status" and p[0] == "error"]
    assert (not errs) is ok
    if ok:
        assert C.check_card(card, fetched_from=fetched)
    else:
        with pytest.raises(C.UnsafeEndpoint):
            C.check_card(card, fetched_from=fetched)
    assert conformance._same_owner(urllib.parse.urlsplit(fetched).hostname, urllib.parse.urlsplit(provider_url).hostname) is ok
