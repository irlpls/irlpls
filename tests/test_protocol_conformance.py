"""Protocol conformance checks (irlpls.conformance --protocol, STANDARD Section 14) against an in-test fake provider.

The fake speaks the protocol in memory; `faults` switch on one misbehaviour each so every check is shown to bite.
"""
import hashlib
import json
import pathlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import pytest

from irlpls import conformance as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = date.today()
HOST = "streetlens.example"
EP = f"https://api.{HOST}/irlpls"
JWKS_URL = f"https://{HOST}/.well-known/irlpls-keys.json"
MEDIA_URL = f"https://media.{HOST}/o/itm_1.jpg"
MEDIA = b"\xff\xd8 fake jpeg bytes for the conformance test \xff\xd9"
V = C.PROTOCOL_VERSION
STAGE_KEYS = {"enrolment": ["human"], "capture": ["camera", "location"], "device_attestation": ["application"],
              "signing": ["camera", "location", "content"], "timestamp": ["time"], "custody": [],
              "appraisal": ["corroboration"], "delivery": []}

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa: E402


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_card(**proto):
    card = json.loads((ROOT / "cards" / "skyfi.json").read_text())
    card.update(id="streetlens", name="Streetlens", status="provider-maintained", provider_url=f"https://{HOST}",
                updated=TODAY.isoformat(), expires=(TODAY + timedelta(days=300)).isoformat())
    card["protocol"] = {"versions": [V], "endpoint": EP, "jwks_uri": JWKS_URL, "quote": "required",
                        "public_quotes": True, "webhooks": False, "cancel": True, **proto}
    return card


def problem(code, status, **extra):
    t = "policy" if code.startswith("policy:") else code
    return status, {"content-type": "application/problem+json"}, {
        "type": f"https://irlpls.com/{V}/problems/{t}", "title": code.replace("_", " "), "status": status,
        "code": code, "resolution": "retry_later" if status >= 500 else "unrecoverable", **extra}


