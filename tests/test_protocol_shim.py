"""The adapter shim (irlpls/protocol/shim.py) speaking the wire protocol in front of two reference adapters:
usgs-water (live, delivered in the order response) and proxypics (commission; quote-only unless the operator enables
orders and credentials are set). No network: every provider call is a recorded response through a monkeypatched
irlpls.http, and the one real socket is the shim's own server on 127.0.0.1. No real order is ever placed."""
import base64
import hashlib
import json
import pathlib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import irlpls
from irlpls import http
from irlpls.adapters import usgs_water
from irlpls.protocol import shim as S

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "https://irlpls.com/2026-10-26/schemas/"
BASE_URL = "https://shim.example.org/irlpls"
V = {"Irlpls-Version": "2026-10-26"}


# ------------------------------------------------------------------------------------------------------------ helpers

@pytest.fixture(scope="module")
def validate():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    reg = Registry()
    for f in list((ROOT / "schema").glob("*.schema.json")) + list((ROOT / "schema/protocol").glob("*.schema.json")):
        s = json.loads(f.read_text())
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))

    def check(instance, target):
        v = Draft202012Validator({"$ref": BASE + "protocol/" + target}, registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER)
        errs = [f"{list(e.path)}: {e.message[:200]}" for e in v.iter_errors(instance)]
        assert not errs, errs
    return check


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_jws(receipt, jwk):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    h, empty, sig = receipt["jws"].split(".")
    hdr = json.loads(b64d(h))
    assert empty == "" and hdr == {"alg": "ES256", "kid": jwk["kid"], "typ": "irlpls-receipt+jws"}
    signing_input = f"{h}.{base64.urlsafe_b64encode(S.jcs(receipt['payload'])).rstrip(b'=').decode()}".encode()
    pub = ec.EllipticCurvePublicNumbers(int.from_bytes(b64d(jwk["x"]), "big"), int.from_bytes(b64d(jwk["y"]), "big"), ec.SECP256R1()).public_key()
    raw = b64d(sig)
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), signing_input, ec.ECDSA(hashes.SHA256()))


STATS = [("08167000", "00060", "ft3/s", 51, 103, 201, 86), ("08167000", "00065", "ft", 2.19, 2.79, 3.38, 36)]


def normals(params, rows):
    """A USGS statistics API observationNormals reply (shape of fixtures/adapters/usgs-water/observation_normals.json):
    rows of (site number, parameter code, unit, p25, p50, p75, years) for the day the request asks for."""
    day = (params or {}).get("start_date")
    feats = {}
    for site, code, unit, p25, p50, p75, n in rows:
        f = feats.setdefault(site, {"type": "Feature", "properties": {"monitoring_location_id": "USGS-" + site, "data": []}})
        f["properties"]["data"].append({"parameter_code": code, "unit_of_measure": unit, "parent_statistic_id": "00003", "values": [
            {"time_of_year": day, "time_of_year_type": "day_of_year", "computation": "percentile", "sample_count": n,
             "percentiles": ["5", "10", "25", "50", "75", "90", "95"], "values": ["1", "2", str(p25), str(p50), str(p75), "900", "999"]}]})
    return {"type": "FeatureCollection", "features": list(feats.values()), "next": None}



