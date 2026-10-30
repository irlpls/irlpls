"""Evidence checks: fetch every `evidence_url` on a Capture Card and run the verifier for what is there
(docs/STANDARD.md Sections 3, 3.1 and its sample-pack note, 6.1 and 14.7).

    python -m irlpls.conformance <card id | card file | domain> --evidence
    python -m irlpls.evidence cards/human-for-ai.json [--tsa-trust fingerprints.txt] [--json]

For every guarantee (`verification.*`) and provenance entry (`provenance.*`) with an `evidence_url`, this module fetches
the URL through irlpls.http's outbound checks (https only, public hosts after DNS, redirects re-checked, size caps),
records the SHA-256, size and retrieval time of every byte it read (the Section 3 SHOULD), and runs the verifier for the
evidence's type. The type comes from a **sample pack** (`irlpls-sample.json`, STANDARD Section 3.1 note) when the URL is
one; otherwise it is inferred from the bytes and the inference is stated in the reasons.

Verifiers (pluggable: `VERIFIERS[type] = fn(ctx) -> (verdict, reasons)`):

- `jws-receipt`: a detached-JWS receipt (STANDARD 14.7) checked with irlpls.protocol.receipts.verify_receipt against
  the key set at the card's `protocol.jwks_uri` (or the pack's `trust_anchor` when the card has none); `iss` must be
  the origin of the card's `provider_url`; the artefact's SHA-256 and size must match `media`.
- `jwks`: a key set. Parses every key; a key set alone shows no signature, so without a sample receipt the verdict is
  "not checkable".
- `rfc3161`: an RFC 3161 TimeStampResp or TimeStampToken. Checks the message imprint against the artefact, the CMS
  signature, messageDigest and contentType attributes, the TSA certificate's validity at genTime and its timeStamping
  EKU. Trust-list status is "unknown" unless the signer's certificate SHA-256 is on a configured list.
- `rekor`: a Sigstore Rekor log entry. Offline only: the RFC 6962 inclusion proof from the entry's leaf to the
  checkpoint root, the checkpoint's signature and the signed entry timestamp under the log key given as trust anchor,
  and the hashedrekord digest against the artefact. No proof in the entry: "not checkable offline".
- `c2pa`: runs `c2patool` on the artefact when it is installed and reports its validation state; "Trusted" (C2PA Trust
  List) is the only state STANDARD Section 3 counts. No tool: "not checkable (tool missing)".
- `merkle`: an inclusion proof under a declared hash (`keccak256` or `sha256`, extendable via MERKLE_HASHES), pair rule
  (`sorted` or `ordered`) and odd-node rule (`explicit`: the proof lists every step; `duplicate`: an odd last node is
  hashed with itself and the proof omits that step; `promote`: an odd last node moves up unchanged). Undeclared odd
  rule: "not checkable". The root's own anchor (a chain, a transaction) is not checked offline and the reasons say so.
- `x509-signature`: a signature by an X.509 certificate at a stated signing time; the certificate must be valid at that
  time (a signature made after the certificate expired fails).
- `detached-signature`: a raw signature over the artefact under a published PEM public key (RSA PKCS#1 v1.5, ECDSA or
  Ed25519, SHA-256).

Output per entry: `{where, level, evidence_url, type, inferred, verdict, reasons, trust, fetched}` with verdict one of
"verified", "failed" or "not checkable". Nothing here changes a card: a verified check is reported, never applied.
Needs the `cryptography` package for signature checks; fetching and Merkle checks are stdlib only.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import http as _http
from . import validate as _validate

VERIFIED, FAILED, NOT_CHECKABLE = "verified", "failed", "not checkable"
PACK_VERSION = "2026-10-26"
MAX_EVIDENCE_BYTES = _http.MAX_BYTES
TYPES = ("jws-receipt", "jwks", "rfc3161", "rekor", "c2pa", "merkle", "x509-signature", "detached-signature")


class Fail(Exception):
    """A check failed; the message is the reason."""


class Unknown(Exception):
    """A check could not be made; the message is the reason."""


# ---------------------------------------------------------------- fetching

def fetch_bytes(url, timeout=20, max_bytes=MAX_EVIDENCE_BYTES):
    """One GET through irlpls.http's checks: https only, public hosts after DNS (fail closed), redirects re-checked and
    never to http, body and decompressed body capped at max_bytes. Returns the body bytes."""
    _http.check_url(url, allow_http=False)
    req = urllib.request.Request(url, headers={"User-Agent": "irlpls-evidence/0.1 (+https://irlpls.com)",
                                               "Accept": "*/*", "Accept-Encoding": "gzip"})
    try:
        with _http._open(req, timeout) as r:
            return _http._decode(_http._read_capped(r, max_bytes), r.headers, max_bytes)
    except urllib.error.HTTPError as e:
        raise _http.HTTPError(e.code, url, "") from None


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Ctx:
    """What a verifier sees: the card, the entry, the parameters, and lazy, recorded fetches of the pack's parts."""

    def __init__(self, card, where, entry, fetch, base_url, pack, first_body, options):
        self.card, self.where, self.entry, self.options = card, where, entry, options
        self.pack = pack or {}
        self.params = dict(self.pack.get("params") or {})
        self.base_url, self._fetch = base_url, fetch
        self.fetched, self._cache = [], {}
        self.notes, self.trust = [], None
        self.first_body = first_body

    def record(self, url, body):
        self.fetched.append({"url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                             "retrieved_at": _now_iso()})

    def url(self, name):
        u = self.pack.get(name)
        return urllib.parse.urljoin(self.base_url, u) if isinstance(u, str) and u else None

    def get_url(self, url):
        if url not in self._cache:
            try:
                body = self._fetch(url)
            except Exception as e:  # noqa: BLE001 - any fetch problem makes the check impossible, never a pass
                raise Unknown(f"could not fetch {url[:120]}: {str(e)[:160]}") from None
            self.record(url, body)
            self._cache[url] = body
        return self._cache[url]

    def get(self, name):
        """Bytes of the pack member `name` (artefact, proof, trust_anchor), or None when the pack has none."""
        u = self.url(name)
        return self.get_url(u) if u else None


def _json(b, what):
    try:
        v = json.loads(b.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        raise Fail(f"{what} is not JSON") from None
    return v


def _unhex(s, what="value"):
    if not isinstance(s, str):
        raise Fail(f"{what} is not a hex string")
    s = s[2:] if s.lower().startswith("0x") else s
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise Fail(f"{what} is not hex") from None


def _b64(s, what="value"):
    if not isinstance(s, str):
        raise Fail(f"{what} is not base64 text")
    s = s.strip()
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_" if ("-" in s or "_" in s) else None)
    except ValueError:
        raise Fail(f"{what} is not base64") from None


# ---------------------------------------------------------------- hashing (Merkle)

_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M64 = (1 << 64) - 1


def _rol(v, n):
    return ((v << n) | (v >> (64 - n))) & _M64 if n else v


def _keccak_f(s):
    for rc in _RC:
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        s = [s[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(s[x + 5 * y], _ROT[x][y])
        s = [b[i] ^ (~b[(i % 5 + 1) % 5 + 5 * (i // 5)] & b[(i % 5 + 2) % 5 + 5 * (i // 5)]) for i in range(25)]
        s[0] ^= rc
    return s


def _keccak(data, pad=0x01, rate=136, out=32):
    msg = bytearray(data) + bytes([pad]) + b"\x00" * ((-len(data) - 1) % rate)
    msg[-1] |= 0x80
    s = [0] * 25
    for off in range(0, len(msg), rate):
        for i in range(rate // 8):
            s[i] ^= int.from_bytes(msg[off + 8 * i:off + 8 * i + 8], "little")
        s = _keccak_f(s)
    return b"".join(x.to_bytes(8, "little") for x in s)[:out]


def keccak256(data):
    """Ethereum's Keccak-256 (original padding 0x01, not SHA3-256's 0x06)."""
    return _keccak(bytes(data))


MERKLE_HASHES = {"keccak256": keccak256, "sha256": lambda b: hashlib.sha256(b).digest()}
ODD_RULES = ("explicit", "duplicate", "promote")
PAIR_RULES = ("sorted", "ordered")


def merkle_root(leaf, proof, *, hash="sha256", pair="sorted", odd="explicit", index=None, tree_size=None):
    """Fold an inclusion proof from `leaf` (bytes) through `proof` (list of bytes) and return the root.

    pair: "sorted" hashes min||max of the two children; "ordered" hashes left||right by the index's bit.
    odd:  "explicit" every step is in the proof (a self-pair appears as a step equal to the node);
          "duplicate" an odd last node is hashed with itself and the proof omits that step;
          "promote" an odd last node moves up unchanged.
    `index` and `tree_size` are needed for "ordered", "duplicate" and "promote" (and used with "explicit" when given).
    Raises Fail when the proof is the wrong length for the tree."""
    if hash not in MERKLE_HASHES:
        raise Unknown(f"no Merkle hash {str(hash)[:20]!r} (known: {', '.join(MERKLE_HASHES)})")
    if pair not in PAIR_RULES:
        raise Unknown(f"pair rule must be one of {', '.join(PAIR_RULES)}")
    if odd not in ODD_RULES:
        raise Unknown(f"odd-node rule must be one of {', '.join(ODD_RULES)}")
    h = MERKLE_HASHES[hash]

    def combine(a, b, a_left):
        if pair == "sorted":
            return h(min(a, b) + max(a, b))
        return h(a + b) if a_left else h(b + a)

    steps, node = list(proof), leaf
    if index is None or tree_size is None:
        if pair != "sorted" or odd != "explicit":
            raise Unknown("this pair or odd-node rule needs the leaf index and the tree size")
        for s in steps:
            node = combine(node, s, True)
        return node
    i, n = int(index), int(tree_size)
    if not 0 <= i < n:
        raise Fail(f"leaf index {i} is outside a tree of {n}")
    while n > 1:
        if i == n - 1 and n % 2 == 1 and odd != "explicit":
            node = combine(node, node, True) if odd == "duplicate" else node
        else:
            if not steps:
                raise Fail("the proof ran out of steps before the root")
            node = combine(node, steps.pop(0), i % 2 == 0 or (i == n - 1 and n % 2 == 1))
        i, n = i // 2, (n + 1) // 2
    if steps:
        raise Fail(f"the proof has {len(steps)} step(s) left over after the root")
    return node


def verify_merkle(leaf, proof, root, **rules):
    """(True, "") when the proof folds to `root` under the given rules, else (False, reason)."""
    try:
        got = merkle_root(leaf, proof, **rules)
    except Fail as e:
        return False, str(e)
    if got != root:
        return False, f"proof folds to {got.hex()[:16]}..., not the root {root.hex()[:16]}..."
    return True, ""


def rfc6962_root(leaf_hash, index, tree_size, proof):
    """RFC 9162 Section 2.1.3.2: the root an inclusion proof implies. Raises Fail when the proof cannot fit."""
    if not 0 <= index < tree_size:
        raise Fail(f"log index {index} is outside a tree of {tree_size}")
    fn, sn, r = index, tree_size - 1, leaf_hash
    for p in proof:
        if sn == 0:
            raise Fail("the inclusion proof is longer than the tree allows")
        if fn & 1 or fn == sn:
            r = hashlib.sha256(b"\x01" + p + r).digest()
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn, sn = fn >> 1, sn >> 1
        else:
            r = hashlib.sha256(b"\x01" + r + p).digest()
        fn, sn = fn >> 1, sn >> 1
    if sn != 0:
        raise Fail("the inclusion proof is shorter than the tree needs")
    return r


# ---------------------------------------------------------------- DER (just enough for CMS / RFC 3161)

def _der(b, i=0):
    """(tag, full bytes, content bytes, next index) of the DER element at b[i]."""
    if i + 2 > len(b):
        raise Fail("truncated DER")
    tag, ln, j = b[i], b[i + 1], i + 2
    if tag & 0x1F == 0x1F:
        raise Fail("multi-byte DER tags are not supported")
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4 or j + n > len(b):
            raise Fail("bad DER length")
        ln, j = int.from_bytes(b[j:j + n], "big"), j + n
    if j + ln > len(b):
        raise Fail("truncated DER")
    return tag, bytes(b[i:j + ln]), bytes(b[j:j + ln]), j + ln


def _kids(content):
    out, i = [], 0
    while i < len(content):
        tag, full, c, i = _der(content, i)
        out.append((tag, full, c))
    return out


def _oid(c):
    if not c:
        raise Fail("empty OID")
    parts, v = [], 0
    for byte in c:
        v = (v << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(v)
            v = 0
    first = parts[0]
    head = [0, first] if first < 40 else ([1, first - 40] if first < 80 else [2, first - 80])
    return ".".join(map(str, head + parts[1:]))


def _int(c):
    return int.from_bytes(c, "big", signed=True)


def _gentime(c):
    s = c.decode("ascii").rstrip("Z")
    main, _, frac = s.partition(".")
    t = datetime.strptime(main, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return t.replace(microsecond=int((frac + "000000")[:6])) if frac else t


OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_TSTINFO = "1.2.840.113549.1.9.16.1.4"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"
OID_SIGNING_CERT = "1.2.840.113549.1.9.16.2.12"
OID_EKU_TIMESTAMPING = "1.3.6.1.5.5.7.3.8"
DIGESTS = {"1.3.14.3.2.26": "sha1", "2.16.840.1.101.3.4.2.1": "sha256", "2.16.840.1.101.3.4.2.2": "sha384",
           "2.16.840.1.101.3.4.2.3": "sha512"}
SIG_DIGESTS = {"1.2.840.113549.1.1.5": "sha1", "1.2.840.113549.1.1.11": "sha256", "1.2.840.113549.1.1.12": "sha384",
               "1.2.840.113549.1.1.13": "sha512", "1.2.840.10045.4.3.2": "sha256", "1.2.840.10045.4.3.3": "sha384",
               "1.2.840.10045.4.3.4": "sha512"}
OID_RSA, OID_ED25519 = "1.2.840.113549.1.1.1", "1.3.101.112"


def _crypto():
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    except ImportError:
        raise Unknown("signature checks need the 'cryptography' package") from None
    return x509, InvalidSignature, hashes, serialization, ec, ed25519, padding, rsa


def _hash_obj(name):
    _, _, hashes, *_ = _crypto()
    return {"sha1": hashes.SHA1, "sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}[name]()


def _verify_sig(public_key, signature, data, digest="sha256"):
    """Verify with any supported key type; raises Fail on a bad signature or an unsupported key."""
    x509, InvalidSignature, hashes, serialization, ec, ed25519, padding, rsa = _crypto()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(signature, data, padding.PKCS1v15(), _hash_obj(digest))
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, data, ec.ECDSA(_hash_obj(digest)))
        elif isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(signature, data)
        else:
            raise Unknown(f"unsupported key type {type(public_key).__name__}")
    except InvalidSignature:
        raise Fail("signature does not verify under the key") from None
    except ValueError as e:  # a malformed signature encoding
        raise Fail(f"signature does not verify: {str(e)[:80]}") from None


def _cert_time(cert, name):
    return getattr(cert, name + "_utc", None) or getattr(cert, name).replace(tzinfo=timezone.utc)


def _fingerprint(cert):
    _, _, hashes, serialization, *_ = _crypto()
    return cert.fingerprint(hashes.SHA256()).hex()


def _load_cert(b):
    x509, *_ = _crypto()
    try:
        return x509.load_pem_x509_certificate(b) if b.lstrip().startswith(b"-----") else x509.load_der_x509_certificate(b)
    except ValueError:
        raise Fail("not an X.509 certificate") from None


def _norm_fp(s):
    return s.replace(":", "").strip().lower()


def verify_rfc3161(token, data=None, *, digest=None, tsa_cert=None, trusted=()):
    """Verify an RFC 3161 TimeStampResp or TimeStampToken (DER).

    Checks: status granted; SignedData with a TSTInfo; the message imprint against `data` (or a precomputed `digest`
    of it); signedAttrs contentType and messageDigest; ESS signing-certificate hash when present; the CMS signature
    under the signer certificate (from the token, or `tsa_cert`); that certificate's validity at genTime and its
    timeStamping EKU. Returns {gen_time, imprint_alg, policy, serial, signer, cert_sha256, trust}; trust is "listed"
    when the signer certificate's SHA-256 is in `trusted`, else "unknown". Raises Fail (bad) or Unknown (cannot say).
    """
    x509, InvalidSignature, hashes, serialization, ec, ed25519, padding, rsa = _crypto()
    tag, _, top, _ = _der(token)
    if tag != 0x30:
        raise Fail("not a DER SEQUENCE")
    kids = _kids(top)
    if kids and kids[0][0] == 0x30 and _kids(kids[0][2]) and _kids(kids[0][2])[0][0] == 0x02:  # TimeStampResp
        status = _int(_kids(kids[0][2])[0][2])
        if status not in (0, 1):
            raise Fail(f"time-stamp response status {status} (not granted)")
        if len(kids) < 2:
            raise Fail("time-stamp response carries no token")
        kids = _kids(kids[1][2])
    if len(kids) < 2 or kids[0][0] != 0x06 or _oid(kids[0][2]) != OID_SIGNED_DATA or kids[1][0] != 0xA0:
        raise Fail("not a CMS SignedData token")
    sd = _kids(_kids(kids[1][2])[0][2])
    encap = _kids(sd[2][2])
    if _oid(encap[0][2]) != OID_TSTINFO or len(encap) < 2:
        raise Fail("the token does not carry a TSTInfo")
    tst_der = _kids(encap[1][2])[0][2]
    tst = _kids(_der(tst_der)[2])
    policy = _oid(tst[1][2])
    mi = _kids(tst[2][2])
    alg = DIGESTS.get(_oid(_kids(mi[0][2])[0][2]))
    hashed = mi[1][2]
    serial = _int(tst[3][2])
    gen_time = _gentime(tst[4][2])
    if alg is None:
        raise Unknown("message imprint uses an unknown hash algorithm")
    if data is not None:
        digest = hashlib.new(alg, data).digest()
    if digest is None:
        raise Unknown("no artefact to compare the message imprint with")
    if digest != hashed:
        raise Fail(f"message imprint ({alg}) does not match the artefact")
    rest = sd[3:]
    certs = []
    for t, full, c in rest:
        if t == 0xA0:
            certs = [_load_cert(f) for _, f, _ in _kids(c)]
    if tsa_cert is not None:
        certs.append(tsa_cert)
    signer_infos = [k for k in rest if k[0] == 0x31]
    if not signer_infos or not _kids(signer_infos[-1][2]):
        raise Fail("the token has no SignerInfo")
    si = _kids(_kids(signer_infos[-1][2])[0][2])
    sid, si_digest = si[1], DIGESTS.get(_oid(_kids(si[2][2])[0][2]))
    cert = None
    for c in certs:
        if sid[0] == 0x30:
            issuer, ser = _kids(sid[2])
            if c.serial_number == _int(ser[2]) and c.issuer.public_bytes() == issuer[1]:
                cert = c
        elif sid[0] == 0x80:
            try:
                ski = c.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            except x509.ExtensionNotFound:
                continue
            if ski == sid[2]:
                cert = c
    if cert is None:
        raise Unknown("the signer's certificate is not in the token; give the TSA certificate as the trust anchor")
    if si[3][0] != 0xA0:
        raise Fail("the SignerInfo has no signed attributes")
    attrs = {}
    for _, _, a in _kids(si[3][2]):
        ak = _kids(a)
        attrs[_oid(ak[0][2])] = _kids(ak[1][2])
    if si_digest is None:
        raise Unknown("the SignerInfo uses an unknown digest algorithm")
    ct = attrs.get(OID_CONTENT_TYPE)
    if not ct or _oid(ct[0][2]) != OID_TSTINFO:
        raise Fail("signed contentType is not TSTInfo")
    md = attrs.get(OID_MESSAGE_DIGEST)
    if not md or md[0][2] != hashlib.new(si_digest, tst_der).digest():
        raise Fail("signed messageDigest does not match the TSTInfo")
    for oid, default in ((OID_SIGNING_CERT_V2, "sha256"), (OID_SIGNING_CERT, "sha1")):
        if oid in attrs:
            ids = _kids(_kids(attrs[oid][0][2])[0][2])
            first = _kids(ids[0][2])
            h_alg = default
            if first[0][0] == 0x30:  # ESSCertIDv2 with an explicit hashAlgorithm
                h_alg = DIGESTS.get(_oid(_kids(first[0][2])[0][2]), "?")
                first = first[1:]
            if h_alg not in ("sha1", "sha256", "sha384", "sha512") or \
                    first[0][2] != hashlib.new(h_alg, cert.public_bytes(serialization.Encoding.DER)).digest():
                raise Fail("the ESS signing-certificate hash does not match the signer's certificate")
            break
    sig_alg = _oid(_kids(si[4][2])[0][2])
    signed = b"\x31" + si[3][1][1:]
    _verify_sig(cert.public_key(), si[5][2], signed, SIG_DIGESTS.get(sig_alg, si_digest))
    nb, na = _cert_time(cert, "not_valid_before"), _cert_time(cert, "not_valid_after")
    if not nb <= gen_time <= na:
        raise Fail(f"genTime {gen_time:%Y-%m-%dT%H:%M:%SZ} is outside the TSA certificate's validity "
                   f"({nb:%Y-%m-%d} to {na:%Y-%m-%d})")
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if OID_EKU_TIMESTAMPING not in [o.dotted_string for o in eku]:
            raise Fail("the TSA certificate lacks the timeStamping extended key usage (RFC 3161 2.3)")
    except x509.ExtensionNotFound:
        raise Fail("the TSA certificate has no extended key usage; RFC 3161 2.3 requires timeStamping") from None
    fp = _fingerprint(cert)
    trust = "listed" if fp in {_norm_fp(t) for t in trusted} else "unknown"
    return {"gen_time": gen_time.strftime("%Y-%m-%dT%H:%M:%SZ"), "imprint_alg": alg, "policy": policy,
            "serial": serial, "signer": cert.subject.rfc4514_string(), "cert_sha256": fp, "trust": trust}


# ---------------------------------------------------------------- verifiers

def _origin(url):
    u = urllib.parse.urlsplit(url or "")
    if not u.scheme or not u.hostname:
        return None
    port = u.port
    default = {"https": 443, "http": 80}.get(u.scheme.lower())
    return f"{u.scheme.lower()}://{u.hostname.lower()}" + (f":{port}" if port and port != default else "")


def _jwks_obj(b):
    j = _json(b, "the key set")
    if not isinstance(j, dict) or not isinstance(j.get("keys"), list):
        raise Fail("the key set has no keys list")
    return j


def v_jws_receipt(ctx):
    from .protocol import receipts
    proof = ctx.get("proof") if ctx.pack else ctx.first_body
    if proof is None:
        raise Unknown("the pack has no proof (the sample receipt)")
    receipt = _json(proof, "the receipt")
    card_jwks = ((ctx.card.get("protocol") or {}).get("jwks_uri"))
    anchor = ctx.url("trust_anchor")
    if card_jwks and anchor and anchor != card_jwks:
        raise Fail(f"the key set {anchor[:80]} is not the card's protocol.jwks_uri; STANDARD 14.7 takes keys only "
                   "from jwks_uri")
    jwks_url = card_jwks or anchor or ctx.params.get("jwks")
    if not jwks_url:
        raise Unknown("no key set: the card has no protocol.jwks_uri and the pack names no trust_anchor")
    if not card_jwks:
        ctx.notes.append("key set taken from the sample pack, since the card has no protocol.jwks_uri")
    jwks = _jwks_obj(ctx.get_url(jwks_url))
    iss = _origin(ctx.card.get("provider_url"))
    artefact = ctx.get("artefact")
    try:
        payload = receipts.verify_receipt(receipt, jwks, iss=iss, media_bytes=artefact)
    except receipts.ReceiptError as e:
        raise Fail(f"receipt: {e}") from None
    except RuntimeError as e:
        raise Unknown(str(e)) from None
    if artefact is not None and (payload.get("media") or {}).get("bytes") != len(artefact):
        raise Fail("receipt: media.bytes does not match the artefact's size")
    out = ["signature verifies under a key at " + jwks_url[:80], f"iss {payload.get('iss')} is the provider's origin"
           if iss else "iss not compared (the card has no provider_url)"]
    out.append("media hash and size match the artefact" if artefact is not None else
               "no artefact in the pack, so the media hash was not recomputed")
    out.append("nonce and request_sha256 are present; a sample cannot show they match a live order")
    out.append("shows who signed what, not that it is true; the signer is the provider")
    return VERIFIED, out


def v_jwks(ctx):
    from .protocol import receipts
    body = ctx.get("trust_anchor") if ctx.pack else ctx.first_body
    if body is None:
        raise Unknown("the pack has no trust_anchor (the key set)")
    jwks = _jwks_obj(body)
    good = []
    for k in receipts._keys(jwks):
        try:
            receipts._public_key(k)
        except (receipts.ReceiptError, RuntimeError) as e:
            raise Fail(f"key {str(k.get('kid'))[:40]!r}: {e}") from None
        if k.get("irlpls_use") == "receipt" and k.get("alg") in receipts.ALLOWED_ALGS:
            good.append(k.get("kid"))
    sample = ctx.url("proof")
    if sample:
        return v_jws_receipt(ctx)
    note = f"{len(good)} receipt key(s) parse" if good else "no key marked irlpls_use receipt with ES256 or EdDSA"
    raise Unknown(f"{note}; a key set alone shows no signature: publish a sample receipt beside it")


def v_rfc3161(ctx):
    token = ctx.get("proof") if ctx.pack else ctx.first_body
    if token is None:
        raise Unknown("the pack has no proof (the time-stamp token)")
    data = ctx.get("artefact")
    digest = None
    if data is None and ctx.params.get("imprint"):
        digest = _unhex(ctx.params["imprint"], "params.imprint")
        ctx.notes.append("imprint compared with params.imprint, not with a fetched artefact")
    anchor = ctx.get("trust_anchor")
    tsa_cert = _load_cert(anchor) if anchor else None
    r = verify_rfc3161(token, data, digest=digest, tsa_cert=tsa_cert, trusted=ctx.options.get("tsa_trust") or ())
    ctx.trust = "TSA on the configured trust list" if r["trust"] == "listed" else \
        "TSA trust-list status unknown (not on a configured list)"
    return VERIFIED, [f"message imprint ({r['imprint_alg']}) matches the artefact" if data is not None or digest
                      else "", f"TSA signature verifies ({r['signer'][:80]})",
                      f"existed no later than {r['gen_time']} (genTime)",
                      "shows the bytes existed by then, not when or where they were captured"]


def _note_text(cp):
    text, sep, sigs = cp.partition("\n\n")
    if not sep:
        raise Fail("the checkpoint has no signature block")
    return (text + "\n").encode(), [ln for ln in sigs.splitlines() if ln.startswith("— ")]


def v_rekor(ctx):
    raw = ctx.get("proof") if ctx.pack else ctx.first_body
    if raw is None:
        raise Unknown("the pack has no proof (the Rekor entry)")
    j = _json(raw, "the Rekor entry")
    entry = next(iter(j.values())) if isinstance(j, dict) and len(j) == 1 and isinstance(next(iter(j.values())), dict) \
        and "body" in next(iter(j.values())) else j
    if not isinstance(entry, dict) or "body" not in entry:
        raise Fail("not a Rekor log entry (no body)")
    proof = (entry.get("verification") or {}).get("inclusionProof")
    if not proof:
        raise Unknown("no inclusion proof in the entry: not checkable offline")
    body = _b64(entry["body"], "body")
    leaf = hashlib.sha256(b"\x00" + body).digest()
    root = rfc6962_root(leaf, int(proof["logIndex"]), int(proof["treeSize"]), [_unhex(h, "proof hash") for h in proof.get("hashes", [])])
    if root != _unhex(proof["rootHash"], "rootHash"):
        raise Fail("the inclusion proof does not fold to the stated rootHash")
    out = [f"inclusion proof folds to rootHash at tree size {proof['treeSize']}"]
    artefact = ctx.get("artefact")
    if artefact is not None:
        try:
            spec = json.loads(body)["spec"]
            want = spec["data"]["hash"]["value"]
        except (ValueError, KeyError, TypeError):
            raise Unknown("the entry body is not a hashedrekord with spec.data.hash; cannot tie it to the artefact") from None
        if want.lower() != hashlib.sha256(artefact).hexdigest():
            raise Fail("the entry's spec.data.hash is not the artefact's SHA-256")
        out.append("the entry's hash is the artefact's SHA-256")
    else:
        out.append("no artefact in the pack, so the entry is not tied to a file")
    key_pem = ctx.get("trust_anchor")
    cp = proof.get("checkpoint")
    if key_pem is None:
        raise Unknown("; ".join(out) + "; but no Rekor public key was given, so the root itself is unauthenticated")
    _, _, _, serialization, *_ = _crypto()
    try:
        key = serialization.load_pem_public_key(key_pem)
    except ValueError:
        raise Fail("the trust anchor is not a PEM public key") from None
    if not cp:
        raise Unknown("; ".join(out) + "; no checkpoint, so the root is unauthenticated")
    text, sig_lines = _note_text(cp)
    lines = text.decode().splitlines()
    if len(lines) < 3 or lines[1] != str(proof["treeSize"]) or _b64(lines[2], "checkpoint root") != root:
        raise Fail("the checkpoint's size or root differs from the inclusion proof")
    ok = False
    for ln in sig_lines:
        sig = _b64(ln.rsplit(" ", 1)[-1], "checkpoint signature")[4:]
        try:
            _verify_sig(key, sig, text)
            ok = True
            break
        except Fail:
            continue
    if not ok:
        raise Fail("no checkpoint signature verifies under the Rekor key")
    out.append("checkpoint signed by the log key")
    set_b64 = (entry.get("verification") or {}).get("signedEntryTimestamp")
    if set_b64:
        from .protocol.receipts import jcs
        msg = jcs({"body": entry["body"], "integratedTime": entry.get("integratedTime"),
                   "logID": entry.get("logID"), "logIndex": entry.get("logIndex")})
        _verify_sig(key, _b64(set_b64, "signedEntryTimestamp"), msg)
        t = datetime.fromtimestamp(int(entry.get("integratedTime") or 0), timezone.utc)
        out.append(f"signed entry timestamp verifies: logged no later than {t:%Y-%m-%dT%H:%M:%SZ}")
    out.append("offline: the log's consistency over time was not checked")
    return VERIFIED, out


def v_c2pa(ctx):
    tool = ctx.options.get("c2patool", shutil.which("c2patool"))
    if not tool:
        raise Unknown("tool missing: install c2patool to validate C2PA manifests")
    art = ctx.get("artefact") if ctx.pack else ctx.first_body
    if art is None:
        raise Unknown("the pack has no artefact (the signed media file)")
    ext = os.path.splitext(urllib.parse.urlsplit(ctx.url("artefact") or ctx.entry.get("evidence_url", "")).path)[1] or ".jpg"
    d = tempfile.mkdtemp(prefix="irlpls-c2pa-")
    try:
        p = os.path.join(d, "artefact" + ext[:8])
        with open(p, "wb") as f:
            f.write(art)
        args = [tool, p]
        anchors = ctx.options.get("c2pa_trust_anchors")
        if anchors:
            args += ["trust", "--trust_anchors", anchors]
        try:
            r = subprocess.run(args, capture_output=True, timeout=60, cwd=d)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unknown(f"c2patool did not run: {str(e)[:120]}") from None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    try:
        report = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip()[:160]
        raise Fail(f"c2patool found no valid manifest: {msg}") from None
    state = report.get("validation_state")
    codes = [s.get("code") for s in report.get("validation_status") or [] if isinstance(s, dict)]
    if state is None:
        state = "Invalid" if codes else "Valid"
    if state == "Invalid":
        raise Fail("C2PA validation state Invalid" + (f" ({', '.join(map(str, codes[:4]))})" if codes else ""))
    if state == "Trusted":
        ctx.trust = "C2PA Trust List (as configured for c2patool)"
        return VERIFIED, ["C2PA validation state Trusted", "signed by a listed signer; not proof the scene is real"]
    ctx.trust = "not trusted: no Trust List match, so it counts as claimed (STANDARD 3)"
    return VERIFIED, [f"C2PA validation state {state}: the manifest is intact, the signer is not on a trust list"]


def v_merkle(ctx):
    raw = ctx.get("proof") if ctx.pack else ctx.first_body
    if raw is None:
        raise Unknown("the pack has no proof (the Merkle proof)")
    p = _json(raw, "the Merkle proof")
    if not isinstance(p, dict):
        raise Fail("the Merkle proof is not an object")
    rules = dict(p.get("rules") or {}, **ctx.params)
    if rules.get("odd") not in ODD_RULES:
        raise Unknown("odd-node rule not declared (explicit, duplicate or promote); a verifier cannot tell how the "
                      "tree treats an odd node")
    h = rules.get("hash", "sha256")
    if h not in MERKLE_HASHES:
        raise Unknown(f"no verifier for Merkle hash {str(h)[:20]!r}")
    leaf = _unhex(p.get("leaf"), "leaf")
    out = []
    artefact = ctx.get("artefact")
    enc = rules.get("leaf")
    if artefact is not None and enc in ("hash(artefact)", "sha256(artefact)", "keccak256(artefact)"):
        fn = MERKLE_HASHES["sha256" if enc.startswith("sha256") else "keccak256" if enc.startswith("keccak") else h]
        if fn(artefact) != leaf:
            raise Fail(f"the leaf is not {enc} of the artefact")
        out.append(f"leaf = {enc}")
    else:
        out.append("leaf encoding not declared or no artefact: the leaf is not tied to a file")
    ok, why = verify_merkle(leaf, [_unhex(s, "proof step") for s in p.get("proof") or p.get("siblings") or []],
                            _unhex(p.get("root"), "root"), hash=h, pair=rules.get("pair", "sorted"),
                            odd=rules["odd"], index=p.get("index"), tree_size=p.get("tree_size"))
    if not ok:
        raise Fail(f"Merkle proof ({h}, {rules.get('pair', 'sorted')} pairs, odd={rules['odd']}): {why}")
    out.insert(0, f"proof folds to the root ({h}, {rules.get('pair', 'sorted')} pairs, odd nodes: {rules['odd']})")
    out.append("the root's anchor (chain, transaction, log) is not checked offline")
    return VERIFIED, out


def v_x509_signature(ctx):
    raw = ctx.get("proof") if ctx.pack else ctx.first_body
    if raw is None:
        raise Unknown("the pack has no proof (the signature block)")
    p = _json(raw, "the signature block")
    if isinstance(p, list) and p and isinstance(p[0], dict):  # a list of signed records (CDSE traces): check the first
        if len(p) > 1:
            ctx.notes.append(f"{len(p)} signed records; the first was checked")
        p = p[0]
    if isinstance(p, dict) and isinstance(p.get("signature"), dict):  # a record {signature: {...}, timestamp}
        p = dict(p["signature"], signed_at=p["signature"].get("signed_at") or p.get("timestamp"))
    if not isinstance(p, dict) or not p.get("signature") or not p.get("certificate"):
        raise Fail("the signature block needs signature and certificate")
    data = ctx.get("artefact")
    signed = p.get("signed") if isinstance(p.get("signed"), str) else p.get("message")
    if data is None and isinstance(signed, str):
        data = signed.encode()
    if data is None:
        raise Unknown("no artefact: the signed bytes are not in the pack")
    cert_txt = p["certificate"]
    cert = _load_cert(cert_txt.encode() if cert_txt.lstrip().startswith("-----") else _b64(cert_txt, "certificate"))
    alg = str(p.get("algorithm", "SHA256")).upper()
    digest = "sha384" if "384" in alg else "sha512" if "512" in alg else "sha256"
    _verify_sig(cert.public_key(), _b64(p["signature"], "signature"), data, digest)
    from .protocol.receipts import parse_rfc3339
    at = parse_rfc3339(p.get("signed_at"))
    if at is None:
        raise Unknown("signature verifies, but no signed_at time to check the certificate against")
    nb, na = _cert_time(cert, "not_valid_before"), _cert_time(cert, "not_valid_after")
    if at > na:
        raise Fail(f"signed {p['signed_at']}, after the certificate expired on {na:%Y-%m-%d}: invalid at signing time")
    if at < nb:
        raise Fail(f"signed {p['signed_at']}, before the certificate was valid ({nb:%Y-%m-%d})")
    out = ["signature verifies under the certificate's key", f"certificate valid at the signing time {p['signed_at']}"]
    anchor = ctx.get("trust_anchor")
    self_signed = cert.issuer == cert.subject
    if anchor:
        ca = _load_cert(anchor)
        try:
            cert.verify_directly_issued_by(ca)
        except Exception as e:  # noqa: BLE001 - cryptography raises several types here
            raise Fail(f"the certificate was not issued by the trust anchor: {str(e)[:80]}") from None
        ctx.trust = f"issued by the given anchor {ca.subject.rfc4514_string()[:60]}; revocation not checked"
    else:
        ctx.trust = ("self-signed: shows only that this key's holder signed" if self_signed
                     else "chain not checked (no trust anchor in the pack); revocation not checked")
    return VERIFIED, out


def v_detached_signature(ctx):
    sig = ctx.get("proof") if ctx.pack else None
    key_pem, data = ctx.get("trust_anchor"), ctx.get("artefact")
    if sig is None or key_pem is None or data is None:
        raise Unknown("a detached signature needs the artefact, the signature (proof) and the public key (trust_anchor)")
    _, _, _, serialization, *_ = _crypto()
    try:
        key = serialization.load_pem_public_key(key_pem)
    except ValueError:
        raise Fail("the trust anchor is not a PEM public key") from None
    if ctx.params.get("signature_encoding") == "base64":
        sig = _b64(sig.decode("ascii", "replace"), "signature")
    try:
        _verify_sig(key, sig, data, ctx.params.get("digest", "sha256"))
    except Fail:
        raise Fail("the signature does not verify under the published key (a different key signed it, or the file "
                   "changed)") from None
    return VERIFIED, ["signature verifies under the published key",
                      "shows the file is unchanged since the key holder signed it, not what it depicts"]


VERIFIERS = {"jws-receipt": v_jws_receipt, "jwks": v_jwks, "rfc3161": v_rfc3161, "rekor": v_rekor, "c2pa": v_c2pa,
             "merkle": v_merkle, "x509-signature": v_x509_signature, "detached-signature": v_detached_signature}


# ---------------------------------------------------------------- inference and orchestration

def sniff(body):
    """Best guess at what un-described evidence is, or None (a web page, or anything unknown)."""
    head = body[:64].lstrip()
    if body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n" or body[4:8] == b"ftyp":
        return "c2pa"
    if head[:1] in (b"{", b"["):
        try:
            j = json.loads(body.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(j, dict):
            if isinstance(j.get("keys"), list):
                return "jwks"
            if "jws" in j and "payload" in j:
                return "jws-receipt"
            vals = list(j.values())
            if "body" in j or (len(vals) == 1 and isinstance(vals[0], dict) and "body" in vals[0]):
                return "rekor"
            if "root" in j and ("proof" in j or "siblings" in j):
                return "merkle"
            if "signature" in j and "certificate" in j:
                return "x509-signature"
        rec = j[0] if isinstance(j, list) and j else j
        if isinstance(rec, dict) and isinstance(rec.get("signature"), dict) and "certificate" in rec["signature"]:
            return "x509-signature"
        return None
    if body[:1] == b"\x30":
        return "rfc3161"
    return None


def is_pack(obj):
    return isinstance(obj, dict) and "irlpls_sample_pack" in obj


def entries(card):
    """[(where, entry)] for every guarantee and provenance entry with an evidence_url."""
    out = []
    for group, keys in (("verification", _validate.GUARANTEES), ("provenance", _validate.PROVENANCE)):
        for k in keys:
            e = (card.get(group) or {}).get(k)
            if isinstance(e, dict) and isinstance(e.get("evidence_url"), str) and e["evidence_url"]:
                out.append((f"{group}.{k}", e))
    return out


def check_entry(card, where, entry, fetch=None, **options):
    """Run the checks for one entry. Returns the result dict (see the module docstring). Never raises for bad evidence."""
    fetch = fetch or fetch_bytes
    url = entry["evidence_url"]
    res = {"where": where, "level": entry.get("level"), "evidence_url": url, "type": None, "inferred": False,
           "sample_pack": False, "verdict": NOT_CHECKABLE, "reasons": [], "trust": None, "fetched": []}
    ctx = _Ctx(card, where, entry, fetch, url, None, None, options)
    try:
        body = ctx.get_url(url)
        pack = None
        if body.lstrip()[:1] == b"{":
            try:
                j = json.loads(body.decode("utf-8-sig"))
                pack = j if is_pack(j) else None
            except (ValueError, UnicodeDecodeError):
                pass
        if pack is not None:
            res["sample_pack"] = True
            typ = pack.get("type")
            if typ not in VERIFIERS:
                raise Unknown(f"the sample pack's type {str(typ)[:30]!r} has no verifier (known: {', '.join(VERIFIERS)})")
            ctx.pack, ctx.params = pack, dict(pack.get("params") or {})
        else:
            typ = options.get("type") or sniff(body)
            res["inferred"] = typ is not None and not options.get("type")
            if typ is None:
                kind = "JSON with no recognised proof shape" if body.lstrip()[:1] in (b"{", b"[") else \
                    "a page or file with no checkable artefact"
                raise Unknown(f"the URL is {kind}; publish a sample pack "
                              "(irlpls-sample.json: artefact, proof, trust anchor, README; STANDARD 3.1 note)")
            ctx.first_body = body
            ctx.notes.append(f"type inferred from the content ({typ}); a sample pack would declare it")
        res["type"] = typ
        verdict, reasons = VERIFIERS[typ](ctx)
        res["verdict"], res["reasons"] = verdict, [r for r in reasons if r]
    except Fail as e:
        res["verdict"], res["reasons"] = FAILED, [str(e)]
    except Unknown as e:
        res["verdict"], res["reasons"] = NOT_CHECKABLE, [str(e)]
    except (KeyError, IndexError, TypeError, ValueError) as e:  # malformed evidence is a failure, never a crash
        res["verdict"], res["reasons"] = FAILED, [f"malformed evidence: {type(e).__name__}: {str(e)[:100]}"]
    res["reasons"] += ctx.notes
    res["trust"], res["fetched"] = ctx.trust, ctx.fetched
    if res["level"] == "evidenced" and res["verdict"] == VERIFIED and not res["sample_pack"]:
        res["reasons"].append("no sample pack: other verifiers have to guess what this evidence is")
    return res


def check_card(card, fetch=None, **options):
    """Check every entry with an evidence_url. Returns a list of result dicts. The card is never modified."""
    return [check_entry(card, w, e, fetch=fetch, **options) for w, e in entries(card)]


def load_trust(path):
    """SHA-256 certificate fingerprints, one per line ('#' comments allowed)."""
    if not path:
        return ()
    with open(path) as f:
        return tuple(_norm_fp(ln.split("#")[0]) for ln in f if ln.split("#")[0].strip())


def options_from_env(tsa_trust=None):
    return {"tsa_trust": load_trust(tsa_trust or os.environ.get("IRLPLS_TSA_TRUST")),
            "c2pa_trust_anchors": os.environ.get("IRLPLS_C2PA_TRUST_ANCHORS")}


def render(results):
    lines = []
    for r in results:
        lines.append(f"[{r['verdict'].upper()}] {r['where']} ({r['level']}, {r['type'] or 'unknown type'}"
                     f"{', sample pack' if r['sample_pack'] else ''}): {r['evidence_url']}")
        lines += [f"    - {x}" for x in r["reasons"]]
        if r["trust"]:
            lines.append(f"    trust: {r['trust']}")
        lines += [f"    fetched {f['url'][:90]} sha256={f['sha256'][:16]}... {f['bytes']} B at {f['retrieved_at']}"
                  for f in r["fetched"]]
    return "\n".join(lines) or "no evidence_url on this card"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="irlpls.evidence", description="Check every evidence_url on a Capture Card.")
    ap.add_argument("card", help="card file path or registry card id")
    ap.add_argument("--tsa-trust", help="file of trusted TSA certificate SHA-256 fingerprints")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    a = ap.parse_args(argv)
    card = load_card(a.card)
    if card is None:
        print(f"no card file or registry id {a.card!r}", file=sys.stderr)
        return 2
    res = check_card(card, **options_from_env(a.tsa_trust))
    print(json.dumps(res, indent=2) if a.json else render(res))
    return 1 if any(r["verdict"] == FAILED for r in res) else 0


def load_card(target):
    """A card dict from a file path or a registry id (cards/<id>.json), else None."""
    import pathlib
    p = pathlib.Path(target)
    if not p.is_file():
        if not target.replace("-", "").replace("_", "").isalnum():
            return None
        p = pathlib.Path(__file__).resolve().parent.parent / "cards" / f"{target}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