class FakeProvider:
    """An in-memory Orderable provider. Call it as a conformance transport."""

    def __init__(self, faults=(), card=None, sandbox=True):
        self.faults = set(faults)
        self.card = card or make_card(sandbox=sandbox)
        self.calls, self.quotes, self.orders, self.idem = [], {}, {}, {}
        self.key = ec.generate_private_key(ec.SECP256R1())
        pub = self.key.public_key().public_numbers()
        self.jwks = {"keys": [{"kty": "EC", "crv": "P-256", "kid": "k1", "alg": "ES256", "use": "sig", "irlpls_use": "receipt",
                               "x": C.b64u(pub.x.to_bytes(32, "big")), "y": C.b64u(pub.y.to_bytes(32, "big"))}]}

    # -- plumbing
    def __call__(self, method, url, headers=None, body=None):
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.calls.append((method, url, headers))
        status, h, obj = self.route(method, url, headers, json.loads(body) if body else None)
        if isinstance(obj, (bytes, bytearray)):
            return status, h, bytes(obj)
        h = dict(h)
        if "plain_errors" in self.faults and status >= 400:
            h["content-type"] = "application/json"
        if status < 400 and url.startswith(EP):
            h.setdefault("irlpls-version", V)
        return status, h, json.dumps(obj).encode()

    def ok(self, obj, status=200, **h):
        return status, {"content-type": "application/json", **h}, obj

    def posts(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]

    def order_posts(self):
        return [c for c in self.calls if c[0] == "POST" and (c[1] == EP + "/orders" or c[1].endswith("/cancel"))]

    # -- routes
    def route(self, method, url, headers, body):
        if url == f"https://{HOST}/.well-known/irlpls":
            return 200, {"content-type": "application/json", "cache-control": "public, max-age=300", "etag": '"1"'}, self.card
        if url == JWKS_URL:
            return self.ok(self.jwks)
        if url == MEDIA_URL:
            return 200, {"content-type": "image/jpeg"}, MEDIA
        if not url.startswith(EP):
            return problem("not_found", 404)
        if headers.get("irlpls-version") != V:
            return problem("unsupported_version", 400, supported_versions=[V])
        if "auth_required" in self.faults and headers.get("authorization") != "Bearer good":
            return 401, {"content-type": "application/problem+json", "www-authenticate": 'Bearer resource_metadata="x"'}, problem("unauthorized", 401)[2]
        path = url[len(EP):]
        if method == "POST" and path == "/quotes":
            return self.quote(body)
        m = re.fullmatch(r"/quotes/([^/]+)", path)
        if method == "GET" and m:
            return self.ok(self.quotes[m.group(1)]) if m.group(1) in self.quotes else problem("not_found", 404)
        if method == "POST" and path == "/orders":
            return self.order(body, headers)
        m = re.fullmatch(r"/orders/([^/]+)(/delivery|/cancel)?", path)
        if m and m.group(1) in self.orders:
            o = self.orders[m.group(1)]
            if method == "GET" and not m.group(2):
                return self.ok(self.advance(o))
            if method == "GET" and m.group(2) == "/delivery":
                return self.ok(self.delivery(o))
            if method == "POST" and m.group(2) == "/cancel":
                if not headers.get("idempotency-key"):
                    return problem("idempotency_key_required", 400)
                if "cancel_changes_terminal" in self.faults:
                    o["state"], o["reason_code"] = "failed", "cancelled"
                return self.ok(o)
        return problem("not_found", 404)

    def quote(self, body):
        req = (body or {}).get("request")
        if not isinstance(req, dict) or not isinstance(req.get("need"), str) or not isinstance(req.get("quantity", 1), int):
            return problem("invalid_request", 400, errors=[{"path": "$.request.need", "detail": "need is required"}])
        qid = "q_" + secrets.token_hex(4)
        exp = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        offer = {"id": "o_1", "provider": {"card_id": "streetlens", "name": "Streetlens"}, "route": "commission",
                 "firm": True, "quantity": 1, "total": {"amount": "12.00", "currency": "USD"}, "expires_at": exp,
                 "order_url": "https://evil.example/orders" if "wrong_order_url" in self.faults else EP + "/orders"}
        q = {"id": qid, "created_at": now(), "expires_at": exp,
             "request_sha256": "0" * 64 if "bad_request_hash" in self.faults else hashlib.sha256(C.jcs(req)).hexdigest(),
             "offers": [offer, dict(offer, id="o_2")] if "two_offers" in self.faults else [offer]}
        self.quotes[qid] = q
        return self.ok(q, 201, location=f"{EP}/quotes/{qid}")

    def order(self, body, headers):
        key = headers.get("idempotency-key")
        if not key and "accept_missing_key" not in self.faults:
            return problem("idempotency_key_required", 400)
        canon = C.jcs(body)
        if key in self.idem and "no_idempotency" not in self.faults:
            stored_body, resp = self.idem[key]
            if stored_body != canon and "ignore_body_mismatch" not in self.faults:
                return problem("idempotency_key_reused", 422)
            return resp
        q = self.quotes.get(body.get("quote_id"))
        if not q:
            return problem("not_found", 404)
        oid = "ord_" + secrets.token_hex(6)
        total = "99.00" if "overcharge" in self.faults else q["offers"][0]["total"]["amount"]
        o = {"id": oid, "state": "submitted", "quote_id": q["id"], "offer_id": body["offer_id"], "card_id": "streetlens",
             "route": "commission", "total": {"amount": total, "currency": "USD"}, "created_at": now(), "updated_at": now(),
             "request_sha256": "1" * 64 if "order_hash" in self.faults else q["request_sha256"],
             "progress": {"ordered": 1, "delivered": 0},
             "status_history": [{"at": now(), "state": "submitted"}],
             "links": {"self": f"{EP}/orders/{oid}", "delivery": f"{EP}/orders/{oid}/delivery", "cancel": f"{EP}/orders/{oid}/cancel"},
             "_nonce": body.get("nonce"), "_rsha": q["request_sha256"], "_polls": 0}
        self.orders[oid] = o
        resp = self.ok(self.public(o), 202, location=f"{EP}/orders/{oid}", **{"retry-after": "0"})
        if key:
            self.idem[key] = (canon, resp)
        return resp

    @staticmethod
    def public(o):
        return {k: v for k, v in o.items() if not k.startswith("_")}

    def advance(self, o):
        seq = ["in_flight", "submitted", "delivered"] if "state_regress" in self.faults else ["in_flight", "delivered"]
        o["_polls"] += 1
        nxt = seq[min(o["_polls"], len(seq)) - 1]
        if "rewrite_history" in self.faults and o["_polls"] == 2:
            o["status_history"][0]["state"] = "in_flight"
        if nxt != o["state"]:
            o["state"], o["updated_at"] = nxt, now()
            o["status_history"].append({"at": now(), "state": nxt})
            if nxt == "delivered":
                o["progress"]["delivered"] = 1
        return self.public(o)

    def sign(self, payload, kid="k1"):
        h = C.b64u(json.dumps({"alg": "ES256", "kid": kid, "typ": "irlpls-receipt+jws"}).encode())
        der = self.key.sign(f"{h}.{C.b64u(C.jcs(payload))}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        if "bad_signature" in self.faults:
            sig = bytes([sig[0] ^ 1]) + sig[1:]
        return f"{h}..{C.b64u(sig)}"

    def delivery(self, o):
        levels = {k: v["level"] for k, v in self.card["verification"].items()}
        stages = []
        for st, keys in STAGE_KEYS.items():
            g = {k: {"level": levels[k], "basis": "card"} for k in keys}
            stages.append({"stage": st, "guarantees": g} if g else {"stage": st, "unknown": "not logged"})
        if "wrong_stage" in self.faults:
            stages[0]["guarantees"]["time"] = {"level": "none", "basis": "card"}
        if "inflated_basis" in self.faults:
            stages[0]["guarantees"]["human"] = {"level": "evidenced", "basis": "card"}
        sha = hashlib.sha256(MEDIA).hexdigest()
        item = {"id": "itm_1", "provider": "streetlens", "kind": "photo", "phenomenon_time": now(), "url": MEDIA_URL,
                "media_type": "image/jpeg", "bytes": len(MEDIA),
                "sha256": "f" * 64 if "media_mismatch" in self.faults else sha}
        payload = {"iss": f"https://{HOST}", "order_id": o["id"], "item_id": "itm_1",
                   "nonce": "wrong-nonce-wrong-nonce-00" if "wrong_nonce" in self.faults else o["_nonce"],
                   "request_sha256": o["_rsha"], "media": {"sha256": sha, "media_type": "image/jpeg", "bytes": len(MEDIA)},
                   "phenomenon_time": item["phenomenon_time"], "issued_at": now()}
        if "moved_item" in self.faults:   # the item as shown is not the item as signed
            item["lat"] = 51.5
        item["receipt"] = {"jws": self.sign(payload), "payload": payload}
        return {"order_id": o["id"], "complete": o["state"] == "delivered", "items": [item] if o["state"] == "delivered" else [],
                "provenance": {"card_id": "streetlens", "stages": stages}}


def run(fake=None, sandbox=True, **kw):
    fake = fake or FakeProvider()
    res = C.check_protocol(HOST, transport=fake, sandbox=sandbox, sleep=lambda s: None, **kw)
    return fake, res


def by(res, status):
    return {n for s, n, _ in res if s == status}


# --- safety: nothing is ever ordered outside a declared sandbox ---------------------------------------------------

def test_dry_run_never_posts_an_order():
    fake, res = run(sandbox=False)
    assert not by(res, C.FAIL), res
    assert fake.order_posts() == []
    assert "order" in by(res, C.SKIP)
    assert any(n == "request_sha256" and s == C.PASS for s, n, _ in res)


def test_sandbox_flag_without_sandbox_card_never_orders():
    fake, res = run(FakeProvider(sandbox=False), sandbox=True)
    assert fake.order_posts() == []
    assert "sandbox" in by(res, C.WARN)


def test_sandbox_card_without_flag_never_orders():
    fake, res = run(FakeProvider(sandbox=True), sandbox=False)
    assert fake.order_posts() == []


def test_wrong_order_url_fails_and_orders_nothing():
    fake, res = run(FakeProvider({"wrong_order_url"}))
    assert "order_url" in by(res, C.FAIL)
    assert fake.order_posts() == [] and not any("evil.example" in c[1] for c in fake.calls)


def test_endpoint_off_domain_is_never_called():
    card = make_card(sandbox=True, endpoint="https://api.attacker.example/irlpls")
    fake, res = run(FakeProvider(card=card))
    assert "endpoint" in by(res, C.FAIL)
    assert not any("attacker" in c[1] for c in fake.calls)


def test_card_only_provider():
    card = make_card()
    del card["protocol"]
    fake, res = run(FakeProvider(card=card))
    assert "protocol block" in by(res, C.FAIL)
    assert len(fake.calls) == 1


def test_http_target_refused():
    fake = FakeProvider()
    res = C.check_protocol("http://" + HOST, transport=fake)
    assert "https" in by(res, C.FAIL) and fake.calls == []


# --- the full sandbox run ----------------------------------------------------------------------------------------

def test_sandbox_full_run_passes():
    fake, res = run()
    assert not by(res, C.FAIL), "\n" + C.render(res)
    passed = by(res, C.PASS)
    for name in ("discover", "protocol block schema", "endpoint", "versions", "jwks_uri", "unsupported version",
                 "invalid quote body", "unknown quote", "unknown order", "quote schema", "request_sha256", "order_url",
                 "read quote", "sandbox", "idempotency key required", "order schema", "order request_sha256", "order total", "idempotent replay",
                 "idempotency key reuse", "state transitions", "delivery schema", "provenance", "media itm_1",
                 "receipt itm_1", "cancel key required", "cancel"):
        assert name in passed, (name, C.render(res))
    assert len(fake.orders) == 1  # the replay did not double-order


# --- each check bites --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fault, failing", [
    ("no_idempotency", "idempotent replay"),
    ("accept_missing_key", "idempotency key required"),
    ("ignore_body_mismatch", "idempotency key reuse"),
    ("plain_errors", "unsupported version"),
    ("two_offers", "offers"),
    ("bad_request_hash", "request_sha256"),
    ("overcharge", "order total"),
    ("state_regress", "state transitions"),
    ("rewrite_history", "status_history"),
    ("media_mismatch", "media itm_1"),
    ("bad_signature", "receipt itm_1"),
    ("wrong_nonce", "receipt itm_1"),
    ("moved_item", "receipt itm_1"),
    ("order_hash", "order request_sha256"),
    ("wrong_stage", "provenance"),
    ("inflated_basis", "provenance basis"),
    ("cancel_changes_terminal", "cancel"),
])
def test_fault_is_caught(fault, failing):
    _, res = run(FakeProvider({fault}))
    assert failing in by(res, C.FAIL), C.render(res)


