"""Validate every JSON example in PROTOCOL-DRAFT.md against schema/protocol/*, then check the hashes and the receipt.

    uv run --with jsonschema --with cryptography python docs/protocol/check_examples.py

Each example is the ```json block after a marker line:
    <!-- validate: <schema file id>[#<pointer>] [@<json pointer into the example>] -->
    <!-- validate: none -->
Also: the quote's request_sha256 is the RFC 8785 hash of the quote request's `request`; every Order example's
request_sha256 equals its quote's (accepted offer) or the hash of its direct order's request; the example receipt
verifies with the reference verifier (irlpls.protocol.receipts.verify_receipt), kid read from the protected header,
against the example JWK Set, with iss, order, nonce, request hash and the item bindings (media, time, place).
"""
import base64
import json
import pathlib
import re
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from irlpls.protocol.receipts import ReceiptError, jcs, request_sha256, verify_receipt  # noqa: E402

DOC = ROOT / "docs/protocol/PROTOCOL-DRAFT.md"
BASE = "https://irlpls.com/2026-10-26/schemas/"
ISS = "https://streetlens.example"


def registry():
    reg = Registry()
    for f in list((ROOT / "schema").glob("*.schema.json")) + list((ROOT / "schema/protocol").glob("*.schema.json")):
        s = json.loads(f.read_text())
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return reg


def pointer(doc, ptr):
    for part in [p for p in ptr.split("/") if p]:
        doc = doc[part.replace("~1", "/").replace("~0", "~")]
    return doc


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def main():
    reg = registry()
    blocks = re.findall(r"<!-- validate: ([^>]*?) -->\s*```json\n(.*?)```", DOC.read_text(), re.S)
    fails, n, examples = 0, 0, []

    def check(ok, what):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + what)
        fails += not ok

    for spec, body in blocks:
        ex = json.loads(body)
        examples.append(ex)
        if spec.strip() == "none":
            continue
        target, _, at = spec.partition(" @")
        ref = target if target.startswith("http") else BASE + "protocol/" + target
        v = Draft202012Validator({"$ref": ref}, registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER)
        inst = pointer(ex, at.strip()) if at.strip() else ex
        errs = sorted(v.iter_errors(inst), key=lambda e: list(e.path))
        n += 1
        check(not errs, f"{target}{' @' + at.strip() if at.strip() else ''}"
              + ("" if not errs else ": " + "; ".join(f"{list(e.path)}: {e.message[:160]}" for e in errs[:5])))

    # request hashes: the quote, every Order (accepted offer -> the quote's; direct -> its own request)
    qr = next(e for e in examples if "request" in e and "proof" in e.get("constraints", {}))
    quote = next(e for e in examples if "offers" in e)
    want = request_sha256(qr["request"])
    check(quote["request_sha256"] == want, "quote request_sha256 is the RFC 8785 hash of the quote request")
    direct = next(e for e in examples if "request" in e and "nonce" in e)
    orders = [e for e in examples if "state" in e and "id" in e]
    for o in orders:
        exp = want if o.get("quote_id") == quote["id"] else request_sha256(direct["request"])
        check(o.get("request_sha256") == exp, f"order {o['id']} ({o['state']}) request_sha256 matches the request it priced")

    # the receipt, with the reference verifier
    d = next(e for e in examples if isinstance(e.get("items"), list) and e["items"] and "receipt" in e["items"][0])
    item = d["items"][0]
    rc = item["receipt"]
    jwks = next(e for e in examples if "keys" in e)
    order_req = next(e for e in examples if e.get("quote_id") == quote["id"] and "nonce" in e)
    hdr = json.loads(b64d(rc["jws"].split(".")[0]))
    check("kid" not in rc and isinstance(hdr.get("kid"), str), f"kid only in the protected header ({hdr.get('kid')})")
    try:
        verify_receipt(rc, jwks, iss=ISS, order_id=d["order_id"], nonce=order_req["nonce"], request_sha256=want,
                       media_sha256=item["sha256"], item=item)
        ok, why = True, ""
    except (ReceiptError, RuntimeError) as e:
        ok, why = False, f": {e}"
    check(ok, "receipt verifies (signature, key, iss, order, nonce, request hash, media, time, place)" + why)
    moved = dict(item, lat=51.5)
    try:
        verify_receipt(rc, jwks, item=moved)
        check(False, "a moved item fails the receipt")
    except ReceiptError:
        check(True, "a moved item fails the receipt")
    print(f"{n} examples validated, {fails} failures")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