@pytest.fixture
def usgs_net(monkeypatch):
    """Recorded USGS responses: a discharge and a gage height at Comfort, TX, the daily statistics, one river camera."""
    calls = []
    t = (datetime.now(timezone.utc) - timedelta(minutes=20)).replace(microsecond=0, second=0)

    def get(url, params=None, headers=None, raw=False, **kw):
        calls.append(url)
        if "latest-continuous" in url:
            val, unit = {"00060": ("74.2", "ft3/s"), "00065": ("2.31", "ft")}[params["parameter_code"]]
            return {"features": [{"geometry": {"coordinates": [-98.90457, 29.96994]}, "properties": {
                "monitoring_location_id": "USGS-08167000", "time": t.isoformat(), "value": val, "unit_of_measure": unit,
                "approval_status": "Provisional"}}]}
        if "observationNormals" in url:
            return normals(params, STATS)
        if url.endswith("/cameras"):
            return [{"camId": "TX_Guadalupe_Comfort", "lat": "29.9700", "lng": "-98.9046", "nwisId": "08167000", "camName": "Guadalupe at Comfort",
                     "smallDir": "https://usgs-nims-images.s3.amazonaws.com/overlay/TX_Guadalupe_Comfort/", "hideCam": False}]
        if "listFiles" in url:
            return ["TX_Guadalupe_Comfort___" + t.strftime("%Y-%m-%dT%H-%M-%SZ") + ".jpg"]
        raise AssertionError("unexpected URL " + url)

    def post(*a, **k):
        raise AssertionError("no POST expected")
    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(http, "post", post)
    monkeypatch.setattr(usgs_water, "_cameras", None)
    monkeypatch.delenv("USGS_WATER_API_KEY", raising=False)
    return calls


JPEG = b"\xff\xd8\xff\xe0 recorded river frame \xff\xd9"


def fake_media(url):
    assert url.startswith("https://usgs-nims-images.s3.amazonaws.com/")
    return JPEG, "image/jpeg"


def usgs_shim(**kw):
    return S.AdapterShim("usgs-water", base_url=BASE_URL, media_fetcher=fake_media, **kw)


RIVER = {"need": "river level at Comfort, TX now", "lat": 29.9683, "lon": -98.9054, "country_code": "US",
         "mode": "live", "modalities": ["sensor"], "require": ["time", "location", "content"]}
DIRECT = {"request": RIVER, "constraints": {"max_total": {"amount": "0", "currency": "USD"}}, "nonce": "q8XvY2m1Lw0aPp5sR3tU7w"}


def order(shim, body, key="key-0001", **h):
    if isinstance(body, dict) and "nonce" not in body:   # every order carries a nonce (STANDARD 14.5)
        body = dict(body, nonce=DIRECT["nonce"])
    return shim.handle("POST", "/irlpls/orders", {**V, "Idempotency-Key": key, **h}, json.dumps(body).encode())


# --------------------------------------------------------------------------------------------------- usgs-water (live)

def test_discover_serves_the_card_unclaimed_with_a_shim_operator(validate):
    r = usgs_shim().handle("GET", "/.well-known/irlpls")
    card = r.body
    assert r.status == 200 and card["status"] == "unclaimed" and card["verification"] == irlpls.card("usgs-water")["verification"]
    p = card["protocol"]
    validate(p, "common.json#/$defs/protocol_block")
    assert p["operator"]["role"] == "shim" and p["endpoint"] == BASE_URL and p["quote"] == "optional" and p["cancel"] is False


def test_usgs_quote_is_indicative_free_and_carries_the_cards_levels(usgs_net, validate):
    r = usgs_shim().handle("POST", "/irlpls/quotes", V, json.dumps({"request": RIVER}))
    assert r.status == 201 and r.headers["Location"] == f"{BASE_URL}/quotes/{r.body['id']}" and r.headers["Irlpls-Version"] == "2026-10-26"
    validate(r.body, "quote.json")
    (o,) = r.body["offers"]
    card = irlpls.card("usgs-water")
    assert o["route"] == "live" and o["firm"] is False and o["total"] == {"amount": "0", "currency": "USD"}
    assert o["proof"] == {g: card["verification"][g]["level"] for g in S.GUARANTEES}
    assert o["order_url"] == BASE_URL + "/orders"
    assert r.body["request_sha256"] == hashlib.sha256(S.jcs(RIVER)).hexdigest()
    assert usgs_net == []                     # a quote calls nothing