def test_unauthenticated_quote_stops_before_order():
    fake, res = run(FakeProvider({"auth_required"}, card=make_card(sandbox=True, public_quotes=False)))
    assert "order" in by(res, C.SKIP)
    assert "quote" in by(res, C.PASS)  # a well-formed 401 problem
    assert fake.order_posts() == []


def test_credentials_reach_the_endpoint_only():
    fake, res = run(FakeProvider({"auth_required"}, card=make_card(sandbox=True, public_quotes=False)),
                    auth_headers={"Authorization": "Bearer good"})
    assert not by(res, C.FAIL), C.render(res)
    leaked = [c[1] for c in fake.calls if "authorization" in c[2] and not c[1].startswith(EP)]
    assert leaked == []
    assert "Bearer good" not in C.render(res)


# --- building blocks ---------------------------------------------------------------------------------------------

def _draft_examples():
    text = (ROOT / "docs/protocol/PROTOCOL-DRAFT.md").read_text()
    return [json.loads(b) for b in re.findall(r"<!-- validate: [^>]*? -->\s*```json\n(.*?)```", text, re.S)]


def test_jcs_matches_the_draft_quote_hash():
    ex = _draft_examples()
    qr = next(e for e in ex if "request" in e and "proof" in e.get("constraints", {}))
    quote = next(e for e in ex if "offers" in e)
    assert hashlib.sha256(C.jcs(qr["request"])).hexdigest() == quote["request_sha256"]


