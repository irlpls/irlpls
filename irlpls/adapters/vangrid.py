"""Vangrid: post a USDC bounty for a person to film a real place (a 3D reconstruction comes back), paid by x402 on Base
or Arc. No account, no API key: the buyer's wallet pays the bounty amount upfront, and the bounty's own agent_token is
the credential for its status, accept and cancel calls. GET /provenance/{sha256} checks an anchored capture for free.

Sources (read 2026-10-28):
- Agent bounties: https://docs.vangrid.io/api/agent-bounties (flow, fields, statuses, errors, networks)
- OpenAPI 3.1: https://data.vangrid.io/openapi.json (paths, bodies, 402 PAYMENT-REQUIRED, agentToken bearer)
- x402 manifest: https://data.vangrid.io/.well-known/x402 (payTo, networks)
- Pay per request: https://docs.vangrid.io/api/pay-per-request.md (402 -> PAYMENT-SIGNATURE retry)

In this SDK: the free provenance read runs live; status and cancel run with the bounty's agent_token; the paid POST
/bounties is prepare-only (wallet signing pending, see _a7.WALLET_SIGNING), so commission() returns the ready call and
a firm quote (the price is the buyer's own amount_usdc, by construction).
"""
import math
from datetime import datetime, timezone
from decimal import Decimal

from .. import http
from . import _a7
from .base import Adapter, MissingCredentials, observation, prepared_call

BASE = "https://data.vangrid.io/api/v1"
DOCS = "https://docs.vangrid.io/api/agent-bounties"
PAY_TO = "0x1a8b69A1338C6E829E1Ced4c3007C79E945E4E70"           # x402 manifest, 2026-10-28
USDC = {"eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # Base
        "eip155:5042": "0x3600000000000000000000000000000000000000"}  # Arc
MIN_USDC, MAX_USDC = 50, 5000      # server defaults (OpenAPI x-payment-info); the 402 is authoritative
SIG_PLACEHOLDER = "{X402_PAYMENT_SIGNATURE}"
TOKEN_PLACEHOLDER = "{VANGRID_AGENT_TOKEN}"
X402_NOTES = ("x402 v2: send the request once without PAYMENT-SIGNATURE; the 402 carries a base64 PAYMENT-REQUIRED "
              "header quoting exactly amount_usdc in USDC on Base (eip155:8453) or Arc (eip155:5042) to "
              f"{PAY_TO}. Sign an exact USDC authorization for it with your wallet (any x402 client) and repeat the request "
              "with PAYMENT-SIGNATURE. The response returns bounty_id and agent_token once: keep the token, it is the "
              "only credential for status, accept and cancel. A malformed body is refused with 400 before any price.")


def _iso_utc(t):
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