def test_usgs_direct_order_delivers_now_with_card_provenance_and_hashed_photo(usgs_net, validate):
    shim = usgs_shim()
    r = order(shim, DIRECT)
    assert r.status == 200, r.body
    validate(r.body, "status.json")
    o = r.body
    assert o["state"] == "delivered" and o["total"] == {"amount": "0", "currency": "USD"} and o["route"] == "live"
    d = o["delivery"]
    validate(d, "delivery.json")
    kinds = sorted(i["label"] if i["kind"] == "sensor" else i["kind"] for i in d["items"])
    assert kinds == ["discharge", "gage height", "photo"] and o["progress"]["delivered"] == 3
    q = next(i for i in d["items"] if i.get("label") == "discharge")
    assert q["value"] == 74.2 and q["daily_median"] == 103.0 and q["sensor"] == {"kind": "device"} and q["provider"] == "usgs-water"
    photo = next(i for i in d["items"] if i["kind"] == "photo")
    assert photo["sha256"] == hashlib.sha256(JPEG).hexdigest() and photo["bytes"] == len(JPEG) and photo["media_type"] == "image/jpeg"
    # provenance: every guarantee from the card, basis card, never above the card's level
    card = irlpls.card("usgs-water")
    for st in d["provenance"]["stages"]:
        for g, e in (st.get("guarantees") or {}).items():
            assert e["basis"] == "card" and e["level"] == card["verification"][g]["level"]
    assert [s["stage"] for s in d["provenance"]["stages"]] == [s[0] for s in S.STAGES]
    assert not any("receipt" in i for i in d["items"])                       # no key, no receipts
    assert "no signing key" in d["provenance"]["stages"][-1]["unknown"]
    assert d["relayed_by"][0]["role"] == "shim"
    # status and deliver read back the same thing
    assert shim.handle("GET", f"/irlpls/orders/{o['id']}", V).body["state"] == "delivered"
    assert shim.handle("GET", f"/irlpls/orders/{o['id']}/delivery", V).body == d


def test_idempotency_replays_and_refuses_reuse_with_another_body(usgs_net):
    shim = usgs_shim()
    first = order(shim, DIRECT, key="k-river")
    n = len(usgs_net)
    again = order(shim, DIRECT, key="k-river")
    assert again.body == first.body and len(usgs_net) == n           # stored response; the provider was not called again
    other = order(shim, dict(DIRECT, nonce="Zx3qN0p8YgS1rT6uWv9a2Q"), key="k-river")
    assert other.status == 422 and other.body["code"] == "idempotency_key_reused"
    missing = shim.handle("POST", "/irlpls/orders", V, json.dumps(DIRECT))
    assert missing.status == 400 and missing.body["code"] == "idempotency_key_required"


def test_order_from_a_quote_and_quote_expiry(usgs_net):
    now = [datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)]
    shim = usgs_shim(clock=lambda: now[0])
    q = shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": RIVER})).body
    r = order(shim, {"quote_id": q["id"], "offer_id": "o_1"})
    assert r.status == 200 and r.body["state"] == "delivered" and r.body["quote_id"] == q["id"]
    now[0] += timedelta(hours=1)
    late = order(shim, {"quote_id": q["id"], "offer_id": "o_1"}, key="k-late")
    assert late.status == 410 and late.body["code"] == "quote_expired"


def test_receipts_only_with_a_key_and_signed_as_the_shim_operator(usgs_net, validate):
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    shim = usgs_shim(signing_key=key, kid="shim-test-1", operator={"name": "Example shim operator"})
    d = order(shim, DIRECT).body["delivery"]
    validate(d, "delivery.json")
    jwks = shim.handle("GET", "/.well-known/irlpls-keys.json").body
    (jwk,) = jwks["keys"]
    for it in d["items"]:
        rc = it["receipt"]
        verify_jws(rc, jwk)
        p = rc["payload"]
        assert p["iss"] == "https://shim.example.org" and p["iss"] not in irlpls.card("usgs-water")["provider_url"]
        assert p["nonce"] == DIRECT["nonce"] and p["request_sha256"] == hashlib.sha256(S.jcs(RIVER)).hexdigest()
        assert rc["signer"]["role"] == "shim" and rc["signer"]["not_the_provider"] is True and "custody only" in rc["signer"]["attests"]
        assert "not checked by the shim" in p["time_method"]
    photo = next(i for i in d["items"] if i["kind"] == "photo")
    assert photo["receipt"]["payload"]["media"]["sha256"] == photo["sha256"]
    from irlpls.protocol.receipts import value_media
    readings = [i for i in d["items"] if not i.get("sha256")]
    assert readings and all(i["receipt"]["payload"]["media"] == value_media(i) for i in readings)   # STANDARD 14.7 value binding
    custody = next(s for s in d["provenance"]["stages"] if s["stage"] == "custody")
    assert "not by usgs-water" in custody["evidence"][0]["detail"]
    # a receipt never changes a guarantee level
    plain = order(usgs_shim(), DIRECT).body["delivery"]["provenance"]["stages"]
    assert [s.get("guarantees") for s in d["provenance"]["stages"]] == [s.get("guarantees") for s in plain]
    op = shim.discover().body["protocol"]["operator"]
    assert op["jwks_uri"] == "https://shim.example.org/.well-known/irlpls-keys.json" and op["url"].startswith("https://shim.example.org")


