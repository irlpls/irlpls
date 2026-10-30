"""Mundane: an agent-to-human marketplace for physical tasks (US, UK, Canada, Switzerland, EEA). Two steps: post a
task (it is screened against the AUP), then offer it to one ID-verified worker at or above their ask rate; the offer
holds pay + fee (the lesser of 5% or $5) in escrow from a prepaid USD wallet until the requester accepts the proof.
Key in MUNDANE_API_KEY (a mundane_agent_ key; headless POST /v1/agents/signup accepts the AUP and ToS, so it is the owner's
decision).

Docs (public, read 2026-10-28):
- OpenAPI: https://api.mundane.market/openapi.json (GET /v1/signup/capabilities, GET /v1/workers, GET /v1/offer-check,
  POST /v1/tasks (PostTaskRequest, TaskCreated), POST /v1/offers (MakeOfferRequest, OfferResult), GET /v1/tasks/{id}
  (TaskStatus), GET /v1/tasks/{id}/proof-media, POST /v1/tasks/{id}/cancel)
- Agent skill: https://github.com/sttruji/mundane-mcp/blob/main/plugin/skills/mundane/SKILL.md (amounts are integer
  minor units with a currency, "2500 + USD is $25.00"; a post answers open, screening or rejected, and a rejection is
  final; releasing payment always needs the user; 401 = bad key, 402 insufficient_balance)
- https://mundane.market/llms.txt

What runs: capabilities() is keyless (live-checked 2026-10-28). With a key: workers() and offer_check() (read-only, no
funded wallet needed) feed the dry run; commission(confirm=True) posts the task and, only when the spec names a worker
and an amount, makes the offer (escrow). Accepting proof (POST /tasks/{id}/review) is never done here. The OpenAPI gives
no status enum for a task beyond the post result, so only open, screening, rejected and completed are mapped.
"""
from decimal import Decimal

from .. import http
from . import _a4
from .base import Adapter, MissingCredentials, NotSupported, Results, observation, prepared_call

BASE = "https://api.mundane.market/v1"
DOCS = "https://api.mundane.market/docs"