def test_verify_receipt_on_the_draft_example():
    ex = _draft_examples()
    item = next(e for e in ex if e.get("items") and "receipt" in e["items"][0])["items"][0]
    jwks = next(e for e in ex if "keys" in e)
    rc = item["receipt"]
    expect = {"iss": "https://streetlens.example", "order_id": "ord_5d1Hq", "item_id": "itm_1",
              "nonce": "Zx3qN0p8YgS1rT6uWv9a2Q", "request_sha256": rc["payload"]["request_sha256"],
              "media_sha256": item["sha256"]}
    res = C.verify_receipt(rc, jwks, expect)
    assert all(s == C.PASS for s, _ in res), res
    tampered = dict(rc, payload=dict(rc["payload"], lat=0.0))
    assert any(s == C.FAIL for s, _ in C.verify_receipt(tampered, jwks, expect))
    assert any(s == C.FAIL for s, _ in C.verify_receipt(rc, jwks, dict(expect, nonce="other")))
    kid = json.loads(C.b64ud(rc["jws"].split(".")[0]))["kid"]
    none_alg = dict(rc, jws=C.b64u(json.dumps({"alg": "none", "kid": kid}).encode()) + "..")
    assert any(s == C.FAIL for s, _ in C.verify_receipt(none_alg, jwks, expect))