class Vangrid(Adapter):
    card_id = "vangrid"
    needs_wallet = True   # paid calls are signed by the person's wallet (catalog: sdk-callable-wallet)
    how_to_get = ("No account or key: a wallet holding USDC on Base or Arc pays each bounty by x402. The agent_token "
                  "returned by POST /bounties is the credential for that bounty.")
    can_commission = True
    can_cancel = True

    status_path = ("status",)
    status_map = {"paid": "in_flight", "creating": "in_flight", "open": "in_flight", "accepting": "in_flight",
                  "cancelling": "in_flight", "accepted": "delivered", "cancelled": "failed", "expired": "failed",
                  "expired_refunded": "failed", "failed": "failed"}

    def __init__(self, signer=None):
        self.signer = signer   # see _a7: a callable that signs an x402 payment with the person's wallet

    def configured(self):
        """Orders need a wallet signer, and wallet signing is prepare-only for now; reads need nothing."""
        return bool(_a7.WALLET_SIGNING and callable(self.signer))

    # ------------------------------------------------------------------------------------------------ the request
    def _body(self, spec):
        """POST /bounties body from a spec, and what is missing. spec: title, brief (or notes/need/description),
        amount_usdc (or price/budget_usd), deadline_days (or within_minutes), lat/lon/place added to the brief."""
        missing = []
        brief = str(spec.get("brief") or spec.get("notes") or spec.get("need") or spec.get("description") or "").strip()
        title = str(spec.get("title") or brief.split(". ")[0] or "").strip()[:200]
        where = spec.get("place") or spec.get("address")
        if where and str(where) not in brief:
            brief += f" Location: {where}."
        if spec.get("lat") is not None and spec.get("lon") is not None:
            brief += f" Coordinates: {float(spec['lat']):.5f}, {float(spec['lon']):.5f}."
        brief = brief.strip()[:4000]
        if len(title) < 3:
            missing.append("title (3-200 characters)")
        if len(brief) < 20:
            missing.append("brief (20-4000 characters: what to film, where exactly, what must be in frame)")
        amt = spec.get("amount_usdc", spec.get("price", spec.get("budget_usd")))
        if type(amt) in (int, float) and math.isfinite(amt) and amt == int(amt):
            amt = int(amt)
        else:
            amt = None
        if amt is None or not MIN_USDC <= amt <= MAX_USDC:
            missing.append(f"amount_usdc (whole USDC, {MIN_USDC}-{MAX_USDC}; it is the price and the escrow)")
        days = spec.get("deadline_days")
        if days is None and spec.get("within_minutes"):
            days = math.ceil(spec["within_minutes"] / 1440)
        body = {"title": title, "brief": brief, "amount_usdc": amt}
        if days is not None:
            body["deadline_days"] = max(1, min(30, int(days)))
        return {k: v for k, v in body.items() if v not in (None, "")}, missing

    def spec_from_request(self, req, ext_id, deliver_by):
        """Protocol request -> spec. Vangrid has no idempotency key, so ext_id is not sent."""
        spec = {"brief": req.get("need"), "place": req.get("place"), "lat": req.get("lat"), "lon": req.get("lon"),
                "amount_usdc": (req.get("constraints") or {}).get("max_total") if isinstance(req.get("constraints"), dict) else None}
        t = _iso_utc(deliver_by) if deliver_by else None
        if t:
            spec["deadline_days"] = max(1, math.ceil((t - datetime.now(timezone.utc)).total_seconds() / 86400))
        return {k: v for k, v in spec.items() if v is not None}

    def order_total(self, resp):
        """The bounty amount the response says was paid (whole USDC), or None."""
        v = resp.get("amount_usdc") if isinstance(resp, dict) else None
        return Decimal(v) if type(v) is int and v >= 0 else None

    # ---------------------------------------------------------------------------------------------- ready calls
    def prepare(self, req, route):
        if route != "commission":
            return None
        body, missing = self._body({**req, "amount_usdc": req.get("amount_usdc", req.get("max_price_usd"))})
        body.setdefault("amount_usdc", "{AMOUNT_USDC}")
        notes = X402_NOTES + (" Still needed: " + "; ".join(missing) + "." if missing else "")
        ph = [{"name": "AMOUNT_USDC", "what": f"the bounty in whole USDC ({MIN_USDC}-{MAX_USDC}); it is the price",
               "where": "your budget"}] if body["amount_usdc"] == "{AMOUNT_USDC}" else None
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/bounties", body=body,
                             headers={"Content-Type": "application/json", "PAYMENT-SIGNATURE": SIG_PLACEHOLDER},
                             auth_kind="wallet", key_name="X402_PAYMENT_SIGNATURE", key_in="header PAYMENT-SIGNATURE",
                             get_key_url="https://docs.vangrid.io/api/pay-per-request", docs_url=DOCS, notes=notes,
                             placeholders=ph)

    def prepared_calls(self, req, sha256=None, bounty_id=None):
        """Every ready call for this ask, by operation: read (free provenance check), quote (the unpaid POST answers 402
        with the price; it is also the buyer's own amount), order, status."""
        order = self.prepare(req, "commission")
        quote = dict(order, operation="quote", headers={"Content-Type": "application/json"}, auth={"kind": "none"},
                     notes="The same POST without PAYMENT-SIGNATURE is answered 402 with the PAYMENT-REQUIRED header "
                           "(the price, networks and payTo); nothing is created until a signed retry settles.",
                     safe_to_run_without_approval=False)
        bid = bounty_id or "{BOUNTY_ID}"
        status = prepared_call(self.card_id, "status", "GET", f"{BASE}/bounties/{bid}",
                               headers={"Authorization": "Bearer " + TOKEN_PLACEHOLDER}, auth_kind="bearer",
                               key_name="VANGRID_AGENT_TOKEN", key_in="header Authorization: Bearer",
                               get_key_url=DOCS, docs_url=DOCS,
                               notes="Free. agent_token is returned once by POST /bounties. Submissions carry a "
                                     "watermarked preview_url (15 min); after POST /bounties/{id}/accept the accepted "
                                     "one carries full_capture_url and model links (15 min).")
        read = prepared_call(self.card_id, "read", "GET", f"{BASE}/provenance/{sha256 or '{CAPTURE_SHA256}'}",
                             docs_url="https://data.vangrid.io/openapi.json",
                             notes="Free, no payment or token: is this sha256 a capture anchored on Base (EAS attestation).")
        return {"read": read, "quote": quote, "order": order, "status": status}

    # -------------------------------------------------------------------------------------------------- reads
    def provenance(self, sha256):
        """Free check that a capture's sha256 is anchored on Base. Returns one observation (value True/False) with the
        capture time, the geohash cell's centre and the anchor; phenomenon_time is Vangrid's captured_at, or None."""
        h = str(sha256 or "").lower().removeprefix("sha256:").removeprefix("0x")
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise ValueError("sha256 must be 64 hex characters")
        r = _a7.call(self.card_id, "provenance", lambda: http.get(f"{BASE}/provenance/{h}")) or {}
        cell = _a7.geohash_center(r.get("geohash"))
        anchor = r.get("anchor") if isinstance(r.get("anchor"), dict) else {}
        return observation(self.card_id, "provenance", r.get("captured_at"), cell[0] if cell else None,
                           cell[1] if cell else None, value=bool(r.get("verified")),
                           label="anchored on Base" if r.get("verified") else "not anchored",
                           url=anchor.get("eas_url") or anchor.get("viewer_url"), sha256=h, geohash=r.get("geohash"),
                           location_precision_km=round(cell[2] * 111.2, 2) if cell else None,
                           anchor={k: anchor.get(k) for k in ("chain_id", "tree_id", "leaf_hash", "root", "attest_uid",
                                                               "attest_tx", "eas_url", "viewer_url") if anchor.get(k) is not None} or None)

    # ------------------------------------------------------------------------------------------------- orders
    def commission(self, spec, confirm=False):
        body, missing = self._body(spec)
        dry = self._dry("POST", f"{BASE}/bounties", body, {"Content-Type": "application/json",
                                                           "PAYMENT-SIGNATURE": SIG_PLACEHOLDER})
        dry["note"] = "Nothing was sent. " + X402_NOTES
        if missing:
            dry["missing"] = missing
        else:
            dry["quote"] = {"total": body["amount_usdc"], "currency": "USDC", "firm": True,
                            "basis": "The price of POST /bounties is exactly amount_usdc, escrowed and refunded in full "
                                     "on cancel or expiry; Vangrid keeps a platform fee from the reward on acceptance."}
        if not confirm:
            return dry
        signer = _a7.require_signer(self.card_id, self.signer)
        if missing:
            raise ValueError(f"{self.card_id}: missing {', '.join(missing)}")
        return self._paid_post(f"{BASE}/bounties", body, body["amount_usdc"], signer)

    def _paid_post(self, url, body, amount_usdc, signer):
        """x402: unpaid POST -> 402 -> check the quoted requirement -> sign -> repeat. Only reached with signing on."""
        try:
            return http.post(url, body=body)        # a 2xx without payment would be unexpected; return it as is
        except http.HTTPError as e:
            if e.status != 402:
                raise _a7.mapped(self.card_id, "order", e) from None
            hdr = next((v for k, v in (e.headers or {}).items() if k.lower() == "payment-required"), None)
        req = _a7.pick_requirement(self.card_id, _a7.decode_payment_required(hdr), amount_usdc, PAY_TO, USDC)
        h = _a7.signed_headers(self.card_id, signer, {"scheme": "x402", "version": 2, "method": "POST", "url": url,
                                                      "requirement": req}, ["PAYMENT-SIGNATURE"])
        return _a7.call(self.card_id, "order", lambda: http.post(url, body=body, headers=h))

    def _token(self, order):
        tok = order.get("agent_token") if isinstance(order, dict) else None
        if not tok:
            raise MissingCredentials(f"{self.card_id}: the order needs its agent_token (returned once by POST /bounties)")
        bid = order.get("bounty_id") or order.get("id")
        if not bid:
            raise ValueError(f"{self.card_id}: the order needs its bounty_id")
        return str(bid), {"Authorization": "Bearer " + tok}

    def status(self, order):
        """GET /bounties/{id} with the bounty's agent_token (free). Returns Vangrid's response (never the token)."""
        bid, h = self._token(order)
        return _a7.call(self.card_id, "status", lambda: http.get(f"{BASE}/bounties/{bid}", headers=h),
                        hint="The agent_token must match the bounty id.")

    def cancel(self, order):
        """POST /bounties/{id}/cancel while open (free, final); the USDC returns to the paying wallet."""
        bid, h = self._token(order)
        return _a7.call(self.card_id, "cancel", lambda: http.post(f"{BASE}/bounties/{bid}/cancel", body={}, headers=h))

    def deliverables(self, resp):
        """The accepted submission's files (signed links, valid 15 minutes). phenomenon_time is None: Vangrid gives
        the submission time, not the capture time."""
        out = []
        subs = resp.get("submissions") if isinstance(resp, dict) else None
        for s in subs if isinstance(subs, list) else []:
            if not isinstance(s, dict) or not s.get("accepted"):
                continue
            for key, label in (("full_capture_url", "full capture"), ("model_glb_url", "3D model (GLB)"),
                               ("model_zip_url", "3D model (zip)")):
                if s.get(key):
                    out.append(observation(self.card_id, "capture", None, None, None, label=label, url=s[key],
                                           result_time=s.get("submitted_at"), submission_id=s.get("submission_id"),
                                           quality_status=s.get("quality_status"),
                                           reconstruction_status=s.get("reconstruction_status")))
        return out