class Mundane(Adapter):
    card_id = "mundane"
    env = "MUNDANE_API_KEY"
    how_to_get = ("POST https://api.mundane.market/v1/agents/signup with principal_display_name, principal_email, agent_name, "
                  "accept_aup_version and accept_tos_version; the mundane_agent_ key is shown once.")
    can_commission = True
    can_cancel = True

    def _h(self, required=True):
        return {"Authorization": "Bearer " + self.key(required)}

    def _call(self, method, path, body=None, params=None):
        try:
            return http.request(method, BASE + path, body=body, params=params, headers=self._h())
        except http.HTTPError as e:
            _a4.raise_if_unauthorized(self.card_id, e, self.env, self.how_to_get)
            raise

    # ---- reads
    def capabilities(self):
        """Keyless: [{slug, label, description, required_proof_types, default_proof_types, min_worker_age}]."""
        try:
            r = http.get(f"{BASE}/signup/capabilities")
            return Results([c for c in r or [] if isinstance(c, dict)])
        except http.HTTPError as e:
            return Results([], [_a4.http_warning(self.card_id, e, "listing capabilities")])

    def workers(self, lat, lon, radius_km=25, capability=None, limit=20):
        """Verified workers near a point (key, no funded wallet). [] is a normal answer."""
        r = self._call("GET", "/workers", params={"lat": lat, "lng": lon, "radius_km": _a4.clamp(radius_km, 0.1, 200),
                                                  "capability": capability, "limit": int(_a4.clamp(limit, 1, 100))})
        return [w for w in r or [] if isinstance(w, dict)]

    def offer_check(self, total_minor, new_task=True):
        """Whether an offer of total_minor (pay + fee, minor units) would be refused now, and every reason. Read-only."""
        return self._call("GET", "/offer-check", params={"total_minor": int(total_minor), "new_task": "true" if new_task else "false"})

    # ---- order
    def _task(self, spec):
        loc = {k: v for k, v in {"address": spec.get("address"), "lat": spec.get("lat"), "lng": spec.get("lon")}.items() if v is not None}
        need = spec.get("instructions") or spec.get("notes") or ""
        body = {"title": _a4.clip(spec.get("title") or need, 120), "instructions": need, "location": loc,
                "required_capabilities": spec.get("capabilities") or [spec.get("capability") or "photo_documentation"],
                "budget_max_minor": spec.get("budget_max_minor"), "currency": spec.get("currency") or "USD",
                "deadline": spec.get("deadline"), "proof_requirements": spec.get("proof_requirements"),
                "constraints": spec.get("constraints"), "idempotency_key": spec.get("external_id")}
        body = {k: v for k, v in body.items() if v is not None}
        missing = []
        if not body["title"] or not need:
            missing.append("instructions: what to do, written so a stranger can follow it")
        if not loc:
            missing.append("address: where the worker goes (an address, or lat and lon)")
        b = body.get("budget_max_minor")
        if not (isinstance(b, int) and not isinstance(b, bool) and b > 0):
            missing.append("budget_max_minor: the most you will pay, integer US cents")
        if not body.get("deadline"):
            missing.append("deadline: when it must be done (ISO 8601 UTC)")
        return body, missing

    def _offer(self, spec, task_id):
        o = {"task_id": task_id, "worker_id": spec.get("worker_id"), "amount_minor": spec.get("amount_minor"),
             "currency": spec.get("currency") or "USD", "expires_in_seconds": spec.get("offer_expires_in_seconds"),
             "message": spec.get("message"),
             "idempotency_key": (spec["external_id"] + "-offer") if spec.get("external_id") else None}
        return {k: v for k, v in o.items() if v is not None}

    def commission(self, spec, confirm=False):
        """spec: title, instructions (or notes), address and/or lat, lon, capability (slug; default photo_documentation)
        or capabilities, budget_max_minor (US cents), deadline (ISO 8601), proof_requirements, constraints, external_id
        (idempotency key); for the offer: worker_id, amount_minor (worker's pay, cents), message.
        Dry run: with a key, nearby workers and their ask rates (GET /workers) and, for the budget, GET /offer-check; no
        firm total (the fee is disclosed only in the offer response). confirm=True posts the task, then offers it to
        worker_id when given (pay + fee are escrowed then)."""
        body, missing = self._task(spec)
        if confirm:
            if missing:
                raise NotSupported(f"{self.card_id}: cannot post, missing " + "; ".join(m.split(":")[0] for m in missing))
            created = self._call("POST", "/tasks", body) or {}
            out = {"task_id": created.get("task_id"), "status": created.get("status"), "task": created}
            if spec.get("worker_id") and spec.get("amount_minor") and str(created.get("status")).lower() == "open":
                out["offer"] = self._call("POST", "/offers", self._offer(spec, created.get("task_id")))
            elif str(created.get("status")).lower() != "rejected":
                out["next"] = "POST /v1/offers {task_id, worker_id, amount_minor} to a worker at or above their ask rate"
            return out
        out = self._dry("POST", BASE + "/tasks", body, self._h(required=False))
        out["then"] = {"method": "POST", "url": BASE + "/offers", "body": self._offer(spec, "{task_id}")}
        out["note"] = ("Nothing was sent. Posting screens the task; the offer to one worker escrows pay + fee (the lesser "
                       "of 5% or $5) from the prepaid USD wallet. Releasing payment always needs your user.")
        if missing:
            out["missing"] = missing
        if self.configured() and spec.get("lat") is not None and spec.get("lon") is not None:
            warnings, q = [], {"currency": "USD"}
            try:
                ws = self.workers(spec["lat"], spec["lon"], spec.get("radius_km", 25), body["required_capabilities"][0], 10)
                asks = [w["ask_rate_minor"] for w in ws if isinstance(w.get("ask_rate_minor"), int)]
                q.update(workers_found=len(ws), lowest_ask_minor=min(asks) if asks else None,
                         workers=[{k: w.get(k) for k in ("worker_id", "handle", "ask_rate_minor", "currency", "rating",
                                                         "rating_count", "distance_km")} for w in ws[:5]])
                if isinstance(body.get("budget_max_minor"), int):
                    q["offer_check"] = self.offer_check(body["budget_max_minor"], new_task=True)
            except http.HTTPError as e:
                warnings.append(_a4.http_warning(self.card_id, e, "searching workers"))
            except MissingCredentials as e:
                warnings.append({"provider": self.card_id, "code": "needs_key", "msg": str(e)})
            out["quote"] = {k: v for k, v in q.items() if v is not None}
            if warnings:
                out["warnings"] = warnings
        return out

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or ""
        cap = ("wait_in_line" if _a4.QUEUE.search(need) else "photo_documentation")
        budget = req.get("max_price_usd")
        s = {"title": need, "instructions": need, "address": req.get("place"), "lat": req.get("lat"), "lon": req.get("lon"),
             "capability": cap, "deadline": _a4.deadline_iso(deliver_by, req.get("within_minutes")),
             "budget_max_minor": int(Decimal(str(budget)) * 100) if isinstance(budget, (int, float)) and budget > 0 else None,
             "external_id": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    def prepare(self, req, route):
        """The worker search (read-only, no funded wallet): who is near and at what ask rate, before posting."""
        if route != "commission" or req.get("lat") is None or req.get("lon") is None:
            return None
        spec = self.spec_from_request(req)
        q = f"lat={float(req['lat']):.5f}&lng={float(req['lon']):.5f}&radius_km=25&capability={spec['capability']}&limit=10"
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/workers?{q}", headers={"Authorization": "Bearer {MUNDANE_API_KEY}"},
            auth_kind="bearer", key_name=self.env, key_in="header Authorization: Bearer",
            get_key_url="https://mundane.market/for-agents", docs_url=DOCS, safe_to_run_without_approval=True,
            notes="Read-only and free: each worker's ask_rate_minor (US cents per task); [] is a normal answer. Then GET "
                  "/v1/offer-check?total_minor=<pay+fee>&new_task=true says whether an offer would be refused. To order: "
                  "POST /v1/tasks {title, instructions, location {address, lat, lng}, required_capabilities, "
                  "budget_max_minor, currency 'USD', deadline, idempotency_key} (answers open, screening or rejected), "
                  "then POST /v1/offers {task_id, worker_id, amount_minor, currency} after your user's yes (escrows pay + "
                  "fee). Capability slugs: GET /v1/signup/capabilities (no key).")

    # ---- status / cancel
    def _id(self, order):
        o = order if isinstance(order, dict) else {}
        task = o.get("task") if isinstance(o.get("task"), dict) else {}
        tid = o.get("task_id") or task.get("task_id")
        if not tid:
            raise NotSupported(f"{self.card_id}: the order has no task_id")
        return tid

    def status(self, order):
        """GET /tasks/{id} (lifecycle, offer, worker, completion); once completed, the proof links are attached under
        "proof_media" (GET /tasks/{id}/proof-media: signed, short-lived)."""
        tid = self._id(order)
        t = self._call("GET", f"/tasks/{tid}") or {}
        if isinstance(t, dict) and str(t.get("status", "")).lower() == "completed":
            try:
                t = dict(t, proof_media=self._call("GET", f"/tasks/{tid}/proof-media"))
            except http.HTTPError as e:
                t = dict(t, warnings=[_a4.http_warning(self.card_id, e, "fetching proof links")])
        return t

    def cancel(self, order):
        """POST /tasks/{id}/cancel: cancels the task and any pending offer; after a worker accepted it may owe them a fee."""
        return self._call("POST", f"/tasks/{self._id(order)}/cancel", {"reason": "cancelled by the requester"})

    def order_total(self, resp):
        """total_amount_minor (pay + fee) from the offer response, in USD."""
        off = resp.get("offer") if isinstance(resp, dict) and isinstance(resp.get("offer"), dict) else {}
        v = off.get("total_amount_minor")
        return Decimal(v) / 100 if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None

    # Documented: a post answers open, screening (human review) or rejected (final) (SKILL.md, TaskCreated); a worker
    # backing out returns the task to open (OpenAPI); "completed" is the state a task reaches once proof is approved
    # (rating and findings require it, per the MCP tools). Any other state stays in_flight, reported verbatim.
    status_map = {"open": "in_flight", "screening": "in_flight", "rejected": "failed", "completed": "delivered"}

    def deliverables(self, resp):
        """Proof photos from the signed proof-media links. The response shape beyond a URL is not documented, so only
        url and, when present, captured_at / lat / lng are read; otherwise phenomenon_time stays None."""
        r = resp if isinstance(resp, dict) else {}
        pm = r.get("proof_media")
        if isinstance(pm, dict):
            items = pm.get("items") or pm.get("media") or []
        else:
            items = pm if isinstance(pm, list) else []
        out = []
        for i, m in enumerate(items):
            if isinstance(m, dict) and isinstance(m.get("url"), str) and m["url"]:
                out.append(observation(self.card_id, "photo", m.get("captured_at"), m.get("lat"), m.get("lng"),
                                       label=f"proof photo {i + 1} (signed, short-lived link)", url=m["url"], task_id=r.get("task_id")))
        return out
