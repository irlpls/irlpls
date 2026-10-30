"""Evidence checks (irlpls/evidence.py, conformance --evidence) against recorded synthetic fixtures; no network.

Fixtures: tests/fixtures/evidence (regenerate with make_fixtures.py there). Three mirror the proof audit:
Satellogic (a receipt checked against a key that did not sign it), Copernicus (signed after the certificate expired)
and Vangrid (a Merkle proof that omits the self-pair steps of odd nodes).
"""
import copy
import hashlib
import json
import pathlib

import pytest

pytest.importorskip("cryptography")

from irlpls import conformance, evidence, validate  # noqa: E402

FX = pathlib.Path(__file__).resolve().parent / "fixtures" / "evidence"
BASE = "https://provider.example/samples/"


def fx(name):
    return (FX / name).read_bytes()


def fetcher(extra=None):
    """Serve the fixtures at BASE; `extra` maps URLs to bytes (in-memory packs). Anything else is a fetch error."""
    seen = []

    def get(url):
        seen.append(url)
        if extra and url in extra:
            return extra[url]
        if url.startswith(BASE):
            p = FX / url[len(BASE):]
            if p.resolve().is_file() and FX in p.resolve().parents:
                return p.read_bytes()
        raise OSError(f"404 {url}")
    get.seen = seen
    return get


def pack(**kw):
    return json.dumps(dict({"irlpls_sample_pack": "2026-10-26", "readme": "README.md"}, **kw)).encode()


def card(where="provenance.receipt", url=BASE + "jws/irlpls-sample.json", level="evidenced", **extra):
    group, key = where.split(".")
    entry = {"level": level, "method": "synthetic", "unknown": "synthetic", "evidence_url": url}
    c = {"id": "fixture", "provider_url": "https://provider.example", group: {key: entry}}
    c.update(extra)
    return c


def run(c, extra=None, **opts):
    res = evidence.check_card(c, fetch=fetcher(extra), **opts)
    assert len(res) == 1
    return res[0]


# ---------------------------------------------------------------- hashing primitives

def test_keccak256_known_vectors():
    assert evidence.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert evidence.keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    assert evidence._keccak(b"q" * 500, pad=0x06) == hashlib.sha3_256(b"q" * 500).digest()  # same sponge as SHA3


# ---------------------------------------------------------------- JWS receipts

def test_valid_jws_receipt_passes():
    r = run(card())
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]
    assert r["sample_pack"] and r["type"] == "jws-receipt"
    assert any("media hash and size match" in x for x in r["reasons"])
    urls = [f["url"] for f in r["fetched"]]
    assert urls[0] == BASE + "jws/irlpls-sample.json" and BASE + "artefact.txt" in urls and BASE + "jws/jwks.json" in urls
    f = r["fetched"][0]
    assert f["sha256"] == hashlib.sha256(fx("jws/irlpls-sample.json")).hexdigest() and f["retrieved_at"].endswith("Z")


def test_receipt_under_the_wrong_key_fails_like_satellogic():
    wrong = BASE + "jws/wrong.irlpls-sample.json"
    extra = {wrong: pack(type="jws-receipt", artefact="../artefact.txt", proof="receipt.json",
                         trust_anchor="jwks-wrong-key.json")}
    r = run(card(url=wrong), extra)
    assert r["verdict"] == evidence.FAILED
    assert "signature does not verify" in r["reasons"][0]


def test_receipt_key_must_come_from_the_cards_jwks_uri():
    c = card(protocol={"jwks_uri": BASE + "jws/jwks-wrong-key.json"})
    r = run(c)  # the pack names the right key set, but the card's jwks_uri is the only place keys may come from
    assert r["verdict"] == evidence.FAILED and "jwks_uri" in r["reasons"][0]


def test_receipt_with_a_changed_artefact_fails():
    url = BASE + "jws/x.irlpls-sample.json"
    extra = {url: pack(type="jws-receipt", artefact="other.txt", proof="receipt.json", trust_anchor="jwks.json"),
             BASE + "jws/other.txt": b"not the delivered file\n"}
    r = run(card(url=url), extra)
    assert r["verdict"] == evidence.FAILED and "media hash" in r["reasons"][0]


def test_jwks_alone_is_not_checkable():
    r = run(card(url=BASE + "jws/jwks.json"))
    assert r["verdict"] == evidence.NOT_CHECKABLE and r["inferred"] and r["type"] == "jwks"
    assert "sample receipt" in r["reasons"][0]


