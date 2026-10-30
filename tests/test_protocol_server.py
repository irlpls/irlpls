"""Reference provider server (irlpls/protocol/server.py) over real HTTP on ephemeral ports.

Every operation (discover, quote, order, status, deliver, cancel), every provider-side problem code, Idempotency-Key
semantics (draft-08), detached-JWS receipts bound to the nonce and request hash, Standard Webhooks signatures over a
real HTTPS receiver, the file-backed store and the CLI. Every response body is validated against schema/protocol/*.

    uv run --with pytest --with jsonschema --with cryptography python -m pytest -q tests/test_protocol_server.py
"""
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("jsonschema")

from irlpls.protocol import models as M  # noqa: E402
from irlpls.protocol.receipts import (ReceiptError, b64u, jcs, request_sha256, verify_receipt,  # noqa: E402
                                      verify_webhook, webhook_order_url_ok)
from irlpls.protocol.server import ProviderServer, SimFulfiller  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://sim.irlpls.example"
ENDPOINT = ORIGIN + "/irlpls"
V = {"Irlpls-Version": "2026-10-26"}
FREE = {"amount": "0", "currency": "USD"}
PHOTO = {"need": "photo of the storefront at 123 Main St, Springfield, IL", "place": "123 Main St, Springfield, IL",
         "lat": 39.8017, "lon": -89.6436, "country_code": "US", "mode": "commission", "modalities": ["photo"]}
READING = {"need": "air temperature in Springfield, IL now", "lat": 39.8, "lon": -89.6, "country_code": "US",
           "mode": "live", "modalities": ["sensor"]}
NONCE = "Zx3qN0p8YgS1rT6uWv9a2Q"


# ------------------------------------------------------------------ helpers