@pytest.mark.parametrize("value, ok", [(1.0, "1"), (0.5, "0.5"), (1e-7, "1e-7"), (0.00001, "0.00001"), (1e21, "1e+21"),
                                       (-98.9054, "-98.9054"), (29.9683, "29.9683")])
def test_jcs_numbers(value, ok):
    assert C.jcs(value).decode() == ok


@pytest.mark.parametrize("m", [{"amount": "NaN", "currency": "USD"}, {"amount": 12, "currency": "USD"},
                               {"amount": "-1", "currency": "USD"}, {"amount": "1e3", "currency": "USD"}, {}, None])
def test_money_rejects_non_decimal_strings(m):
    assert C.money(m) is None


def test_default_request_coarsens_place():
    body = C.default_quote_request(make_card(), lat=39.80168, lon=-89.64357)
    assert body["request"]["lat"] == 39.8 and body["request"]["lon"] == -89.64
    assert "policy" not in body["request"] and "why" not in body["request"]


# --- CLI ---------------------------------------------------------------------------------------------------------

def test_cli_sandbox_run(capsys):
    fake = FakeProvider()
    assert C.main([HOST, "--protocol", "--sandbox"], transport=fake, sleep=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "[PASS] idempotent replay" in out and "0 fail" in out
    assert len(fake.orders) == 1


def test_cli_dry_run(capsys):
    fake = FakeProvider()
    assert C.main([HOST, "--protocol"], transport=fake) == 0
    assert "[SKIP] order: dry run" in capsys.readouterr().out
    assert fake.order_posts() == []


def test_cli_failure_exit_code(capsys):
    assert C.main([HOST, "--protocol", "--sandbox"], transport=FakeProvider({"no_idempotency"}), sleep=lambda s: None) == 1


def test_cli_sandbox_needs_protocol():
    with pytest.raises(SystemExit):
        C.main([HOST, "--sandbox"], transport=FakeProvider())


def test_cli_token_from_env(monkeypatch, capsys):
    monkeypatch.setenv("IRLPLS_PROTOCOL_TOKEN", "good")
    fake = FakeProvider({"auth_required"}, card=make_card(sandbox=True, public_quotes=False))
    assert C.main([HOST, "--protocol", "--sandbox"], transport=fake, sleep=lambda s: None) == 0
    assert "Bearer good" not in capsys.readouterr().out