def test_jwks_pack_with_a_sample_receipt_verifies():
    url = BASE + "jws/k.irlpls-sample.json"
    extra = {url: pack(type="jwks", trust_anchor="jwks.json", proof="receipt.json", artefact="../artefact.txt")}
    assert run(card(url=url), extra)["verdict"] == evidence.VERIFIED


# ---------------------------------------------------------------- X.509 signature (Copernicus)

def x509_pack(sig):
    url = BASE + f"x509/{sig}.irlpls-sample.json"
    return url, {url: pack(type="x509-signature", artefact="trace.json", proof=f"{sig}.json")}


def test_signature_after_certificate_expiry_fails_like_copernicus():
    url, extra = x509_pack("signature-expired")
    r = run(card("provenance.custody", url), extra)
    assert r["verdict"] == evidence.FAILED
    assert "after the certificate expired on 2024-05-15" in r["reasons"][0]


def test_signature_within_certificate_validity_verifies_but_self_signed_is_flagged():
    url, extra = x509_pack("signature-valid")
    r = run(card("provenance.custody", url), extra)
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]
    assert "self-signed" in r["trust"]


def test_x509_signature_over_changed_bytes_fails():
    url, extra = x509_pack("signature-valid")
    extra[BASE + "x509/trace.json"] = fx("x509/trace.json").replace(b"CREATE", b"DELETE")
    assert run(card("provenance.custody", url), extra)["verdict"] == evidence.FAILED


# ---------------------------------------------------------------- Merkle proofs (Vangrid)

@pytest.mark.parametrize("h", ["keccak256", "sha256"])
def test_merkle_proof_missing_odd_node_steps_fails_standard_verifier_and_passes_with_rule(h):
    p = json.loads(fx(f"merkle/proof-{h}.json"))
    leaf, root = bytes.fromhex(p["leaf"][2:]), bytes.fromhex(p["root"][2:])
    steps = [bytes.fromhex(s[2:]) for s in p["proof"]]
    ok, why = evidence.verify_merkle(leaf, steps, root, hash=h, pair="sorted", odd="explicit")
    assert not ok and "not the root" in why                     # a standard sorted-pair fold, as the audit ran
    ok, why = evidence.verify_merkle(leaf, steps, root, hash=h, pair="sorted", odd="explicit",
                                     index=p["index"], tree_size=p["tree_size"])
    assert not ok                                               # with positions, the missing steps show
    ok, why = evidence.verify_merkle(leaf, steps, root, hash=h, pair="sorted", odd="promote",
                                     index=p["index"], tree_size=p["tree_size"])
    assert not ok                                               # the wrong odd-node rule fails too
    ok, why = evidence.verify_merkle(leaf, steps, root, hash=h, pair="sorted", odd="duplicate",
                                     index=p["index"], tree_size=p["tree_size"])
    assert ok, why
    with_self = [leaf, evidence.MERKLE_HASHES[h](leaf + leaf)] + steps  # the self-pair steps written out
    ok, why = evidence.verify_merkle(leaf, with_self, root, hash=h, pair="sorted", odd="explicit")
    assert ok, why                                              # what a fixed proof would publish


def test_merkle_pack_needs_a_declared_odd_rule():
    url = BASE + "merkle/a.irlpls-sample.json"
    extra = {url: pack(type="merkle", proof="proof-keccak256.json", params={"hash": "keccak256", "pair": "sorted"})}
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.NOT_CHECKABLE and "odd-node rule not declared" in r["reasons"][0]
    extra[url] = pack(type="merkle", proof="proof-keccak256.json",
                      params={"hash": "keccak256", "pair": "sorted", "odd": "duplicate"})
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]
    assert any("anchor" in x and "not checked offline" in x for x in r["reasons"])
    extra[url] = pack(type="merkle", proof="proof-keccak256.json",
                      params={"hash": "keccak256", "pair": "sorted", "odd": "explicit"})
    assert run(card("verification.time", url), extra)["verdict"] == evidence.FAILED


def test_merkle_root_rejects_a_proof_with_left_over_steps():
    p = json.loads(fx("merkle/proof-sha256.json"))
    leaf, steps = bytes.fromhex(p["leaf"][2:]), [bytes.fromhex(s[2:]) for s in p["proof"]]
    with pytest.raises(evidence.Fail, match="left over"):
        evidence.merkle_root(leaf, steps + steps, hash="sha256", odd="duplicate", index=4, tree_size=5)


# ---------------------------------------------------------------- RFC 3161