class Client:
    def __init__(self, srv, token=None):
        self.srv, self.base, self.token = srv, f"http://127.0.0.1:{srv.port}", token

    def path(self, url):
        return url[len(ORIGIN):] if url.startswith(ORIGIN) else url

    def call(self, method, url, body=None, headers=None, raw_body=None, version=True):
        h = dict(V if version else {}, **(headers or {}))
        if self.token:
            h.setdefault("Authorization", f"Bearer {self.token}")
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + self.path(url), data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                status, hdrs, raw = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, hdrs, raw = e.code, e.headers, e.read()
        ctype = hdrs.get("Content-Type", "")
        out = json.loads(raw) if "json" in ctype else raw
        if status >= 400:
            assert ctype == "application/problem+json", ctype
            assert_valid(out, "problem.json")
            assert out["status"] == status
            assert out["type"].endswith("/problems/" + ("policy" if out["code"].startswith("policy:") else out["code"]))
        assert hdrs.get("Irlpls-Version") == "2026-10-26"
        return status, hdrs, out

    def quote(self, request, constraints=None, **kw):
        body = {"request": request}
        if constraints is not None:
            body["constraints"] = constraints
        return self.call("POST", "/irlpls/quotes", body, **kw)

    def order(self, body, key="auto", nonce="auto", **kw):
        headers = dict(kw.pop("headers", {}))
        if nonce == "auto" and isinstance(body, dict) and "nonce" not in body:   # every order carries a nonce (14.5)
            body = dict(body, nonce=NONCE)
        if key == "auto":
            key = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.call("POST", "/irlpls/orders", body, headers=headers, **kw)

    def cancel(self, oid, key="auto", body=None):
        headers = {}
        if key == "auto":
            key = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
        if key is not None:
            headers["Idempotency-Key"] = key
        return self.call("POST", f"/irlpls/orders/{oid}/cancel", body if body is not None else {}, headers=headers)

    def status(self, oid):
        return self.call("GET", f"/irlpls/orders/{oid}")

    def delivery(self, oid):
        return self.call("GET", f"/irlpls/orders/{oid}/delivery")

    def wait(self, oid, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            s, _, o = self.status(oid)
            assert s == 200
            if pred(o):
                return o
            time.sleep(0.05)
        raise AssertionError(f"timed out; last state {o['state']}")


def assert_valid(instance, target):
    errs = M.errors(instance, target)
    assert not errs, errs


def direct(request, max_total="20.00", **extra):
    return dict({"request": request, "constraints": {"max_total": {"amount": max_total, "currency": "USD"}}}, **extra)


@pytest.fixture
def make():
    servers = []

    def _make(fulfiller=None, token=None, **kw):
        kw.setdefault("endpoint", ENDPOINT)
        kw.setdefault("port", 0)
        kw.setdefault("tick", 0.05)
        srv = ProviderServer(fulfiller or SimFulfiller(seconds=1.0), **kw).start()
        servers.append(srv)
        return srv, Client(srv, token)
    yield _make
    for s in servers:
        s.stop()


# ------------------------------------------------------------------ discover

def test_discover_card_and_keys(make):
    srv, c = make()
    s, h, card = c.call("GET", "/.well-known/irlpls")
    assert s == 200 and "max-age" in h["Cache-Control"]
    assert_valid(card, "https://irlpls.com/2026-10-26/schemas/capture-card.json")
    assert_valid(card["protocol"], "common.json#/$defs/protocol_block")
    p = card["protocol"]
    assert p["endpoint"] == ENDPOINT and p["versions"] == ["2026-10-26"] and p["cancel"] and p["webhooks"]
    assert p["jwks_uri"] == ORIGIN + "/.well-known/irlpls-keys.json" and card["provider_url"] == ORIGIN
    s, _, jwks = c.call("GET", c.path(p["jwks_uri"]))
    algs = sorted(k["alg"] for k in jwks["keys"])
    assert s == 200 and algs == ["ES256", "EdDSA"]
    assert all("d" not in k for k in jwks["keys"])  # never the private part


# ------------------------------------------------------------------ quote

@pytest.mark.parametrize("accept,ok", [
    (None, True), ("application/json", True), ("application/problem+json", True), ("*/*", True), ("application/*", True),
    ("text/html, */*;q=0.1", True), ("application/json;q=0.5, text/html", True),
    ("text/html", False), ("image/*", False), ("application/xml", False), ("application/json;q=0", False),
    ("*/*;q=0", False), ("application/json;q=0, application/problem+json;q=0, */*", False),
    ("application/json;q=nan", False), ("text/*, application/*;q=0", False),
])
def test_accept_header_decides_406(make, accept, ok):
    from irlpls.protocol.server import accepts_json
    assert accepts_json(accept) is ok
    srv, c = make()
    h = {"Accept": accept} if accept is not None else {}
    for method, path, body in (("GET", "/.well-known/irlpls", None), ("POST", "/irlpls/quotes", {"request": READING})):
        s, hdr, out = c.call(method, path, body, headers=h)
        if ok:
            assert s in (200, 201), out
        else:   # call() checks the problem shape (problem+json, schema, type URI)
            assert s == 406 and out["code"] == "not_acceptable" and out["resolution"] == "unrecoverable", out


def test_406_comes_before_auth_and_routing(make):
    srv, c = make(api_keys={"tok": {"client": "a"}})
    anon = Client(srv)
    s, _, out = anon.call("POST", "/irlpls/orders", {"quote_id": "q", "offer_id": "o"})
    assert s == 401                                                       # without the Accept problem: auth
    s, _, out = anon.call("POST", "/irlpls/orders", {"quote_id": "q", "offer_id": "o"}, headers={"Accept": "text/html"})
    assert s == 406 and out["code"] == "not_acceptable"
    s, _, _ = anon.call("GET", "/irlpls/nowhere", headers={"Accept": "text/html"})
    assert s == 406


def test_quote_and_get_quote(make):
    srv, c = make()
    s, h, q = c.quote(PHOTO, {"max_total": {"amount": "40.00", "currency": "USD"}, "rights_needed": ["commercial_use"],
                              "proof": {"time": "claimed"}, "scene_nonce": True})
    assert s == 201, q
    assert_valid(q, "quote.json")
    assert h["Location"] == f"{ENDPOINT}/quotes/{q['id']}"
    assert q["request_sha256"] == hashlib.sha256(jcs(PHOTO)).hexdigest()
    (o,) = q["offers"]
    assert o["firm"] and o["total"] == {"amount": "12.00", "currency": "USD"} and o["order_url"] == ENDPOINT + "/orders"
    assert o["route"] == "commission" and o["scene_nonce"] is True
    s, _, again = c.call("GET", h["Location"])
    assert s == 200 and again == q
    s, _, p = c.call("GET", "/irlpls/quotes/q_nope")
    assert s == 404 and p["code"] == "not_found"


def test_quote_with_idempotency_key_replays(make):
    srv, c = make()
    s1, _, q1 = c.quote(PHOTO, headers={"Idempotency-Key": "quote-1"})
    s2, h2, q2 = c.quote(PHOTO, headers={"Idempotency-Key": "quote-1"})
    assert s1 == s2 == 201 and q1 == q2 and h2["Idempotent-Replayed"] == "true"
    assert len(srv.store.quotes) == 1


# ------------------------------------------------------------------ order, status, deliver, receipts

def test_live_reading_is_one_call_with_inline_delivery_and_receipt(make):
    srv, c = make()
    s, h, o = c.order(direct(READING, "0", nonce=NONCE))
    assert s == 200, o
    assert_valid(o, "status.json")
    assert o["state"] == "delivered" and o["total"] == {"amount": "0.00", "currency": "USD"} and o["route"] == "live"
    assert [e["state"] for e in o["status_history"]] == ["submitted", "delivered"]
    d = o["delivery"]
    assert d["complete"] and len(d["items"]) == 1 and d["items"][0]["unit"] == "Cel"
    _, _, jwks = c.call("GET", "/.well-known/irlpls-keys.json")
    item = d["items"][0]
    doc = jcs({"kind": item["kind"], "value": item["value"], "unit": item["unit"], "phenomenon_time": item["phenomenon_time"]})
    payload = verify_receipt(item["receipt"], jwks, iss=ORIGIN, nonce=NONCE, request_sha256=request_sha256(READING),
                             media_sha256=hashlib.sha256(doc).hexdigest(), order_id=o["id"], item_id=item["id"])
    assert payload["media"]["media_type"] == "application/json"


def test_commission_quote_order_status_deliver_with_verified_receipt(make):
    srv, c = make(SimFulfiller(seconds=0.8))
    _, _, q = c.quote(PHOTO, {"max_total": {"amount": "40.00", "currency": "USD"}, "scene_nonce": True})
    s, h, o = c.order({"quote_id": q["id"], "offer_id": "o_1", "nonce": NONCE, "client_reference": "ref-1",
                       "approval": {"by": "person", "at": M.iso()}})
    assert s == 202, o
    assert_valid(o, "status.json")
    assert h["Location"] == f"{ENDPOINT}/orders/{o['id']}" and int(h["Retry-After"]) >= 1
    assert o["state"] == "submitted" and o["quote_id"] == q["id"] and o["offer_id"] == "o_1"
    assert o["total"] == {"amount": "12.00", "currency": "USD"} and o["links"]["cancel"].endswith("/cancel")
    assert o["status_history"][0]["provider_status"] == "posted"
    assert len(o["id"]) > 16  # unguessable

    # while in flight, delivery answers complete:false
    s, _, d0 = c.delivery(o["id"])
    assert s == 200 and d0["complete"] is False
    assert_valid(d0, "delivery.json")

    done = c.wait(o["id"], lambda x: x["state"] == "delivered")
    assert_valid(done, "status.json")
    states = [e["state"] for e in done["status_history"]]
    assert states[0] == "submitted" and "in_flight" in states and states[-1] == "delivered"
    assert states.index("in_flight") < states.index("delivered")
    assert done["progress"] == {"ordered": 1, "delivered": 1} and "cancel" not in done["links"]
    assert done["provider_status"] == "approved"

    s, _, d = c.call("GET", done["links"]["delivery"])
    assert s == 200 and d["complete"] is True
    assert_valid(d, "delivery.json")
    stages = [st["stage"] for st in d["provenance"]["stages"]]
    assert stages == ["enrolment", "capture", "device_attestation", "signing", "timestamp", "custody", "appraisal", "delivery"]
    cap = d["provenance"]["stages"][1]["guarantees"]
    assert set(cap) == {"camera", "location"} and cap["location"] == {"level": "claimed", "basis": "card",
                                                                        "unknown": srv.f.card["verification"]["location"]["unknown"]}

    item = d["items"][0]
    s, mh, media = c.call("GET", item["url"])
    assert s == 200 and mh["Content-Type"] == "image/png" and media[:8] == b"\x89PNG\r\n\x1a\n"
    assert hashlib.sha256(media).hexdigest() == item["sha256"] and len(media) == item["bytes"]
    assert NONCE.encode() in media  # scene nonce embedded (tEXt)

    _, _, jwks = c.call("GET", "/.well-known/irlpls-keys.json")
    payload = verify_receipt(item["receipt"], jwks, iss=ORIGIN, nonce=NONCE, request_sha256=q["request_sha256"],
                             media_bytes=media, order_id=o["id"], item_id=item["id"])
    assert payload["media"] == {"sha256": item["sha256"], "media_type": "image/png", "bytes": item["bytes"]}
    hdr = json.loads(base64.urlsafe_b64decode(item["receipt"]["jws"].split(".")[0] + "=="))
    assert hdr == {"alg": "ES256", "kid": srv.receipt_key.kid, "typ": "irlpls-receipt+jws"} and "kid" not in item["receipt"]
    assert o["request_sha256"] == q["request_sha256"] == item["receipt"]["payload"]["request_sha256"]
    rkey = next(k for k in jwks["keys"] if k["kid"] == srv.receipt_key.kid)
    assert rkey["alg"] == "ES256" and rkey["irlpls_use"] == "receipt"
    assert verify_receipt(item["receipt"], jwks, item=item)["item_id"] == item["id"]
    for change in ({"lat": 51.5}, {"phenomenon_time": "2020-01-01T00:00:00Z"}, {"bytes": item["bytes"] + 1}):
        with pytest.raises(ReceiptError):   # the item as shown must be the item as signed
            verify_receipt(item["receipt"], jwks, item=dict(item, **change))

    # every binding is checked
    for kw in ({"nonce": "x" * 22}, {"request_sha256": "0" * 64}, {"iss": "https://evil.example"},
               {"media_bytes": media + b"!"}):
        with pytest.raises(ReceiptError):
            verify_receipt(item["receipt"], jwks, **kw)
    tampered = json.loads(json.dumps(item["receipt"]))
    tampered["payload"]["lat"] = 0.0
    with pytest.raises(ReceiptError, match="signature"):
        verify_receipt(tampered, jwks)
    none_alg = dict(item["receipt"], jws=b64u(jcs({"alg": "none", "kid": srv.receipt_key.kid})) + ".." + item["receipt"]["jws"].split("..")[1])
    with pytest.raises(ReceiptError, match="not allowed"):
        verify_receipt(none_alg, jwks)
    with pytest.raises(ReceiptError, match="no key"):
        verify_receipt(item["receipt"], {"keys": [k for k in jwks["keys"] if k["alg"] != "ES256"]})


def test_partial_delivery_grows_while_in_flight(make):
    srv, c = make(SimFulfiller(seconds=2.0))
    _, _, o = c.order(direct(dict(PHOTO, quantity=3), "36.00", nonce=NONCE))
    assert o["total"]["amount"] == "36.00" and o["progress"] == {"ordered": 3, "delivered": 0}
    part = c.wait(o["id"], lambda x: 0 < x["progress"]["delivered"] < 3)
    assert part["state"] == "in_flight"
    _, _, d = c.delivery(o["id"])
    assert d["complete"] is False and 0 < len(d["items"]) < 3
    assert_valid(d, "delivery.json")
    c.wait(o["id"], lambda x: x["state"] == "delivered")
    _, _, d = c.delivery(o["id"])
    assert d["complete"] and [i["id"] for i in d["items"]] == ["itm_1", "itm_2", "itm_3"]
    assert len({i["sha256"] for i in d["items"]}) == 3


def test_media_link_expires(make):
    srv, c = make(SimFulfiller(seconds=0.2), media_ttl=0.5)
    _, _, o = c.order(direct(PHOTO))
    c.wait(o["id"], lambda x: x["state"] == "delivered")
    _, _, d = c.delivery(o["id"])
    url = d["items"][0]["url"]
    time.sleep(1.1)
    s, _, p = c.call("GET", url)
    assert s == 404 and p["code"] == "not_found"


# ------------------------------------------------------------------ idempotency (draft-08)

def test_order_and_cancel_need_idempotency_key(make):
    srv, c = make()
    s, _, p = c.order(direct(PHOTO), key=None)
    assert s == 400 and p["code"] == "idempotency_key_required" and p["resolution"] == "recoverable"
    _, _, o = c.order(direct(PHOTO))
    s, _, p = c.cancel(o["id"], key=None)
    assert s == 400 and p["code"] == "idempotency_key_required"
    s, _, p = c.order(direct(PHOTO), key="has space")
    assert s == 400 and p["code"] == "invalid_request"
    assert not srv.store.idem.get(("local", "has space"))


def test_same_key_same_body_replays_one_order(make):
    srv, c = make()
    body = direct(PHOTO, nonce=NONCE)
    s1, h1, o1 = c.order(body, key="order-7f3a")
    s2, h2, o2 = c.order(json.loads(json.dumps(body, sort_keys=True)), key="order-7f3a")  # same JCS, other byte order
    assert s1 == s2 == 202 and o1 == o2 and h2["Idempotent-Replayed"] == "true" and "Idempotent-Replayed" not in h1
    assert h1["Location"] == h2["Location"]
    assert len(srv.store.orders) == 1


def test_same_key_different_body_is_422(make):
    srv, c = make()
    c.order(direct(PHOTO), key="k-1")
    s, _, p = c.order(direct(PHOTO, "25.00"), key="k-1")
    assert s == 422 and p["code"] == "idempotency_key_reused" and p["resolution"] == "unrecoverable"
    _, _, o = c.order(direct(PHOTO))
    s, _, p = c.cancel(o["id"], key="k-1")  # the same key on another operation is a different request
    assert s == 422 and p["code"] == "idempotency_key_reused"
    assert len(srv.store.orders) == 2


def test_same_key_while_first_runs_is_409(make):
    srv, c = make(SimFulfiller(seconds=5, accept_delay=1.0))
    body, out = direct(PHOTO), {}

    def first():
        out["first"] = c.order(body, key="slow-1")
    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.3)
    s, h, p = c.order(body, key="slow-1")
    assert s == 409 and p["code"] == "request_in_progress" and h["Retry-After"] == "1" and p["resolution"] == "retry_later"
    t.join()
    assert out["first"][0] == 202
    s, h, again = c.order(body, key="slow-1")
    assert s == 202 and again["id"] == out["first"][2]["id"] and h["Idempotent-Replayed"] == "true"
    assert len(srv.store.orders) == 1


