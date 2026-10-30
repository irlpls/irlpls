"""Regenerate the synthetic evidence fixtures in this directory (tests/test_evidence.py reads the files; it never runs
this). Every key here is made up for the fixtures and signs nothing else.

    uv run --with cryptography python tests/fixtures/evidence/make_fixtures.py

Needs `openssl` with the `ts` command (OpenSSL 3+) for the RFC 3161 token, so that the token comes from an independent
implementation, not from irlpls's own parser. The fixtures mirror the proof-audit findings (docs/research/proof-audit):
a receipt checked against the wrong key (Satellogic, g3.md), a signature made after its certificate expired
(Copernicus, g1.md), and a Merkle proof that omits the self-pair steps of odd nodes (Vangrid, g4.md).
"""
import base64
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from irlpls.evidence import keccak256  # noqa: E402
from irlpls.protocol.receipts import ReceiptSigner, jcs  # noqa: E402

UTC = dt.timezone.utc
ARTEFACT = b"irlpls synthetic sample: river gauge photo bytes stand-in\n"


def w(name, data):
    p = HERE / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else (json.dumps(data, indent=2) + "\n").encode())


def pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def cert(key, cn, nb, na, eku=None):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na))
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=True)
    return b.sign(key, hashes.SHA256())


def jws_fixtures():
    w("artefact.txt", ARTEFACT)
    signer = ReceiptSigner.generate(kid="sample-2026-10")
    receipt = signer.sign({
        "iss": "https://provider.example", "order_id": "ord_sample_1", "item_id": "itm_1",
        "nonce": "n-3f9a2c", "request_sha256": hashlib.sha256(b"sample request").hexdigest(),
        "media": {"sha256": hashlib.sha256(ARTEFACT).hexdigest(), "media_type": "text/plain", "bytes": len(ARTEFACT)},
        "phenomenon_time": "2026-10-28T12:00:00Z", "issued_at": "2026-10-28T12:05:00Z"})
    w("jws/receipt.json", receipt)
    w("jws/jwks.json", {"keys": [signer.public_jwk()]})
    other = ReceiptSigner.generate(kid="sample-2026-10")  # same kid, different key: what Satellogic's samples show
    w("jws/jwks-wrong-key.json", {"keys": [other.public_jwk()]})
    w("jws/irlpls-sample.json", {
        "irlpls_sample_pack": "2026-10-26", "stage": "receipt", "type": "jws-receipt",
        "artefact": "../artefact.txt", "proof": "receipt.json", "trust_anchor": "jwks.json", "readme": "README.md",
        "verify": "python -m irlpls.evidence <card> (or any JWS library: detached payload = RFC 8785 of payload)"})
    w("jws/README.md", b"Sample receipt: artefact ../artefact.txt, receipt receipt.json (detached JWS, ES256), keys "
                       b"jwks.json. Check: SHA-256 of the artefact equals payload.media.sha256, then verify the JWS "
                       b"over RFC 8785 of payload with the key named by kid.\n")


def detached_fixtures():
    key, wrong = rsa.generate_private_key(65537, 2048), rsa.generate_private_key(65537, 2048)
    manifest = b"sha256  " + hashlib.sha256(ARTEFACT).hexdigest().encode() + b"  artefact.txt\n"
    w("detached/manifest.txt", manifest)
    w("detached/manifest.txt.sig", key.sign(manifest, padding.PKCS1v15(), hashes.SHA256()))
    w("detached/publickey.pem", pem(key))
    w("detached/publickey-wrong.pem", pem(wrong))


def x509_fixtures():
    key = rsa.generate_private_key(65537, 2048)
    c = cert(key, "trace.provider.example", dt.datetime(2023, 5, 16, tzinfo=UTC), dt.datetime(2024, 5, 15, tzinfo=UTC))
    trace = json.dumps({"event": "CREATE", "hash_algorithm": "SHA256",
                        "product_hash": hashlib.sha256(ARTEFACT).hexdigest()}, sort_keys=True).encode()
    w("x509/trace.json", trace)
    sig = base64.b64encode(key.sign(trace, padding.PKCS1v15(), hashes.SHA256())).decode()
    cpem = c.public_bytes(serialization.Encoding.PEM).decode()
    for name, at in (("signature-expired.json", "2024-06-19T02:19:24Z"), ("signature-valid.json", "2024-01-10T08:00:00Z")):
        w(f"x509/{name}", {"algorithm": "RSA-SHA256", "signature": sig, "certificate": cpem, "signed_at": at})


def merkle_fixtures():
    """Five leaves, sorted pairs, odd nodes hashed with themselves (Vangrid's tree). The published proof for the last
    leaf omits the self-pair steps, as Vangrid's do."""
    for hname, h in (("keccak256", keccak256), ("sha256", lambda b: hashlib.sha256(b).digest())):
        leaves = [h(f"capture-{i}".encode()) for i in range(5)]
        level, levels = leaves, [leaves]
        while len(level) > 1:
            nxt = []
            for i in range(0, len(level), 2):
                a, b = level[i], level[i + 1] if i + 1 < len(level) else level[i]
                nxt.append(h(min(a, b) + max(a, b)))
            level = nxt
            levels.append(level)
        root, idx = levels[-1][0], 4
        proof, i = [], idx
        for lv in levels[:-1]:
            sib = i ^ 1
            if sib < len(lv):  # an odd last node has no sibling: its self-pair step is left out
                proof.append(lv[sib].hex())
            i //= 2
        w(f"merkle/proof-{hname}.json", {"leaf": "0x" + leaves[idx].hex(), "index": idx, "tree_size": 5,
                                         "proof": ["0x" + p for p in proof], "root": "0x" + root.hex()})