def test_rfc3161_token_verifies_its_imprint():
    data = fx("rfc3161/data.txt")
    for name in ("rfc3161/response.tsr", "rfc3161/token.tst"):
        r = evidence.verify_rfc3161(fx(name), data)
        assert r["imprint_alg"] == "sha256" and r["trust"] == "unknown" and r["gen_time"].endswith("Z")
        assert "Synthetic TSA" in r["signer"]
    with pytest.raises(evidence.Fail, match="message imprint"):
        evidence.verify_rfc3161(fx("rfc3161/response.tsr"), data + b"x")


def test_rfc3161_trust_list_is_configured_not_assumed():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    fp = x509.load_pem_x509_certificate(fx("rfc3161/tsa.pem")).fingerprint(hashes.SHA256()).hex()
    r = evidence.verify_rfc3161(fx("rfc3161/token.tst"), fx("rfc3161/data.txt"), trusted=[fp.upper()])
    assert r["trust"] == "listed"


def test_rfc3161_tampered_signature_fails():
    tok = bytearray(fx("rfc3161/token.tst"))
    tok[-5] ^= 0x01  # inside the signature
    with pytest.raises(evidence.Fail):
        evidence.verify_rfc3161(bytes(tok), fx("rfc3161/data.txt"))


def test_rfc3161_sample_pack_verifies_with_unknown_trust():
    r = run(card("verification.time", BASE + "rfc3161/irlpls-sample.json"))
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]
    assert "unknown" in r["trust"]
    r = run(card("verification.time", BASE + "rfc3161/irlpls-sample.json"),
            tsa_trust=[__import__("hashlib").sha256(b"someone else").hexdigest()])
    assert "unknown" in r["trust"]


# ---------------------------------------------------------------- Rekor

def rekor(entry="entry.json", key="log.pem", artefact="../artefact.txt"):
    url = BASE + "rekor/irlpls-sample.json"
    return url, {url: pack(type="rekor", proof=entry, trust_anchor=key, artefact=artefact)}


def test_rekor_inclusion_proof_verifies_offline():
    url, extra = rekor()
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.VERIFIED, r["reasons"]
    assert any("checkpoint signed" in x for x in r["reasons"])
    assert any("signed entry timestamp verifies" in x for x in r["reasons"])


def test_rekor_without_a_proof_is_not_checkable_offline():
    url, extra = rekor(entry="entry-no-proof.json")
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.NOT_CHECKABLE and "not checkable offline" in r["reasons"][0]


def test_rekor_entry_for_another_file_fails():
    url, extra = rekor(artefact="../rfc3161/data.txt")
    assert run(card("verification.time", url), extra)["verdict"] == evidence.FAILED


def test_rekor_wrong_log_key_fails():
    url, extra = rekor(key="../detached/publickey.pem")
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.FAILED and "checkpoint" in r["reasons"][0]


# ---------------------------------------------------------------- detached signature, C2PA, pages

def test_detached_signature_under_the_published_key():
    url = BASE + "detached/irlpls-sample.json"
    extra = {url: pack(type="detached-signature", artefact="manifest.txt", proof="manifest.txt.sig",
                       trust_anchor="publickey.pem")}
    assert run(card("verification.content", url), extra)["verdict"] == evidence.VERIFIED
    extra[url] = pack(type="detached-signature", artefact="manifest.txt", proof="manifest.txt.sig",
                      trust_anchor="publickey-wrong.pem")
    r = run(card("verification.content", url), extra)
    assert r["verdict"] == evidence.FAILED and "different key" in r["reasons"][0]


def test_c2pa_without_c2patool_is_not_checkable():
    url = BASE + "c2pa/irlpls-sample.json"
    extra = {url: pack(type="c2pa", artefact="../artefact.txt")}
    r = run(card("verification.camera", url), extra, c2patool=None)
    assert r["verdict"] == evidence.NOT_CHECKABLE and "tool missing" in r["reasons"][0]


def test_c2pa_reports_the_tools_validation_state(tmp_path):
    tool = tmp_path / "c2patool"
    tool.write_text("#!/bin/sh\necho '{\"active_manifest\": \"m\", \"validation_state\": \"Valid\"}'\n")
    tool.chmod(0o755)
    url = BASE + "c2pa/irlpls-sample.json"
    extra = {url: pack(type="c2pa", artefact="../artefact.txt")}
    r = run(card("verification.camera", url), extra, c2patool=str(tool))
    assert r["verdict"] == evidence.VERIFIED and "claimed" in r["trust"]  # Valid is not Trusted (STANDARD 3)
    tool.write_text("#!/bin/sh\necho '{\"validation_state\": \"Invalid\", \"validation_status\": [{\"code\": \"assertion.dataHash.mismatch\"}]}'\n")
    r = run(card("verification.camera", url), extra, c2patool=str(tool))
    assert r["verdict"] == evidence.FAILED and "dataHash" in r["reasons"][0]