def test_keys_are_scoped_per_client(make):
    keys = {"tok-a": {"client": "a"}, "tok-b": {"client": "b"}}
    srv, a = make(api_keys=keys, token="tok-a")
    b = Client(srv, "tok-b")
    _, _, oa = a.order(direct(PHOTO), key="shared")
    _, _, ob = b.order(direct(PHOTO, "30.00"), key="shared")
    assert oa["id"] != ob["id"]
    s, _, p = b.status(oa["id"])  # and orders are private to their client
    assert s == 404 and p["code"] == "not_found"


def test_business_problem_is_replayed_but_auth_problem_is_not(make):
    keys = {"tok": {"client": "a", "account": "no_payment"}}
    srv, c = make(api_keys=keys, token="tok")
    s, _, p = c.order(direct(PHOTO, "1.00"), key="bk-1")
    assert s == 422 and p["code"] == "over_max_total"
    s, h, p2 = c.order(direct(PHOTO, "1.00"), key="bk-1")
    assert s == 422 and p2 == p and h["Idempotent-Replayed"] == "true"
    s, _, p = c.order(direct(PHOTO), key="pay-1")
    assert s == 402 and p["code"] == "payment_required" and p["continue_url"].startswith("https://")
    srv.api_keys["tok"]["account"] = "ok"  # the person adds billing; the same key now works
    s, _, o = c.order(direct(PHOTO), key="pay-1")
    assert s == 202 and o["state"] == "submitted"


