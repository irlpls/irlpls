"""Human For AI: one verified human operator takes tasks from agents (verification, local errands, testing, review).
No key, no account, free during the pilot. A task is an order to a real person: the operator reviews and accepts or
rejects each one, so ordering always needs the user's yes, and this module's tests never call POST /tasks.

Docs (all public, read 2026-10-28):
- API reference: https://humanforai.dev/api ; OpenAPI 3: https://humanforai.dev/openapi.json (paths /api/v1/services,
  /api/v1/tasks, /api/v1/tasks/{task_id}; TaskRequest, Task, TaskStatus schemas; Idempotency-Key header; 202/409/422/429)
- https://humanforai.dev/llms.txt (free pilot, status_poll delivery = 1 task per client per day, English only)

What runs: services() (keyless read, live-checked 2026-10-28), commission() dry run (no provider quote exists: the pilot
is free and budget_usd is ignored, so offers stay indicative), status() and deliverables() (GET /tasks/{id}; anyone with
the task id can read it). Delivered tasks carry a compact JWS receipt (EdDSA) over deliverable_sha256; that is the
provider's own receipt, not an irlpls receipt, and is passed through as is.
"""
from .. import http
from . import _a4
from .base import Adapter, MissingDetails, MissingCredentials, NotSupported, Results, observation, prepared_call

BASE = "https://humanforai.dev/api/v1"
DOCS = "https://humanforai.dev/api"
TASK_TYPES = ("real_world_verification", "product_or_app_testing", "human_judgment_and_feedback", "data_collection",
              "local_physical_task", "ai_output_review", "prompt_and_workflow_testing", "simulation_and_automation_testing",
              "accessibility_and_usability_check", "custom_human_in_the_loop")


