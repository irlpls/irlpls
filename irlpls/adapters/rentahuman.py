"""RentAHuman: post a paid bounty for people to do a task, optionally on site, with live photo/video capture or
location check-ins required. Key in RENTAHUMAN_API_KEY (agents can sign up headlessly). The platform does not verify
submissions itself; require live capture and check-ins, and review what comes back.

Docs: https://rentahuman.ai/docs and https://rentahuman.ai/openapi.json (POST /bounties: title, description, price,
estimatedHours required; dryRun returns a preview with the funding total and the docs ask that it be shown and approved
before posting; idempotencyKey; GET /bounties/{id}; header X-API-Key)."""
from .. import http
from .base import Adapter, MissingDetails, pick_operation, prepared_call

BASE = "https://rentahuman.ai/api"


class RentAHuman(Adapter):
    card_id = "rentahuman"
    env = "RENTAHUMAN_API_KEY"
    how_to_get = "POST https://rentahuman.ai/api/agents/signup (email, acceptTerms, country, city), then verify the emailed code."
    can_commission = True

    def _h(self, required=True):
        return {"X-API-Key": self.key(required)}

    def prepare(self, req, route, operation=None):
        """quote: POST /bounties with dryRun true (default; nothing posted); order: the same without dryRun; status: one bounty."""
        op = pick_operation(route, operation, "commission", ("quote", "order", "status"), "quote")
        if not op:
            return None
        h = {"X-API-Key": "{" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header X-API-Key",
                    get_key_url="https://rentahuman.ai/account/api-keys", docs_url="https://rentahuman.ai/docs")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/bounties/{{BOUNTY_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "BOUNTY_ID", "what": "the id POST /bounties returned", "where": "its response"}],
                                 notes="Then GET /bounties/{id}/applications to accept a worker and /bounties/{id}/submissions for evidence.")
        spec = self.spec_from_request(req, None, req.get("deliver_by"))
        body = self._body(spec)
        ph = []
        if "price" not in body:
            body["price"] = "{PRICE_USD}"
            ph.append({"name": "PRICE_USD", "what": "what you pay in USD (a number)", "where": "your user's budget"})
        if "country" not in body["location"]:
            body["location"]["country"] = "{COUNTRY}"
            ph.append({"name": "COUNTRY", "what": "the country of the task", "where": "the ask's place"})
        if "city" not in body["location"]:
            body["location"]["city"] = "{CITY}"
            ph.append({"name": "CITY", "what": "the city of the task", "where": "the ask's place"})
        if op == "quote":
            body["dryRun"] = True
        else:
            body["idempotencyKey"] = "{IDEMPOTENCY_KEY}"
            ph.append({"name": "IDEMPOTENCY_KEY", "what": "a unique id for this bounty", "where": "you choose it"})
        return prepared_call(
            self.card_id, op, "POST", f"{BASE}/bounties", headers=dict(h, **{"Content-Type": "application/json"}), body=body, **auth,
            placeholders=ph,
            notes=("dryRun: true returns a preview with the funding total and posts nothing; show it to the account owner. "
                   if op == "quote" else
                   "Posts the bounty (the docs require the dryRun preview to be approved first). Unfunded bounties come back "
                   "pending_deposit with a deposit_url. Needs the person's approval. ")
                  + "The platform does not verify submissions; live capture and check-ins are required here.")

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        """The bounty spec from an ask: the need as title and description, the place as the city, the country code, the
        requester's own price (max_price_usd; never invented), the deadline and one spot per item asked for."""
        need = (req.get("need") or "").strip()
        cc = req.get("country_code")
        s = {"title": need[:120] or None, "description": (need + " Send live photos as evidence.") if need else None,
             "price": req.get("max_price_usd"), "country": cc.upper() if isinstance(cc, str) and cc else None,
             "city": req.get("place"), "deadline": deliver_by or req.get("deliver_by"),
             "spots": req.get("quantity") if isinstance(req.get("quantity"), int) and req.get("quantity") > 1 else None,
             "idempotency_key": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _body(spec):
        """POST /bounties body. Nothing is defaulted that the person must decide: no price, no task text."""
        body = {"title": spec.get("title"), "description": spec.get("description", spec.get("notes")),
                "price": spec.get("price"), "estimatedHours": spec.get("hours", 1),
                "evidenceTypes": spec.get("evidence", ["photo"]), "liveCaptureRequirement": spec.get("live", True),
                "checkIns": {"enabled": True, "intervalMinutes": spec.get("checkin_minutes", 30)},
                "location": {k: v for k, v in {"country": spec.get("country"), "city": spec.get("city"), "isRemoteAllowed": False}.items() if v is not None},
                "deadline": spec.get("deadline"), "spotsAvailable": spec.get("spots", 1),
                "idempotencyKey": spec.get("idempotency_key")}
        return {k: v for k, v in body.items() if v is not None}

    @staticmethod
    def missing(body):
        from decimal import Decimal, InvalidOperation
        m = []
        if len((body.get("title") or "").strip()) < 3:
            m.append("title: a short name for the task")
        if len((body.get("description") or "").strip()) < 10:
            m.append("description: what the person should do and capture (10 characters or more)")
        p = body.get("price")
        try:
            ok = p is not None and not isinstance(p, bool) and Decimal(str(p)) > 0
        except InvalidOperation:
            ok = False
        if not ok:
            m.append("price: what you pay in USD, set by your person (pass spec.price); irlpls never picks one")
        return m

    def commission(self, spec, confirm=False):
        """spec: title, description, price (USD), hours, country, city, evidence (["photo","video"]), live (bool), deadline, spots."""
        body = self._body(spec)
        missing = self.missing(body)
        if not confirm:
            out = self._dry("POST", f"{BASE}/bounties", body, self._h(required=False))
            if missing:
                out["missing"] = missing
            elif body.get("price") is not None:
                # the bounty price is the person's own number; fees are the platform's, so it stays indicative
                out["provider_quote"] = {"amount": body["price"], "currency": "USD"}
            return out
        if missing:
            raise MissingDetails(self.card_id, missing)
        return http.post(f"{BASE}/bounties", body=body, headers=self._h())

    def status(self, order):
        bid = order["id"]
        return {"bounty": http.get(f"{BASE}/bounties/{bid}", headers=self._h()),
                "submissions": http.get(f"{BASE}/bounties/{bid}/submissions", headers=self._h())}