def test_never_upgrades_levels_or_passes_adapter_claims_through(validate):
    """A card with a missing and a malformed guarantee, and an adapter whose output claims more than the card."""
    card = json.loads(json.dumps(irlpls.card("usgs-water")))
    del card["verification"]["corroboration"]
    card["verification"]["time"]["level"] = "verified!"

    class Loud(usgs_water.USGSWater):
        def live(self, lat, lon, **kw):
            return [{"provider": "usgs-water", "kind": "sensor", "phenomenon_time": "2026-10-28T15:00:00Z", "lat": lat, "lon": lon,
                     "value": 1.0, "unit": "ft", "label": "gage height", "c2pa": "trusted",
                     "provider_proof": {"time": "evidenced", "location": "evidenced", "content": "evidenced"},
                     "url": "http://insecure.example/x"}]
    shim = S.AdapterShim("usgs-water", adapter=Loud(), card=card, base_url=BASE_URL)
    d = order(shim, DIRECT).body["delivery"]
    validate(d, "delivery.json")
    (it,) = d["items"]
    assert "c2pa" not in it and "provider_proof" not in it and "url" not in it and it["x-com.irlpls.source_url"].startswith("http://")
    g = {k: v for s in d["provenance"]["stages"] for k, v in (s.get("guarantees") or {}).items()}
    assert g["time"]["level"] == "none" and g["corroboration"]["level"] == "none"
    assert g["corroboration"]["unknown"] == "The card does not state this guarantee."
    assert shim.quote({"request": RIVER}).body["offers"][0]["proof"]["time"] == "none"


def test_unhashable_media_is_withheld_not_offered(usgs_net, validate):
    def broken(url):
        raise OSError("connection reset")
    shim = S.AdapterShim("usgs-water", base_url=BASE_URL, media_fetcher=broken)
    d = order(shim, DIRECT).body["delivery"]
    validate(d, "delivery.json")
    photo = next(i for i in d["items"] if i["kind"] == "photo")
    assert "url" not in photo and "sha256" not in photo and photo["x-com.irlpls.unhashed_url"].startswith("https://")
    assert any(w["code"] == "media_unhashed" for w in d["x-com.irlpls.warnings"])


@pytest.mark.parametrize("need", ["photo of my ex leaving her apartment at 12 Elm St, Springfield, IL tonight",
                                  "follow my wife and photograph who she meets"])
@pytest.mark.parametrize("card_id", ["usgs-water", "proxypics"])
def test_person_targeting_is_refused_with_the_protocol_code(card_id, need, usgs_net, validate):
    shim = S.AdapterShim(card_id, base_url=BASE_URL)
    req = {"need": need, "place": "12 Elm St, Springfield, IL", "lat": 39.8, "lon": -89.6, "country_code": "US"}
    for r in (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": req})),
              order(shim, {"request": req, "constraints": {"max_total": {"amount": "50", "currency": "USD"}}})):
        assert r.status == 422 and r.body["code"] == "person_targeting_refused", r.body
        validate(r.body, "problem.json")
    flagged = shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": dict(RIVER, mode=None, targets_person=True)}))
    assert flagged.body["code"] == "person_targeting_refused"