# ------------------------------------------------------------------ cancel

def test_cooperative_cancel(make):
    srv, c = make(SimFulfiller(seconds=5))
    _, _, o = c.order(direct(PHOTO))
    s, _, after = c.cancel(o["id"], body={"reason": "plans changed"})
    assert s == 200
    assert_valid(after, "status.json")
    assert after["state"] in ("in_flight", "submitted", "failed")
    done = c.wait(o["id"], lambda x: x["state"] == "failed")
    assert done["reason_code"] == "cancelled" and done["reason_text"]
    assert any(e.get("provider_status") == "cancel_requested" for e in done["status_history"])
    assert done["status_history"][-1]["reason_code"] == "cancelled"
    s, _, d = c.delivery(o["id"])
    assert s == 200 and d["complete"] is False and d["items"] == []
    # cancelling a terminal order returns it unchanged
    s, _, again = c.cancel(o["id"])
    assert s == 200 and again == done
    # terminal orders never change
    time.sleep(0.2)
    assert c.status(o["id"])[2] == done


def test_cancel_too_late_is_409_and_order_still_delivers(make):
    srv, c = make(SimFulfiller(seconds=0.3, finalize_seconds=2.0))
    _, _, o = c.order(direct(PHOTO))
    c.wait(o["id"], lambda x: x["progress"]["delivered"] == 1)
    s, _, p = c.cancel(o["id"])
    assert s == 409 and p["code"] == "not_cancellable" and p["order_id"] == o["id"]
    assert c.wait(o["id"], lambda x: x["state"] == "delivered")["state"] == "delivered"


def test_cancel_disabled_provider_is_409(make):
    srv, c = make(cancel=False)
    _, _, card = c.call("GET", "/.well-known/irlpls")
    assert card["protocol"]["cancel"] is False
    _, _, o = c.order(direct(PHOTO))
    assert "cancel" not in o["links"]
    s, _, p = c.cancel(o["id"])
    assert s == 409 and p["code"] == "not_cancellable"


def test_cancel_unknown_order_and_bad_body(make):
    srv, c = make()
    s, _, p = c.cancel("ord_missing")
    assert s == 404 and p["code"] == "not_found"
    _, _, o = c.order(direct(PHOTO))
    s, _, p = c.cancel(o["id"], body={"refund_to": "someone else"})
    assert s == 400 and p["code"] == "invalid_request"


# ------------------------------------------------------------------ problems