def _mth(hs):
    if len(hs) == 1:
        return hs[0]
    k = 1 << (len(hs) - 1).bit_length() - 1
    return hashlib.sha256(b"\x01" + _mth(hs[:k]) + _mth(hs[k:])).digest()


def _path(m, hs):
    if len(hs) == 1:
        return []
    k = 1 << (len(hs) - 1).bit_length() - 1
    return _path(m, hs[:k]) + [_mth(hs[k:])] if m < k else _path(m - k, hs[k:]) + [_mth(hs[:k])]


def rekor_fixtures():
    log = ec.generate_private_key(ec.SECP256R1())
    w("rekor/log.pem", pem(log))
    body = jcs({"apiVersion": "0.0.1", "kind": "hashedrekord", "spec": {
        "data": {"hash": {"algorithm": "sha256", "value": hashlib.sha256(ARTEFACT).hexdigest()}},
        "signature": {"content": "c2lnbmF0dXJl", "publicKey": {"content": "cHVibGljIGtleQ=="}}}})
    others = [jcs({"n": i}) for i in range(4)]
    bodies = others[:3] + [body] + others[3:]
    idx, size = 3, len(bodies)
    hs = [hashlib.sha256(b"\x00" + b).digest() for b in bodies]
    root = _mth(hs)
    text = f"rekor.example - 1193050959916656506\n{size}\n{base64.b64encode(root).decode()}\n"
    keyhint = hashlib.sha256(log.public_key().public_bytes(serialization.Encoding.DER,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo)).digest()[:4]
    sig = log.sign(text.encode(), ec.ECDSA(hashes.SHA256()))
    checkpoint = text + "\n— rekor.example " + base64.b64encode(keyhint + sig).decode() + "\n"
    b64body = base64.b64encode(body).decode()
    entry = {"body": b64body, "integratedTime": 1793102400, "logID": hashlib.sha256(b"log").hexdigest(), "logIndex": idx}
    setsig = base64.b64encode(log.sign(jcs(entry), ec.ECDSA(hashes.SHA256()))).decode()
    entry["verification"] = {"inclusionProof": {"checkpoint": checkpoint, "hashes": [p.hex() for p in _path(idx, hs)],
                                                "logIndex": idx, "rootHash": root.hex(), "treeSize": size},
                             "signedEntryTimestamp": setsig}
    w("rekor/entry.json", {"24296fb24b8ad77a" + hashlib.sha256(body).hexdigest(): entry})
    bare = {k: v for k, v in entry.items() if k != "verification"}
    w("rekor/entry-no-proof.json", {"24296fb24b8ad77a" + hashlib.sha256(body).hexdigest(): bare})


def rfc3161_fixtures():
    key = ec.generate_private_key(ec.SECP256R1())
    c = cert(key, "Synthetic TSA", dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2036, 1, 1, tzinfo=UTC),
             [ExtendedKeyUsageOID.TIME_STAMPING])
    data = b"daily receipt-hash Merkle root 2026-10-28: " + keccak256(ARTEFACT).hex().encode() + b"\n"
    w("rfc3161/data.txt", data)
    w("rfc3161/tsa.pem", c.public_bytes(serialization.Encoding.PEM))
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "tsa.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
        (d / "tsa.pem").write_bytes(c.public_bytes(serialization.Encoding.PEM))
        (d / "data.txt").write_bytes(data)
        (d / "serial").write_text("01\n")
        (d / "ts.cnf").write_text(
            "[ tsa ]\ndefault_tsa = t\n[ t ]\nserial = ./serial\ncrypto_device = builtin\nsigner_digest = sha256\n"
            "default_policy = 1.2.3.4.1\ndigests = sha256\naccuracy = secs:1\nordering = no\ntsa_name = yes\n"
            "ess_cert_id_chain = no\ness_cert_id_alg = sha256\n")
        run = lambda *a: subprocess.run(["openssl", "ts", *a], cwd=d, check=True, capture_output=True)
        run("-query", "-data", "data.txt", "-sha256", "-cert", "-out", "req.tsq")
        run("-reply", "-config", "ts.cnf", "-queryfile", "req.tsq", "-inkey", "tsa.key", "-signer", "tsa.pem",
            "-out", "resp.tsr")
        run("-reply", "-in", "resp.tsr", "-token_out", "-out", "token.tst")
        run("-verify", "-data", "data.txt", "-in", "resp.tsr", "-CAfile", "tsa.pem")  # independent cross-check
        w("rfc3161/response.tsr", (d / "resp.tsr").read_bytes())
        w("rfc3161/token.tst", (d / "token.tst").read_bytes())
    w("rfc3161/irlpls-sample.json", {"irlpls_sample_pack": "2026-10-26", "stage": "time", "type": "rfc3161",
                                     "artefact": "data.txt", "proof": "response.tsr", "trust_anchor": "tsa.pem",
                                     "readme": "README.md",
                                     "verify": "openssl ts -verify -data data.txt -in response.tsr -CAfile tsa.pem"})
    w("rfc3161/README.md", b"RFC 3161 sample: data.txt is time-stamped by response.tsr from a synthetic TSA (tsa.pem). "
                           b"Check: openssl ts -verify -data data.txt -in response.tsr -CAfile tsa.pem\n")


if __name__ == "__main__":
    jws_fixtures()
    detached_fixtures()
    x509_fixtures()
    merkle_fixtures()
    rekor_fixtures()
    rfc3161_fixtures()
    print("fixtures written to", HERE)
