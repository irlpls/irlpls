"""GetterDone: post a USD bounty (reward $1-$100 plus a platform fee) for a gig worker to do a physical task (in-person
tasks launch in the Atlanta metro, Georgia) or a remote one, and get photo/text proof with an automated criteria check.
Key in GETTERDONE_API_KEY, the agent API key "gd_<clientId>:<clientSecret>" (the docs' `apiKey`), exchanged for a 1-hour
Bearer token by the client_credentials grant.

Docs (public, read 2026-10-28):
- OpenAPI 3.1: https://getterdone.ai/api/openapi (POST /api/auth/agent/token, POST /api/tasks/quote, POST /api/tasks,
  GET /api/tasks/{id}, POST /api/tasks/{id}/cancel; Task, Location, ReviewCriteria, ProofOfWork schemas)
- Reference (Markdown): https://getterdone.ai/api/docs/spec, incl. Sandbox mode (https://getterdone.ai/docs/api#sandbox-mode)
- https://getterdone.ai/llms.txt (fee tiers, launch area, "don't poll faster than once every 5 minutes")

Sandbox: an agent attribute, not a host. A sandbox agent's key works against the same base URL; its quotes say
`sandbox: true` / `tokenStatus: "sandbox"`, its tasks get `sbx_` ids, a simulated worker claims (~30 s) and submits proof
(~60 s), and nothing is charged or dispatched ($200 pretend budget and 20 creations per UTC day). spec
`require_sandbox=True` makes commission(confirm=True) quote first and refuse unless the quote says sandbox. Nothing here
has been called: registering even a sandbox agent is a sign-up, held for the owner. Fixtures are documented-shape.
"""
from decimal import Decimal, InvalidOperation

from .. import http
from . import _a4
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, NotSupported, observation, prepared_call

BASE = "https://getterdone.ai"
TOKEN_URL = BASE + "/api/auth/agent/token"
DOCS = BASE + "/docs/api"
CATEGORIES = ("General", "Research", "Data Entry", "Writing", "Design", "Photography", "Delivery", "Handyman", "Errands",
              "Translation", "Customer Service", "Verification", "Inspection", "Mystery Shopping", "Promotion",
              "Proofreading", "Video", "Voice & Audio", "Social Media", "Other")
_TOKENS = TokenCache()


def reward_for_total(total):
    """The largest reward whose documented fee keeps reward + fee <= total (fee: $2 flat for $1-$20, 20% to $75, 15% to
    $100), floored to cents and capped at $100; None below the $3 minimum total. The quote call gives the exact figure."""
    try:
        t = Decimal(str(total))
    except (InvalidOperation, ValueError):
        return None
    if t < 3:
        return None
    if t <= 22:
        r = t - 2
    elif t <= Decimal("90"):
        r = t / Decimal("1.2")
    else:
        r = min(t / Decimal("1.15"), Decimal(100))
    return float(r.quantize(Decimal("0.01"), rounding="ROUND_DOWN"))