@pytest.mark.parametrize("request_, constraints, code", [
    (dict(PHOTO, modalities=["aerial"]), None, "no_match"),
    (dict(PHOTO, country_code="FR"), None, "outside_coverage"),
    ({"need": "photo of the bakery", "mode": "commission"}, None, "needs_input"),
    (dict(PHOTO, within_minutes=0), None, "too_slow"),
    (PHOTO, {"deliver_by": "2020-01-01T00:00:00Z"}, "too_slow"),
    (PHOTO, {"max_total": {"amount": "5.00", "currency": "USD"}}, "over_max_total"),
    (PHOTO, {"max_total": {"amount": "50.00", "currency": "EUR"}}, "over_max_total"),
    (PHOTO, {"rights_needed": ["resale"]}, "rights_unavailable"),
    (PHOTO, {"proof": {"time": "evidenced"}}, "proof_unavailable"),
    (dict(PHOTO, targets_person=True), None, "person_targeting_refused"),
    (dict(PHOTO, need=PHOTO["need"] + " [sim:terms]"), None, "terms"),
    (dict(PHOTO, need=PHOTO["need"] + " [sim:provider_unavailable]"), None, "provider_unavailable"),
])
def test_quote_problems(make, request_, constraints, code):
    srv, c = make(SimFulfiller(seconds=2.0))
    s, h, p = c.quote(request_, constraints)
    assert p["code"] == code, p
    assert s == M.PROBLEMS[code][0] and p["resolution"] == M.PROBLEMS[code][2] and p["instance"] == "/irlpls/quotes"
    if code == "needs_input":
        assert p["questions"][0]["field"] == "place" and p["questions"][0]["blocking"] is True
    if code == "provider_unavailable":
        assert h["Retry-After"]


def test_direct_order_runs_the_same_checks(make):
    srv, c = make()
    s, _, p = c.order(direct(dict(PHOTO, country_code="FR")))
    assert s == 422 and p["code"] == "outside_coverage"
    s, _, p = c.order(direct(PHOTO, "11.99"))
    assert s == 422 and p["code"] == "over_max_total"
    assert not srv.store.orders


@pytest.mark.parametrize("body, path", [
    ({"request": {"lat": 1}}, "$.request"),                                                  # need missing
    ({"request": dict(PHOTO, policy={"max_order_usd": 5})}, "$.request"),                     # policy never sent
    ({"request": PHOTO, "constraints": {"max_total": {"amount": 12, "currency": "USD"}}}, "$.constraints.max_total.amount"),
    ({"request": dict(PHOTO, lat=200)}, "$.request"),
])
def test_invalid_quote_bodies(make, body, path):
    srv, c = make()
    s, _, p = c.call("POST", "/irlpls/quotes", body)
    assert s == 400 and p["code"] == "invalid_request"
    assert any(e["path"].startswith(path) for e in p["errors"]), p["errors"]


def test_invalid_order_bodies(make):
    srv, c = make()
    s, _, p = c.order({"quote_id": "q_1", "offer_id": "o_1", "request": PHOTO})  # both forms
    assert s == 400 and p["code"] == "invalid_request"
    s, _, p = c.order({"request": PHOTO})  # direct without max_total
    assert s == 400 and p["code"] == "invalid_request"
    s, _, p = c.order(direct(PHOTO, nonce="short"))
    assert s == 400 and any(e["path"] == "$.nonce" for e in p["errors"])
    s, _, p = c.call("POST", "/irlpls/orders", raw_body=b'{"request": NaN}', headers={"Idempotency-Key": "nan"})
    assert s == 400 and p["code"] == "invalid_request"
    s, _, p = c.call("POST", "/irlpls/orders", raw_body=b"[1]", headers={"Idempotency-Key": "arr"})
    assert s == 400 and p["code"] == "invalid_request"
    s, _, p = c.order(direct(PHOTO, webhook={"url": "http://hooks.example/x"}))
    assert s == 400 and p["errors"]
    s, _, p = c.order(direct(PHOTO, webhook={"url": "https://127.0.0.1/hook"}))  # SSRF: private address refused
    assert s == 400 and p["errors"][0]["path"] == "$.webhook.url"
    assert not srv.store.orders


def test_unsupported_version(make):
    srv, c = make()
    s, _, p = c.call("GET", "/irlpls/orders/x", headers={"Irlpls-Version": "1999-01-01"}, version=False)
    assert s == 400 and p["code"] == "unsupported_version" and p["supported_versions"] == ["2026-10-26"]
    s, _, p = c.call("GET", "/irlpls/orders/x", version=False)  # absent: the server's version is assumed
    assert s == 404


def test_auth_scope_account(make):
    keys = {"full": {"client": "a"}, "readonly": {"client": "b", "scopes": ["com.irlpls.capture:read"]},
            "new": {"client": "c", "account": "needs_account"}}
    srv, c = make(api_keys=keys, signup_url="https://sim.irlpls.example/signup")
    _, _, card = c.call("GET", "/.well-known/irlpls")  # discover needs no credentials
    assert card["api"]["auth"][0]["type"] == "api_key"
    s, h, p = c.quote(PHOTO)
    assert s == 401 and p["code"] == "unauthorized" and h["WWW-Authenticate"].startswith("Bearer")
    s, h, p = Client(srv, "wrong").quote(PHOTO)
    assert s == 401 and "invalid_token" in h["WWW-Authenticate"]
    s, h, p = Client(srv, "readonly").quote(PHOTO)
    assert s == 403 and p["code"] == "insufficient_scope" and "insufficient_scope" in h["WWW-Authenticate"]
    s, _, p = Client(srv, "new").quote(PHOTO)
    assert s == 403 and p["code"] == "needs_account" and p["continue_url"] == "https://sim.irlpls.example/signup"
    s, _, q = Client(srv, "full").quote(PHOTO)
    assert s == 201
    s, _, p = Client(srv, "readonly").call("GET", f"/irlpls/quotes/{q['id']}")
    assert s == 403  # quotes are read with the quote scope
    s, _, p = Client(srv, "new").order(direct(PHOTO))
    assert s == 403 and p["code"] == "needs_account"