class HumanForAI(Adapter):
    card_id = "human-for-ai"
    how_to_get = "No key or account. A task needs a reachable contact_email, or delivery 'status_poll' (1 per client per day)."
    can_commission = True

    # ---- read
    def services(self, limit=50):
        """The service catalogue (GET /api/v1/services, keyless): [{id, name, description}], following next_cursor."""
        out, cursor, warnings = [], None, []
        for _ in range(5):
            try:
                page = http.get(f"{BASE}/services", params={"limit": max(1, min(int(limit), 50)), "cursor": cursor})
            except http.HTTPError as e:
                warnings.append(_a4.http_warning(self.card_id, e, "listing services"))
                break
            out += [s for s in (page or {}).get("items") or [] if isinstance(s, dict)]
            cursor = (page or {}).get("next_cursor")
            if not cursor:
                break
        return Results(out, warnings)

    # ---- order
    def _body(self, spec):
        loc = spec.get("location_detail") or spec.get("address")
        if not loc and spec.get("lat") is not None and spec.get("lon") is not None:
            loc = f"{float(spec['lat']):.5f}, {float(spec['lon']):.5f}"
        email = spec.get("contact_email")
        delivery = spec.get("delivery") or ("email" if email else "status_poll")
        body = {"task_type": spec.get("task_type") or ("local_physical_task" if loc else "custom_human_in_the_loop"),
                "description": spec.get("description") or spec.get("notes"),
                "location_required": bool(loc), "location_detail": _a4.clip(loc, 500) if loc else None,
                "deadline": spec.get("deadline"),
                # status_poll returns the deliverable as text only (operator_notes), so ask for a text report there.
                "output_format": spec.get("output_format") or ("text_report" if delivery == "status_poll" else "text_report_with_photos"),
                "contact_email": email, "delivery": delivery, "requester": spec.get("requester") or "irlpls"}
        body = {k: v for k, v in body.items() if v is not None}
        missing = []
        if body["task_type"] not in TASK_TYPES:
            missing.append(f"task_type: one of {', '.join(TASK_TYPES)}")
        d = body.get("description") or ""
        if not 10 <= len(d) <= 5000:
            missing.append("description: what to do, where, and what success looks like (10-5000 characters)")
        if delivery == "email" and not email:
            missing.append("contact_email: a real, reachable mailbox for the result (or use delivery status_poll)")
        return body, missing

    def _headers(self, spec):
        return {"Idempotency-Key": spec["external_id"]} if spec.get("external_id") else {}

    def commission(self, spec, confirm=False):
        """spec: description (or notes), task_type, location_detail/address/lat/lon, deadline (ISO 8601), output_format,
        contact_email or delivery='status_poll', requester, external_id (sent as Idempotency-Key).
        confirm=True submits a task to a real human operator. The pilot is free; there is no quote endpoint."""
        body, missing = self._body(spec)
        url = f"{BASE}/tasks"
        if not confirm:
            out = self._dry("POST", url, body, self._headers(spec))
            out["note"] = ("Nothing was sent. Human For AI has no quote call: the pilot is free (budget_usd is ignored). "
                           "confirm=True sends this task to a real human operator, who accepts or rejects it.")
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise MissingDetails(self.card_id, missing)
        return http.post(url, body=body, headers=self._headers(spec))

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or ""
        loc = _a4.where(req)
        if loc and _a4.VERIFY.search(need):
            tt = "real_world_verification"
        elif loc:
            tt = "local_physical_task"
        else:
            tt = "custom_human_in_the_loop"
        s = {"task_type": tt, "description": need, "location_detail": loc, "lat": req.get("lat"), "lon": req.get("lon"),
             "deadline": _a4.deadline_iso(deliver_by, req.get("within_minutes")), "external_id": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    def prepare(self, req, route):
        if route != "commission" or not req.get("need"):
            return None
        body, missing = self._body(self.spec_from_request(req, None, req.get("deliver_by")))
        return prepared_call(
            self.card_id, "order", "POST", f"{BASE}/tasks", headers={"Content-Type": "application/json"}, body=body,
            auth_kind="none", docs_url=DOCS, safe_to_run_without_approval=False,
            notes="Sends the task to a real human operator (free pilot; no quote call exists), so run it only after your "
                  "user says yes. delivery 'status_poll' returns the result as text in operator_notes and is limited to 1 "
                  "task per client per day; for photos, set delivery 'email' and a real contact_email. Add an "
                  "Idempotency-Key header to retry safely; an identical task within 24 h answers 409. Then poll GET "
                  "/api/v1/tasks/{task_id} (status submitted -> accepted -> delivered, or rejected). Catalogue: GET "
                  "/api/v1/services." + (" Still needed: " + "; ".join(missing) if missing else ""))

    # ---- status
    def status(self, order):
        tid = (order or {}).get("task_id") or (order or {}).get("id")
        if not tid:
            raise NotSupported(f"{self.card_id}: the order has no task_id")
        try:
            return http.get(f"{BASE}/tasks/{tid}")
        except http.HTTPError as e:
            if e.status in (401, 403):
                raise MissingCredentials(f"{self.card_id}: the provider refused the status request (HTTP {e.status})") from None
            raise

    # TaskStatus enum in the OpenAPI: submitted -> accepted -> delivered, rejected from any open state. The retired values
    # under_review and in_progress fold onto submitted and accepted (v1.8.0), so they map the same way.
    status_map = {"submitted": "in_flight", "accepted": "in_flight", "under_review": "in_flight",
                  "in_progress": "in_flight", "delivered": "delivered", "rejected": "failed"}

    def deliverables(self, resp):
        """A delivered task: the operator's text (operator_notes) with its sha256 and the provider's signed receipt.
        The operator gives no observation time, so phenomenon_time stays None; result_time is when the task was
        marked delivered (status_history) or the receipt was issued."""
        r = resp if isinstance(resp, dict) else {}
        notes = r.get("operator_notes")
        if not isinstance(notes, str) or not notes:
            return []
        hist = [h for h in r.get("status_history") or [] if isinstance(h, dict) and h.get("status") == "delivered"]
        when = (hist[-1].get("at") if hist else None) or r.get("receipt_issued_at")
        tid = r.get("task_id")
        extra = {k: r.get(k) for k in ("deliverable_sha256", "receipt", "receipt_issued_at") if r.get(k)}
        return [observation(self.card_id, "report", None, None, None, value=notes, label=f"{r.get('task_type') or 'task'} report",
                            url=f"https://humanforai.dev/tasks?id={tid}" if tid else None, result_time=when, task_id=tid,
                            receipt_kind="provider JWS (EdDSA), not an irlpls receipt" if extra.get("receipt") else None,
                            **extra)]
