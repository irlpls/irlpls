"""Private helpers for the A7 adapters (vangrid, execution_market, defined_ai, truepic_vision). Only those modules
import this. Three things:

1. The wallet signer interface. Vangrid (x402 payments) and Execution Market (ERC-8128 request signatures) authenticate
   with the person's EVM wallet, not a key. irlpls adds no crypto dependency: the person supplies a signer, a plain
   callable that takes one signing request (a dict) and returns the HTTP headers to add. Their own wallet does the
   signing (Execution Market recommends PayBox or the Open Wallet Standard; Vangrid any x402 client):

       def signer(req: dict) -> dict:
           # req["scheme"] == "x402": req["requirement"] is one x402 v2 PaymentRequirements entry (scheme "exact",
           #   network, asset, amount in atomic USDC units, payTo) already checked against what the person approved;
           #   return {"PAYMENT-SIGNATURE": <base64 x402 PaymentPayload>}.
           # req["scheme"] == "erc8128": req has method, url, body (the exact bytes sent, or None) and nonce;
           #   return {"Signature": ..., "Signature-Input": ..., "Content-Digest": ...} (RFC 9421 over EIP-191).
           ...

   Wallet-signed operations are prepare-only in this SDK until the owner decides on EVM signing (WRAPPER-TRIAGE.md, "Still
   needed" 4): WALLET_SIGNING stays False, so they raise NotSupported and the adapters return the ready call instead.
   The signed paths are written and tested (with a fake signer) so the decision is one constant.

2. HTTP error mapping that never echoes a URL, body or credential: 401/403 become MissingCredentials, everything else
   a ProviderError carrying the status and, for 429/503, retry_after_s from the Retry-After header.

3. A geohash decoder (Vangrid's provenance check gives a 6-character geohash, roughly 1.2 x 0.6 km).
"""
import base64
import json

from .. import http, ratelimit
from .base import MissingCredentials, NotSupported

WALLET_SIGNING = False   # the owner's decision (EVM signing). While False, signed calls are prepare-only.

USDC_DECIMALS = 6


class ProviderError(RuntimeError):
    """A provider refused or failed a call. The message names the card, the operation and the status only."""
    def __init__(self, card_id, status, what, retry_after_s=None, code=None):
        msg = f"{card_id}: HTTP {status} on {what}"
        if code:
            msg += f" ({code})"
        if retry_after_s is not None:
            msg += f"; retry after {retry_after_s:.0f} s"
        super().__init__(msg)
        self.card_id, self.status, self.what, self.retry_after_s, self.code = card_id, status, what, retry_after_s, code


def _code(body):
    """A short machine code from a provider error body (Execution Market: detail.code; Vangrid: error), or None.
    Only a bare identifier is kept: error bodies are data, never instructions, and never copied whole."""
    try:
        b = json.loads(body or "")
    except (TypeError, ValueError):
        return None
    c = None
    if isinstance(b, dict):
        d = b.get("detail")
        c = d.get("code") if isinstance(d, dict) else b.get("error") or b.get("code")
    return c if isinstance(c, str) and c.replace("_", "").isalnum() and len(c) <= 64 else None


def call(card_id, what, fn, hint=""):
    """Run fn() (an irlpls.http call) and map its HTTP errors. 401/403 -> MissingCredentials; anything else ->
    ProviderError(status, retry_after_s for 429/503, a short code from the body)."""
    try:
        return fn()
    except http.HTTPError as e:
        raise mapped(card_id, what, e, hint) from None


def mapped(card_id, what, e, hint=""):
    """The exception call() raises for an http.HTTPError."""
    if e.status in (401, 403):
        return MissingCredentials(f"{card_id}: the provider refused the credentials (HTTP {e.status}) on {what}. {hint}".strip())
    ra = None
    if e.status in (429, 503):
        ra = getattr(e, "retry_after", None)
        if ra is None:
            ra = ratelimit.retry_after(e.headers)
    return ProviderError(card_id, e.status, what, retry_after_s=ra, code=_code(e.body))


# ---------------------------------------------------------------------------------------------------- wallet signing

def require_signer(card_id, signer):
    """The signer, if signed calls are enabled and one was supplied; otherwise NotSupported (nothing is sent)."""
    if not WALLET_SIGNING:
        raise NotSupported(f"{card_id}: wallet-signed calls are prepare-only in this SDK (EVM signing is pending the owner's "
                           "decision). Run the prepared call with your own wallet client.")
    if not callable(signer):
        raise NotSupported(f"{card_id}: pass signer=<callable> (your wallet) to the adapter for wallet-signed calls")
    return signer


def signed_headers(card_id, signer, request, expect):
    """Call the signer and keep only the headers it is expected to return (case-insensitive), as strings."""
    out = signer(dict(request))
    if not isinstance(out, dict):
        raise NotSupported(f"{card_id}: the signer must return a dict of headers")
    want = {e.lower(): e for e in expect}
    got = {want[k.lower()]: str(v) for k, v in out.items() if isinstance(k, str) and k.lower() in want and v}
    if "signature" in want and "Signature" not in got or "payment-signature" in want and "PAYMENT-SIGNATURE" not in got:
        raise NotSupported(f"{card_id}: the signer returned no signature header")
    return got


def decode_payment_required(value):
    """The x402 v2 PaymentRequired object from a base64 PAYMENT-REQUIRED header, or None if it can't be read."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw = base64.b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def pick_requirement(card_id, payment_required, amount_usdc, pay_to, assets):
    """The first offered x402 'exact' requirement that charges exactly amount_usdc (whole USDC), to the documented
    payTo address, in a documented USDC asset on a documented network. Anything else raises: the signer is never asked
    to sign a price, a receiver or an asset the person did not approve. x402 v2 names the atomic amount `amount`
    (v1: `maxAmountRequired`); both are read."""
    accepts = (payment_required or {}).get("accepts")
    if not isinstance(accepts, list):
        raise NotSupported(f"{card_id}: the 402 carried no readable payment requirements; nothing was signed")
    want = str(int(amount_usdc) * 10 ** USDC_DECIMALS)
    for r in accepts:
        if not isinstance(r, dict) or r.get("scheme") != "exact":
            continue
        amt = r.get("amount", r.get("maxAmountRequired"))
        if (str(amt) == want and str(r.get("payTo", "")).lower() == pay_to.lower()
                and str(r.get("asset", "")).lower() == str(assets.get(r.get("network"), "")).lower()):
            return r
    raise NotSupported(f"{card_id}: no offered payment matched {amount_usdc} USDC to the documented address; nothing was signed")


# -------------------------------------------------------------------------------------------------------------- geo

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_center(gh):
    """(lat, lon, half_height_deg, half_width_deg) of a geohash cell's centre, or None if it isn't a geohash."""
    if not isinstance(gh, str) or not gh or any(c not in _B32 for c in gh.lower()):
        return None
    lat, lon, even = [-90.0, 90.0], [-180.0, 180.0], True
    for c in gh.lower():
        v = _B32.index(c)
        for bit in (16, 8, 4, 2, 1):
            rng = lon if even else lat
            mid = (rng[0] + rng[1]) / 2
            if v & bit:
                rng[0] = mid
            else:
                rng[1] = mid
            even = not even
    return ((lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2, (lat[1] - lat[0]) / 2, (lon[1] - lon[0]) / 2)