def test_public_quotes(make):
    srv, c = make(api_keys={"full": {"client": "a"}}, public_quotes=True)
    s, _, q = c.quote(PHOTO)
    assert s == 201
    s, _, p = c.order({"quote_id": q["id"], "offer_id": "o_1"})
    assert s == 401  # orders are always authenticated
    s, _, o = Client(srv, "full").order({"quote_id": q["id"], "offer_id": "o_1"})
    assert s == 202


def test_quote_expired_required_and_not_found(make):
    srv, c = make(quote_ttl=0.3, quote_mode="required")
    s, _, p = c.order(direct(PHOTO))
    assert s == 422 and p["code"] == "quote_required"
    s, _, o = c.order(direct(READING, "0"))  # free live readings never need a quote
    assert s == 200
    _, _, q = c.quote(PHOTO)
    s, _, p = c.order({"quote_id": q["id"], "offer_id": "o_9"})
    assert s == 404 and p["code"] == "not_found"
    s, _, p = c.order({"quote_id": "q_unknown", "offer_id": "o_1"})
    assert s == 404 and p["quote_id"] == "q_unknown"
    time.sleep(0.5)
    s, _, p = c.order({"quote_id": q["id"], "offer_id": "o_1"})
    assert s == 410 and p["code"] == "quote_expired" and p["quote_id"] == q["id"] and p["instance"] == "/irlpls/orders"
    s, _, p = c.status("ord_nope")
    assert s == 404 and p["order_id"] == "ord_nope"
    s, _, p = c.delivery("ord_nope")
    assert s == 404


def test_rate_limit_and_unavailable(make):
    srv, c = make(rate_limit_per_min=3)
    for _ in range(3):
        assert c.status("ord_x")[0] == 404
    s, h, p = c.status("ord_x")
    assert s == 429 and p["code"] == "rate_limited" and int(h["Retry-After"]) >= 1
    srv2, c2 = make(unavailable=True)
    s, h, p = c2.quote(PHOTO)
    assert s == 503 and p["code"] == "provider_unavailable" and h["Retry-After"] == "30"
    assert c2.call("GET", "/.well-known/irlpls")[0] == 200  # discovery stays up


def test_routing_problems(make):
    srv, c = make()
    s, h, p = c.call("DELETE", "/irlpls/orders/abc")
    assert s == 405 and h["Allow"] == "GET"
    s, _, p = c.call("GET", "/irlpls/nothing")
    assert s == 404 and p["code"] == "not_found"
    s, _, p = c.call("POST", "/irlpls/quotes", raw_body=b"x" * (300 * 1024))
    assert s == 413


# ------------------------------------------------------------------ webhooks (Standard Webhooks, Ed25519)

def _self_signed(tmp):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp / "cert.pem", tmp / "key.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp


@pytest.fixture
def receiver(tmp_path):
    """An HTTPS webhook receiver on 127.0.0.1 with a self-signed cert. The first `fail_first` posts get 500."""
    cp, kp = _self_signed(tmp_path)
    got, cfg = [], {"fail_first": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append(({k.lower(): v for k, v in self.headers.items()}, body))
            code = 500 if cfg["fail_first"] > 0 else 204
            cfg["fail_first"] -= 1
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cp, kp)
    httpd.socket = sctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cctx = ssl.create_default_context(cafile=str(cp))
    yield {"url": f"https://127.0.0.1:{httpd.server_address[1]}/hooks/irlpls", "got": got, "ctx": cctx, "cfg": cfg}
    httpd.shutdown()