def test_a_web_page_is_not_checkable_and_asks_for_a_sample_pack():
    extra = {"https://provider.example/how-we-sign": b"<!doctype html><h1>We sign everything</h1>"}
    r = run(card(url="https://provider.example/how-we-sign"), extra)
    assert r["verdict"] == evidence.NOT_CHECKABLE and "sample pack" in r["reasons"][0]
    assert r["fetched"][0]["sha256"] == hashlib.sha256(extra["https://provider.example/how-we-sign"]).hexdigest()


def test_unsafe_evidence_url_is_refused_before_any_request():
    r = evidence.check_card(card(url="https://127.0.0.1/irlpls-sample.json"))[0]
    assert r["verdict"] == evidence.NOT_CHECKABLE and "non-public" in r["reasons"][0]
    r = evidence.check_card(card(url="http://provider.example/irlpls-sample.json"))[0]
    assert r["verdict"] == evidence.NOT_CHECKABLE and "https required" in r["reasons"][0]


def test_malformed_evidence_fails_without_crashing():
    url = BASE + "bad/irlpls-sample.json"
    extra = {url: pack(type="rfc3161", proof="t.der", artefact="../artefact.txt"), BASE + "bad/t.der": b"\x30\x03\x02\x01"}
    r = run(card("verification.time", url), extra)
    assert r["verdict"] == evidence.FAILED


def test_checks_never_change_the_card():
    c = card(level="claimed")
    before = copy.deepcopy(c)
    r = run(c)
    assert r["verdict"] == evidence.VERIFIED and c == before


# ---------------------------------------------------------------- validate + conformance

def test_validate_warns_when_an_evidenced_entry_has_no_sample_pack():
    def warns(url):
        c = card("provenance.receipt", url)
        return [m for s, w, m in validate.problems(c) if s == "warn" and w == "provenance.receipt"]
    assert any("sample pack" in m for m in warns("https://provider.example/docs/signing"))
    assert not warns("https://provider.example/.well-known/irlpls-samples/receipt/irlpls-sample.json")
    c = card("verification.time", "https://provider.example/docs/signing", level="claimed")
    assert not [m for s, w, m in validate.problems(c) if "sample pack" in m]


def test_conformance_evidence_on_a_local_card(tmp_path, capsys):
    url, extra = x509_pack("signature-expired")
    c = card("provenance.custody", url)
    p = tmp_path / "card.json"
    p.write_text(json.dumps(c))
    code = conformance.main([str(p), "--evidence"], evidence_fetch=fetcher(extra))
    out = capsys.readouterr().out
    assert code == 1 and "[FAIL] evidence provenance.custody" in out and "expired" in out
    c["provenance"]["custody"]["level"] = "claimed"  # a failed check on a claimed entry warns, not fails
    p.write_text(json.dumps(c))
    assert conformance.main([str(p), "--evidence"], evidence_fetch=fetcher(extra)) == 0
    p.write_text(json.dumps(card()))
    assert conformance.main([str(p), "--evidence"], evidence_fetch=fetcher()) == 0
    assert "[PASS] evidence provenance.receipt" in capsys.readouterr().out


def test_conformance_evidence_on_a_registry_card_with_no_network():
    res = conformance.check_evidence(evidence.load_card("copernicus-sentinel"), fetch=fetcher())
    assert res and all(s in (conformance.WARN, conformance.SKIP, conformance.FAIL) for s, _, _ in res)
    assert all("could not fetch" in d or "unknown type" in d for _, _, d in res)


def test_signed_record_shape_is_inferred_and_checked_at_its_timestamp():
    """The shape CDSE serves (a list of records with a signature block and a timestamp), synthetic keys."""
    blk = json.loads(fx("x509/signature-expired.json"))
    rec = [{"event": "CREATE", "timestamp": "2024-06-19T02:19:24.020063Z",
            "signature": {"algorithm": blk["algorithm"], "signature": blk["signature"],
                          "certificate": blk["certificate"], "message": fx("x509/trace.json").decode()}}]
    url = "https://provider.example/api/traces/1"
    r = run(card("provenance.custody", url, level="claimed"), {url: json.dumps(rec).encode()})
    assert r["type"] == "x509-signature" and r["inferred"]
    assert r["verdict"] == evidence.FAILED and "expired on 2024-05-15" in r["reasons"][0]
    rec[0]["timestamp"] = "2024-01-10T08:00:00Z"
    r = run(card("provenance.custody", url, level="claimed"), {url: json.dumps(rec).encode()})
    assert r["verdict"] == evidence.VERIFIED and any("type inferred" in x for x in r["reasons"])