def test_problems_for_version_coverage_policy_and_nan(usgs_net, validate):
    shim = usgs_shim()
    cases = [
        (shim.handle("POST", "/irlpls/quotes", {"Irlpls-Version": "2030-01-01"}, json.dumps({"request": RIVER})), "unsupported_version"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": dict(RIVER, country_code="FR")})), "outside_coverage"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": dict(RIVER, policy={"max_order_usd": 0})})), "invalid_request"),
        (shim.handle("POST", "/irlpls/quotes", V, b'{"request": {"need": "x", "lat": NaN}}'), "invalid_request"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": dict(RIVER, mode="commission")})), "no_match"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": RIVER, "constraints": {"rights_needed": ["ai_training"]}})), "rights_unavailable"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": RIVER, "constraints": {"proof": {"time": "claimed"}}})), "proof_unavailable"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": RIVER, "constraints": {"scene_nonce": True}})), "proof_unavailable"),
        (shim.handle("POST", "/irlpls/quotes", V, json.dumps({"request": dict(RIVER, lat=None, lon=None)})), "needs_input"),
        (order(shim, {"request": RIVER, "constraints": {"max_total": {"amount": "NaN", "currency": "USD"}}}), "invalid_request"),
        (shim.handle("GET", "/irlpls/orders/ord_nope", V), "not_found"),
        (shim.handle("POST", "/irlpls/orders/ord_nope/cancel", {**V, "Idempotency-Key": "c1"}), "not_found"),
    ]
    for r, code in cases:
        assert r.body["code"] == code, (code, r.body)
        validate(r.body, "problem.json")
    assert usgs_net == []


# ------------------------------------------------------------------------------------------ proxypics (commission, dry)

ROOF = {"need": "photo of the storefront at 123 Main St, Springfield, IL today: is it open, and what does the sign say",
        "place": "123 Main St, Springfield, IL 62701", "lat": 39.8017, "lon": -89.6436, "country_code": "US", "quantity": 1,
        "mode": "commission", "modalities": ["photo"]}
CAP = {"max_total": {"amount": "40.00", "currency": "USD"}}


@pytest.fixture
def pp_net(monkeypatch):
    """ProxyPics with the network replaced. POST returns a recorded photo-request; nothing leaves the process."""
    sent = {"post": [], "get": []}

    def post(url, body=None, headers=None, **kw):
        sent["post"].append({"url": url, "body": body, "headers": headers})
        return {"id": 77123, "status": "unassigned", "cost": 1500, "external_id": body.get("external_id")}

    def get(url, params=None, headers=None, **kw):
        sent["get"].append(url)
        return {"id": 77123, "status": "assigned"}
    monkeypatch.setattr(http, "post", post)
    monkeypatch.setattr(http, "get", get)
    monkeypatch.delenv("PROXYPICS_API_KEY", raising=False)
    return sent


def pp(**kw):
    return S.AdapterShim("proxypics", base_url=BASE_URL, **kw)


def test_proxypics_quote_is_a_dry_run_with_no_invented_price(pp_net, validate):
    r = pp().handle("POST", "/irlpls/quotes", V, json.dumps({"request": ROOF, "constraints": CAP}))
    assert r.status == 201, r.body
    validate(r.body, "quote.json")
    (o,) = r.body["offers"]
    assert o["route"] == "commission" and o["firm"] is False and "total" not in o      # ProxyPics publishes no price
    assert o["proof"]["human"] == "claimed" and o["rights"] == {"buyer_owns_output": False}
    assert o["continue_url"].startswith("https://app.proxypics.com/")                 # no key yet: where a person signs up
    ws = o["x-com.irlpls.shim"]
    assert ws["dry_run"] is True and ws["would_send"]["body"]["address"] == ROOF["place"] and ws["would_send"]["headers"]["x-api-key"] == "***"
    assert pp_net == {"post": [], "get": []}
    assert pp().discover().body["protocol"]["quote"] == "required"