def _wait_for(pred, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_webhooks_are_signed_thin_events(make, receiver):
    srv, c = make(SimFulfiller(seconds=0.6), allow_private_webhooks=True, webhook_ssl_context=receiver["ctx"])
    _, _, jwks = c.call("GET", "/.well-known/irlpls-keys.json")
    _, _, o = c.order(direct(PHOTO, webhook={"url": receiver["url"]}))
    assert _wait_for(lambda: any(json.loads(b)["data"]["state"] == "delivered" for _, b in receiver["got"]))
    states = []
    for headers, body in receiver["got"]:
        ev = verify_webhook(headers, body, jwks)
        assert_valid(ev, "status.json#/$defs/webhook_event")
        assert ev["type"] == "order.updated" and ev["data"]["order_id"] == o["id"]
        assert webhook_order_url_ok(ev, ENDPOINT)   # exactly {endpoint}/orders/{order_id}, never a prefix match
        assert headers["irlpls-webhook-kid"] == srv.webhook_key.kid
        assert headers["webhook-signature"].startswith("v1a,")
        states.append(ev["data"]["state"])
        # the receiver re-reads the order before acting
        assert c.call("GET", ev["data"]["order_url"])[0] == 200
    assert set(states) == {"in_flight", "delivered"}
    headers, body = receiver["got"][-1]
    with pytest.raises(ReceiptError):
        verify_webhook(headers, body.replace(b"delivered", b"failed"), jwks)
    with pytest.raises(ReceiptError, match="tolerance"):
        verify_webhook(headers, body, jwks, now=int(headers["webhook-timestamp"]) + 301)
    with pytest.raises(ReceiptError):
        verify_webhook(headers, body, {"keys": [k for k in jwks["keys"] if k["kty"] != "OKP"]})
    with pytest.raises(ReceiptError, match="kid"):
        verify_webhook({k: v for k, v in headers.items() if k != "irlpls-webhook-kid"}, body, jwks)
    seen = {}
    verify_webhook(headers, body, jwks, seen=seen)
    with pytest.raises(ReceiptError, match="replay"):   # the same webhook-id inside the window is refused
        verify_webhook(headers, body, jwks, seen=seen)
    ev = json.loads(body)
    for bad in (ENDPOINT + "-evil/orders/" + ev["data"]["order_id"], ev["data"]["order_url"] + "/x",
                "https://evil.example/irlpls/orders/" + ev["data"]["order_id"]):
        assert not webhook_order_url_ok({"data": dict(ev["data"], order_url=bad)}, ENDPOINT), bad


def test_orders_found_again_by_client_reference(make):
    srv, c = make()
    _, _, a = c.order(direct(PHOTO, client_reference="job-42"))
    _, _, b = c.order(direct(PHOTO, client_reference="job-42"))
    c.order(direct(PHOTO, client_reference="other"))
    s, _, out = c.call("GET", "/irlpls/orders?client_reference=job-42")
    assert s == 200 and {o["id"] for o in out["orders"]} == {a["id"], b["id"]}
    assert_valid(out, "status.json#/$defs/order_list")
    s, _, p = c.call("GET", "/irlpls/orders")
    assert s == 400 and p["code"] == "invalid_request"


def test_bodies_need_a_strict_content_length(make):
    import socket
    srv, _ = make()
    for head, want in ((b"Transfer-Encoding: chunked\r\n", b"411"), (b"Content-Length: 12abc\r\n", b"400")):
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sk:
            sk.sendall(b"POST /irlpls/quotes HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n" + head + b"\r\n{}")
            sk.shutdown(socket.SHUT_WR)
            out = sk.recv(4096)
        assert out.split(b" ")[1] == want, out[:200]


@pytest.mark.parametrize("mt,inline", [("image/png", True), ("image/svg+xml", False), ("text/html", False),
                                       ("application/json", False)])
def test_media_headers_never_render_active_content(mt, inline):
    from irlpls.protocol.server import media_headers
    h = media_headers(mt)
    assert h["X-Content-Type-Options"] == "nosniff" and "sandbox" in h["Content-Security-Policy"]
    assert ("Content-Disposition" not in h) is inline


def test_webhook_retries_keep_the_same_id(make, receiver):
    receiver["cfg"]["fail_first"] = 1
    srv, c = make(SimFulfiller(seconds=0.2), allow_private_webhooks=True, webhook_ssl_context=receiver["ctx"],
                  webhook_retry_delays=(0.2, 0.2))
    _, _, o = c.order(direct(READING, "0", webhook={"url": receiver["url"]}))
    assert o["state"] == "delivered"
    assert _wait_for(lambda: len(receiver["got"]) >= 2)
    (h1, b1), (h2, b2) = receiver["got"][:2]
    assert h1["webhook-id"] == h2["webhook-id"] and b1 == b2
    assert [e["status"] for e in srv.webhook_log if e["id"] == h1["webhook-id"]] == [500, 204]


# ------------------------------------------------------------------ file store, CLI, JCS

def test_file_store_survives_restart(make, tmp_path):
    srv, c = make(SimFulfiller(seconds=0.3), data_dir=tmp_path)
    _, _, o = c.order(direct(PHOTO, nonce=NONCE), key="persist-1")
    c.wait(o["id"], lambda x: x["state"] == "delivered")
    _, _, d = c.delivery(o["id"])
    srv.stop()
    assert (tmp_path / "state.json").exists() and oct((tmp_path / "keys.json").stat().st_mode)[-3:] == "600"
    srv2, c2 = make(SimFulfiller(seconds=0.3), data_dir=tmp_path)
    s, _, again = c2.delivery(o["id"])
    assert s == 200 and again == d
    s, h, replay = c2.order(direct(PHOTO, nonce=NONCE), key="persist-1")
    assert replay["id"] == o["id"] and h["Idempotent-Replayed"] == "true"
    _, _, jwks = c2.call("GET", "/.well-known/irlpls-keys.json")  # same key after restart
    _, _, media = c2.call("GET", d["items"][0]["url"])
    verify_receipt(d["items"][0]["receipt"], jwks, nonce=NONCE, media_bytes=media)


def test_in_flight_order_resumes_after_restart(make, tmp_path):
    srv, c = make(SimFulfiller(seconds=0.8), data_dir=tmp_path)
    _, _, o = c.order(direct(PHOTO))
    srv.stop()
    srv2, c2 = make(SimFulfiller(seconds=0.8), data_dir=tmp_path)
    assert c2.wait(o["id"], lambda x: x["state"] == "delivered")["progress"]["delivered"] == 1


def test_cli_localhost_only(tmp_path):
    p = subprocess.Popen([sys.executable, "-m", "irlpls.protocol.server", "--port", "0", "--sim", "--sim-seconds", "1"],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = p.stdout.readline()
        assert "http://127.0.0.1:" in line, line + p.stderr.read()
        port = int(line.split("http://127.0.0.1:")[1].split()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/.well-known/irlpls", timeout=5) as r:
            card = json.loads(r.read())
        assert card["id"] == "irlpls-sim" and card["protocol"]["endpoint"] == f"http://127.0.0.1:{port}/irlpls"
    finally:
        p.terminate()
        p.wait(5)
    r = subprocess.run([sys.executable, "-m", "irlpls.protocol.server", "--host", "0.0.0.0", "--sim"],
                       cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "--allow-remote" in r.stderr


def test_jcs_rfc8785_vectors():
    assert jcs({"b": 1, "a": [True, None, "é\n"], "€": 0.5, "\u0080": 1}) == \
        '{"a":[true,null,"é\\n"],"b":1,"\u0080":1,"€":0.5}'.encode()
    nums = [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001, 1e21, 1e-7, -0.0, 100.0]
    assert jcs(nums) == b"[333333333.3333333,1e+30,4.5,0.002,1e-27,1e+21,1e-7,0,100]"
    with pytest.raises(ValueError):
        jcs(float("nan"))


# ------------------------------------------------------------------ indicative offers, mounted adapter shim

class _Indicative(SimFulfiller):
    """A provider that publishes no price: its offers are indicative and carry no total."""

    def quote(self, request, constraints, client):
        from irlpls.protocol.server import Terms
        t = super().quote(request, constraints, client)
        return Terms(route=t.route, total=None, firm=False, quantity=t.quantity, rights=t.rights, proof=t.proof)


def test_indicative_offer_without_a_price(make):
    srv, c = make(_Indicative())
    s, _, q = c.quote(PHOTO, {"max_total": {"amount": "40.00", "currency": "USD"}})
    assert s == 201
    assert_valid(q, "quote.json")
    (o,) = q["offers"]
    assert o["firm"] is False and "total" not in o and "unit_price" not in o
    s, _, p = c.order({"quote_id": q["id"], "offer_id": "o_1"})
    assert s == 422 and p["code"] == "quote_required" and p["quote_id"] == q["id"]
    s, _, p = c.order(direct(PHOTO))
    assert s == 422 and p["code"] == "quote_required"
    assert not srv.store.orders


RIVER = {"need": "river level at Comfort, TX now", "lat": 29.9683, "lon": -98.9054, "country_code": "US",
         "mode": "live", "modalities": ["sensor"], "require": ["time", "location", "content"]}
JPEG = b"\xff\xd8\xff\xe0 recorded river frame \xff\xd9"
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
def usgs_recorded(monkeypatch):
    """Recorded USGS responses (Comfort, TX): discharge, gage height, daily statistics, one river camera. No network."""
    from irlpls import http
    from irlpls.adapters import usgs_water
    calls = []
    t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).replace(microsecond=0, second=0)

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
            return [{"camId": "TX_Guadalupe_Comfort", "lat": "29.9700", "lng": "-98.9046", "nwisId": "08167000",
                     "camName": "Guadalupe at Comfort", "hideCam": False,
                     "smallDir": "https://usgs-nims-images.s3.amazonaws.com/overlay/TX_Guadalupe_Comfort/"}]
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


def test_mounted_usgs_shim_keeps_the_providers_identity(make, usgs_recorded):
    import irlpls
    from irlpls.protocol.shim import AdapterFulfiller, AdapterShim
    shim_origin = "https://shim.example.org"

    def fake_media(url):
        assert url.startswith("https://usgs-nims-images.s3.amazonaws.com/")
        return JPEG, "image/jpeg"
    shim = AdapterShim("usgs-water", base_url=shim_origin + "/irlpls", media_fetcher=fake_media,
                       operator={"name": "Example shim operator", "url": shim_origin})
    srv, c = make(AdapterFulfiller(shim), endpoint=shim_origin + "/irlpls", provider_url=shim_origin)
    usgs = irlpls.card("usgs-water")

    # discover: the provider's card, unclaimed, with the shim operator disclosed and no provider jwks_uri
    _, _, card = c.call("GET", "/.well-known/irlpls")
    assert card["provider_url"] == usgs["provider_url"] and card["status"] == "unclaimed" and card["id"] == "usgs-water"
    assert card["verification"] == usgs["verification"]
    p = card["protocol"]
    assert_valid(p, "common.json#/$defs/protocol_block")
    assert p["operator"] == {"name": "Example shim operator", "url": shim_origin, "role": "shim",
                             "jwks_uri": shim_origin + "/.well-known/irlpls-keys.json"}
    assert "jwks_uri" not in p
    assert p["cancel"] is False and p["endpoint"] == shim_origin + "/irlpls"

    # quote: indicative, free, the card's levels; calls nothing upstream
    s, _, q = c.quote(RIVER)
    assert s == 201, q
    assert_valid(q, "quote.json")
    (o,) = q["offers"]
    assert o["firm"] is False and o["route"] == "live" and o["provider"]["card_id"] == "usgs-water"
    assert o["proof"] == {g: usgs["verification"][g]["level"] for g in M.GUARANTEES}
    assert usgs_recorded == []

    # order from the (free) quote: delivered at once, receipts signed by the operator, operator in relayed_by
    s, _, order = c.order({"quote_id": q["id"], "offer_id": "o_1", "nonce": NONCE})
    assert s == 200, order
    assert_valid(order, "status.json")
    d = order["delivery"]
    assert_valid(d, "delivery.json")
    assert d["relayed_by"] == [{"name": "Example shim operator", "url": shim_origin, "role": "shim"}]
    values = {i.get("unit"): i.get("value") for i in d["items"] if "value" in i}
    assert values == {"ft3/s": 74.2, "ft": 2.31}
    photo = next(i for i in d["items"] if i.get("media_type") == "image/jpeg")
    assert photo["url"].startswith(shim_origin + "/irlpls/media/")
    _, _, media = c.call("GET", photo["url"][len(shim_origin):])
    assert media == JPEG and photo["sha256"] == hashlib.sha256(JPEG).hexdigest()
    for st in d["provenance"]["stages"]:
        for g, e in (st.get("guarantees") or {}).items():
            assert e["basis"] == "card" and e["level"] == usgs["verification"][g]["level"]
    _, _, jwks = c.call("GET", "/.well-known/irlpls-keys.json")
    pl = verify_receipt(photo["receipt"], jwks, iss=shim_origin, nonce=NONCE, request_sha256=q["request_sha256"],
                        media_bytes=media)
    assert pl["iss"] != usgs["provider_url"]
    s, _, pr = c.cancel(order["id"])
    assert s == 200 and pr["state"] == "delivered"  # terminal: unchanged