class GetterDone(Adapter):
    card_id = "getterdone"
    env = "GETTERDONE_API_KEY"
    how_to_get = ("Register the agent at https://getterdone.ai/register-agent (owner KYC + card) or, for testing, create a "
                  "sandbox agent (dashboard My Agents > New sandbox agent); copy its apiKey 'gd_<clientId>:<clientSecret>'.")
    can_commission = True
    can_cancel = True

    def _token(self):
        cid, _, secret = self.key().partition(":")
        if not cid or not secret:
            raise MissingCredentials(f"{self.card_id}: {self.env} must be the apiKey 'clientId:clientSecret'. {self.how_to_get}")
        return _TOKENS.client_credentials(TOKEN_URL, cid, secret)

    def _call(self, method, path, body=None):
        try:
            return http.request(method, BASE + path, body=body, headers={"Authorization": "Bearer " + self._token()})
        except http.HTTPError as e:
            _a4.raise_if_unauthorized(self.card_id, e, self.env, self.how_to_get)
            raise

    # ---- order
    def _body(self, spec):
        remote = bool(spec.get("remote"))
        lat, lon = spec.get("lat"), spec.get("lon")
        hours = spec.get("expires_in_hours")
        if hours is None:
            m = _a4.minutes_until(spec.get("deadline"), None)
            hours = round(m / 60, 2) if m else None
        body = {"title": _a4.clip(spec.get("title") or spec.get("description") or spec.get("notes"), 150),
                "description": spec.get("description") or spec.get("notes"),
                "reward": spec.get("reward"), "category": spec.get("category"),
                "expiresInHours": _a4.clamp(hours, 0.5, 720) if hours else None,
                "remote": True if remote else None,
                "location": None if remote or lat is None or lon is None else
                {"lat": lat, "lng": lon, "label": spec.get("address") or f"{float(lat):.5f}, {float(lon):.5f}"},
                "reviewCriteria": spec.get("review_criteria"), "minTrustScore": spec.get("min_trust_score"),
                "tags": spec.get("tags"), "privateDescription": spec.get("private_description"),
                "confirmationToken": spec.get("confirmation_token"), "sandboxScenario": spec.get("sandbox_scenario")}
        body = {k: v for k, v in body.items() if v is not None}
        missing = []
        if len(body.get("title") or "") < 5 or len(body.get("description") or "") < 20:
            missing.append("description: what to do and what proof to send (title 5-150, description 20+ characters)")
        r = body.get("reward")
        if not (isinstance(r, (int, float)) and not isinstance(r, bool) and 1 <= r <= 100):
            missing.append("reward: the worker's reward in USD, 1-100 (a fee is added: $2 up to $20, then 20% / 15%)")
        if not remote and "location" not in body:
            missing.append("lat: a physical task needs lat, lon and a label (or set remote)")
        if body.get("category") and body["category"] not in CATEGORIES:
            missing.append("category: one of " + ", ".join(CATEGORIES))
        return body, missing

    def quote(self, spec):
        """POST /api/tasks/quote: the exact total, nothing charged, plus a 10-minute confirmationToken. Returns `data`."""
        body, _ = self._body(spec)
        body.pop("confirmationToken", None)
        resp = self._call("POST", "/api/tasks/quote", body) or {}
        return resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else resp

    def commission(self, spec, confirm=False):
        """spec: title, description (or notes), reward (USD 1-100), lat, lon, address (label) or remote=True, category,
        expires_in_hours (or deadline), review_criteria {keywords, minImages, minVideos, minTextLength}, min_trust_score,
        tags, private_description, confirmation_token, sandbox_scenario (no-claim|bad-proof|forfeit, sandbox agents only),
        require_sandbox (refuse to create unless the quote says sandbox).
        Dry run: the provider's quote when the key is set (its USD total is firm). confirm=True creates the task: the
        owner's card is authorised for reward + fee (a sandbox agent spends pretend money)."""
        body, missing = self._body(spec)
        url = BASE + "/api/tasks"
        if confirm:
            if missing:
                raise NotSupported(f"{self.card_id}: cannot create, missing " + "; ".join(m.split(":")[0] for m in missing))
            if spec.get("require_sandbox"):
                q = self.quote(spec)
                if not (isinstance(q, dict) and (q.get("sandbox") is True or q.get("tokenStatus") == "sandbox")):
                    raise NotSupported(f"{self.card_id}: require_sandbox is set but the key is not a sandbox agent's; nothing was created")
                body.setdefault("confirmationToken", q.get("confirmationToken"))
            return self._call("POST", "/api/tasks", body)
        out = self._dry("POST", url, body, {"Authorization": "Bearer <token from GETTERDONE_API_KEY>"})
        out["note"] = ("Nothing was sent. confirm=True creates the task and authorises the owner's card for reward + fee "
                       "(a sandbox agent spends pretend money and a simulated worker answers).")
        if missing:
            out["missing"] = missing
            return out
        if self.configured():
            try:
                q = self.quote(spec)
                if isinstance(q, dict):
                    if q.get("currency") not in (None, "USD"):   # never a non-USD figure under a firm key
                        q = {("provider_" + k if k == "total" else k): v for k, v in q.items()}
                    out["quote"] = q
            except http.HTTPError as e:
                out["warnings"] = [_a4.http_warning(self.card_id, e, "quoting")]
            except MissingCredentials as e:
                out["warnings"] = [{"provider": self.card_id, "code": "needs_key", "msg": str(e)}]
        return out

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or ""
        photo = bool(_a4.PHOTO.search(need))
        cat = "Photography" if photo else "Verification" if _a4.VERIFY.search(need) else "Errands"
        mins = _a4.minutes_until(deliver_by, req.get("within_minutes"))
        budget = req.get("max_price_usd")
        s = {"title": need, "description": need + (" Send clear photos as proof." if photo else " Describe what you found as proof."),
             "lat": req.get("lat"), "lon": req.get("lon"), "address": req.get("place"), "category": cat,
             "reward": reward_for_total(budget) if budget is not None else None,
             "expires_in_hours": round(_a4.clamp(mins / 60, 0.5, 720), 2) if mins else None,
             "review_criteria": {"minImages": 1} if photo else {"minTextLength": 20}}
        return {k: v for k, v in s.items() if v is not None}

    def prepare(self, req, route):
        """The quote call (nothing is charged), the step before creating a task."""
        if route != "commission" or not req.get("need"):
            return None
        body, missing = self._body(self.spec_from_request(req))
        ph = [{"name": "GETTERDONE_ACCESS_TOKEN", "what": "a 1-hour Bearer token",
               "where": f"POST {TOKEN_URL} with grant_type=client_credentials, client_id and client_secret (the two halves "
                        "of GETTERDONE_API_KEY), form or JSON"}]
        if "reward" not in body:
            body["reward"] = "{REWARD_USD}"
            ph.append({"name": "REWARD_USD", "what": "the worker's reward in USD, 1-100 (the fee is added on top)",
                       "where": "your user's budget"})
        return prepared_call(
            self.card_id, "quote", "POST", BASE + "/api/tasks/quote",
            headers={"Authorization": "Bearer {GETTERDONE_ACCESS_TOKEN}", "Content-Type": "application/json"}, body=body,
            auth_kind="oauth2", key_name=self.env, key_in="client_credentials at /api/auth/agent/token, then header "
            "Authorization: Bearer", get_key_url=BASE + "/register-agent", docs_url=DOCS, placeholders=ph,
            notes=f"OAuth client_credentials: token URL {TOKEN_URL}. The quote returns reward, platformFee, total (USD) and "
                  "a 10-minute confirmationToken; nothing is charged. After the user approves the total, POST /api/tasks "
                  "with the same body plus confirmationToken; poll GET /api/tasks/{id} no faster than every 5 minutes. "
                  "In-person tasks only in the launch area (Atlanta metro, GA); others answer 403 "
                  "LOCATION_OUTSIDE_LAUNCH_AREA. A sandbox agent's key runs the same calls with a simulated worker and "
                  "no charge." + (" Still needed: " + "; ".join(m for m in missing if not m.startswith("reward")) if
                                   [m for m in missing if not m.startswith("reward")] else ""))

    # ---- status / cancel
    def _id(self, order):
        o = order if isinstance(order, dict) else {}
        tid = (o.get("data") or {}).get("id") if isinstance(o.get("data"), dict) else None
        tid = tid or o.get("id")
        if not tid:
            raise NotSupported(f"{self.card_id}: the order has no task id")
        return tid

    def status(self, order):
        return self._call("GET", f"/api/tasks/{self._id(order)}")

    def cancel(self, order):
        """POST /api/tasks/{id}/cancel: open tasks only (422 once claimed); full refund of reward + fee."""
        return self._call("POST", f"/api/tasks/{self._id(order)}/cancel")

    def order_total(self, resp):
        """escrowedAmount (reward + fee secured, USD) from the create response, else reward + platformFee."""
        d = resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else {}
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
        if num(d.get("escrowedAmount")):
            return Decimal(str(d["escrowedAmount"]))
        if num(d.get("reward")) and num(d.get("platformFee")):
            return Decimal(str(d["reward"])) + Decimal(str(d["platformFee"]))
        return None

    status_path = ("data", "status")
    # Task.status enum (OpenAPI) and the sandbox lifecycle table: open -> claimed -> submitted -> payout_pending ->
    # completed. "resolved" ends a dispute upheld for the agent with the funds returned (no delivery); expired and
    # canceled end it. "suspended" is listed without a meaning, so it stays unmapped (in_flight, reported verbatim).
    status_map = {"open": "in_flight", "claimed": "in_flight", "submitted": "in_flight", "payout_pending": "in_flight",
                  "disputed": "in_flight", "contested": "in_flight", "completed": "delivered", "resolved": "failed",
                  "expired": "failed", "canceled": "failed"}

    def deliverables(self, resp):
        """A completed task's proof: each image and video URL, and the worker's text. GetterDone states no capture time
        in the task, so phenomenon_time is None; the automated criteria and image checks ride along."""
        d = resp.get("data") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else (resp if isinstance(resp, dict) else {})
        p = d.get("proofOfWork") if isinstance(d.get("proofOfWork"), dict) else {}
        loc = d.get("location") if isinstance(d.get("location"), dict) else {}
        lat, lon = loc.get("lat"), loc.get("lng")
        common = {k: v for k, v in {"task_id": d.get("id"), "sandbox": d.get("sandbox"), "criteria_check": d.get("criteriaCheckResult"),
                                    "image_authenticity": d.get("imageAuthenticityResult")}.items() if v is not None}
        out = []
        for kind, key in (("photo", "images"), ("video", "videos")):
            for i, u in enumerate(p.get(key) or []):
                if isinstance(u, str) and u:
                    out.append(observation(self.card_id, kind, None, lat, lon, label=f"{kind} {i + 1}", url=u, **common))
        if isinstance(p.get("text"), str) and p["text"]:
            out.append(observation(self.card_id, "report", None, lat, lon, value=p["text"], label="worker's proof text", **common))
        return out