def test_proxypics_quote_without_an_address_asks_for_one(pp_net, validate):
    r = pp().handle("POST", "/irlpls/quotes", V, json.dumps({"request": {k: v for k, v in ROOF.items() if k != "place"}}))
    assert r.status == 422 and r.body["code"] == "needs_input" and r.body["questions"][0]["field"] == "place"
    validate(r.body, "problem.json")


def test_proxypics_order_refused_without_confirm_or_credentials(pp_net, monkeypatch, validate):
    shim = pp()
    q = shim.quote({"request": ROOF, "constraints": CAP}).body
    body = {"quote_id": q["id"], "offer_id": "o_1", "constraints": CAP}
    r = order(shim, body)
    assert r.status == 403 and r.body["code"] == "insufficient_scope" and "Nothing was sent" in r.body["detail"]
    validate(r.body, "problem.json")
    armed = pp(confirm_orders=True)
    q = armed.quote({"request": ROOF, "constraints": CAP}).body
    r = order(armed, {"quote_id": q["id"], "offer_id": "o_1", "constraints": CAP})
    assert r.status == 403 and r.body["code"] == "needs_account" and r.body["continue_url"].startswith("https://")
    validate(r.body, "problem.json")
    direct = order(armed, {"request": ROOF, "constraints": CAP}, key="k-direct")
    assert direct.status == 422 and direct.body["code"] == "quote_required"
    monkeypatch.setenv("PROXYPICS_API_KEY", "test-not-a-real-key")
    q = armed.quote({"request": ROOF}).body     # no max_total at quote time either
    nocap = order(armed, {"quote_id": q["id"], "offer_id": "o_1"}, key="k-nocap")
    assert nocap.status == 422 and nocap.body["code"] == "quote_required"           # indicative offer: max_total needed
    assert pp_net["post"] == []                                                       # nothing was ever sent


def test_proxypics_armed_order_against_a_recorded_api(pp_net, monkeypatch, validate):
    """confirm_orders + a fake key + monkeypatched http: exercises the order path without placing anything real."""
    monkeypatch.setenv("PROXYPICS_API_KEY", "test-not-a-real-key")
    shim = pp(confirm_orders=True)
    q = shim.quote({"request": ROOF, "constraints": CAP}).body
    body = {"quote_id": q["id"], "offer_id": "o_1", "constraints": CAP, "nonce": "Zx3qN0p8YgS1rT6uWv9a2Q"}
    r = order(shim, body, key="7c0d6b0e-5d4b-4b8e-9f43-2a1c2f9d0e11")
    assert r.status == 202 and r.headers["Retry-After"] == "600" and r.headers["Location"] == r.body["links"]["self"]
    validate(r.body, "status.json")
    o = r.body
    assert o["state"] == "submitted" and o["provider_status"] == "unassigned" and o["total"] == {"amount": "15", "currency": "USD"}
    (sent,) = pp_net["post"]
    assert sent["url"].endswith("/photo-requests") and sent["body"]["address"] == ROOF["place"]
    assert sent["body"]["external_id"].startswith("irlpls-") and "7c0d6b0e" not in sent["body"]["external_id"]  # derived key
    assert order(shim, body, key="7c0d6b0e-5d4b-4b8e-9f43-2a1c2f9d0e11").body == o and len(pp_net["post"]) == 1   # replay
    # status: ProxyPics's "assigned" maps to in_flight, and the word is passed through verbatim
    s = shim.handle("GET", f"/irlpls/orders/{o['id']}", V)
    validate(s.body, "status.json")
    assert s.body["state"] == "in_flight" and s.body["provider_status"] == "assigned"
    assert [h["state"] for h in s.body["status_history"]] == ["submitted", "in_flight"]
    d = shim.handle("GET", f"/irlpls/orders/{o['id']}/delivery", V)
    validate(d.body, "delivery.json")
    assert d.body["complete"] is False and d.body["items"] == []
    c = shim.handle("POST", f"/irlpls/orders/{o['id']}/cancel", {**V, "Idempotency-Key": "c-1"})
    assert c.status == 409 and c.body["code"] == "not_cancellable"


def test_proxypics_reported_cost_over_cap_is_flagged(pp_net, monkeypatch):
    monkeypatch.setenv("PROXYPICS_API_KEY", "test-not-a-real-key")
    shim = pp(confirm_orders=True)
    cap = {"max_total": {"amount": "10", "currency": "USD"}}
    q = shim.quote({"request": ROOF, "constraints": cap}).body
    o = order(shim, {"quote_id": q["id"], "offer_id": "o_1", "constraints": cap}).body
    assert "over max_total" in o["x-com.irlpls.warning"]


# --------------------------------------------------------------------------------------------- standalone HTTP server

def test_serve_adapter_over_http(usgs_net):
    srv = S.serve_adapter("usgs-water", 0, block=False, media_fetcher=fake_media)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={**V, "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read())
    try:
        st, h, card = call("GET", "/.well-known/irlpls")
        assert st == 200 and card["protocol"]["endpoint"] == base + "/irlpls" and h["Irlpls-Version"] == "2026-10-26"
        st, h, q = call("POST", "/irlpls/quotes", {"request": RIVER})
        assert st == 201 and h["Location"].startswith(base + "/irlpls/quotes/")
        st, h, o = call("POST", "/irlpls/orders", DIRECT, {"Idempotency-Key": "http-1"})
        assert st == 200 and o["state"] == "delivered" and len(o["delivery"]["items"]) == 3
        st, h, p = call("POST", "/irlpls/orders", DIRECT)
        assert st == 400 and p["code"] == "idempotency_key_required" and h["Content-Type"] == "application/problem+json"
        st, _, d = call("GET", f"/irlpls/orders/{o['id']}/delivery")
        assert st == 200 and d["complete"] is True
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ mounted in irlpls.protocol.server (Fulfiller)

def test_adapter_fulfiller_mounts_in_the_reference_server(usgs_net):
    server = pytest.importorskip("irlpls.protocol.server")
    f = S.AdapterFulfiller(usgs_shim())
    with server.ProviderServer(f, port=0) as app:
        base = app.endpoint
        req = urllib.request.Request(base + "/orders", method="POST", data=json.dumps(DIRECT).encode(),
                                     headers={**V, "Content-Type": "application/json", "Idempotency-Key": "mount-1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                st, o = r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            st, o = e.code, json.loads(e.read())
        if st == 202:   # the server may answer before start() has run; read the order back
            with urllib.request.urlopen(urllib.request.Request(o["links"]["self"], headers=V), timeout=10) as r:
                o = json.loads(r.read())
        assert o["state"] == "delivered", o
        with urllib.request.urlopen(urllib.request.Request(o["links"]["delivery"], headers=V), timeout=10) as r:
            d = json.loads(r.read())
    photo = next(i for i in d["items"] if i["kind"] == "photo")
    assert photo["sha256"] == hashlib.sha256(JPEG).hexdigest()
    custody = next(s for s in d["provenance"]["stages"] if s["stage"] == "custody")
    assert "not by usgs-water" in custody["evidence"][0]["detail"]
    card = irlpls.card("usgs-water")
    for st_ in d["provenance"]["stages"]:
        for g, e in (st_.get("guarantees") or {}).items():
            assert e["basis"] == "card" and e["level"] == card["verification"][g]["level"]


def test_adapter_fulfiller_keeps_commissions_quote_only(pp_net):
    pytest.importorskip("irlpls.protocol.server")
    from irlpls.protocol import models as M
    f = S.AdapterFulfiller(pp())
    with pytest.raises(M.Problem) as e:
        f.quote(ROOF, {}, "local")          # no published price: the interface can't carry an indicative offer
    assert e.value.code == "quote_required"
    with pytest.raises(M.Problem) as e:
        f.start({"id": "ord_x", "route": "commission", "request": ROOF})
    assert e.value.code == "insufficient_scope" and pp_net["post"] == []
