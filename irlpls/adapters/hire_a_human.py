"""Hire a Human: post a task (on-site photos, checks, errands) to a marketplace of KYC-verified freelancers, who then
apply. Key in HIREAHUMAN_API_KEY (prefix hah_), sent as the x-api-key header; issued after Veriff KYC.

Sources (read 2026-10-28):
- Developer guide, https://hire-a-human.ai/blog/ai-agent-marketplace-mcp-rest-api-guide (2026-02-25): GET /api-tasks
  (list), POST /api-tasks (create: title, description, offered_rate, target_type ai | human | both, deadline),
  POST /api-task-apply (freelancer side), GET /api-usage-stats, GET /api-rate-limit-status; x-api-key; scoped keys;
  60 requests/minute.
- llms.txt, https://hire-a-human.ai/llms.txt: full docs "available through the platform dashboard"; Stripe escrow.
- The guide's example host (https://hire-a-human.ai/api-tasks) serves the website. The functions answer at
  https://cwktwvmcxstpscjcmqzi.supabase.co/functions/v1/api-tasks: a keyless GET on 2026-10-28 returned 401
  {"error":"Missing x-api-key header"} (the card's base_url).

Unverified: no response body is published for either call, and no status, applicant, hire or deliverable endpoint is
published, so this adapter has no status() and its fixtures are documented-shape. Posting a task is the order: it goes
live to freelancers at once; money moves later, when a freelancer is hired and escrow is funded (in the web app).
"""
from .. import http
from .base import Adapter, MissingCredentials, prepared_call

BASE = "https://cwktwvmcxstpscjcmqzi.supabase.co/functions/v1"
DOCS = "https://hire-a-human.ai/blog/ai-agent-marketplace-mcp-rest-api-guide"
KEY_URL = "https://hire-a-human.ai/dashboard/ai-agent"
RATE = {"name": "HIREAHUMAN_OFFERED_RATE", "what": "the rate you offer in USD (freelancers bid against it; the provider "
        "says physical tasks typically pay $15-$30 an hour)", "where": "your person's budget"}


def _err(e, what):
    if not isinstance(e, http.HTTPError):
        return e
    if e.status in (401, 403):
        return MissingCredentials(f"hire-a-human: key refused or missing a scope ({e.status}). Set HIREAHUMAN_API_KEY to a "
                                  "key with the write scope from the AI Agent Dashboard.")
    if e.status == 404:
        return LookupError(f"hire-a-human: {what} not found (404)")
    if e.status == 429 and getattr(e, "retry_after", None) is None:
        from ..ratelimit import retry_after
        e.retry_after = retry_after(e.headers)
    return e


class HireAHuman(Adapter):
    card_id = "hire-a-human"
    env = "HIREAHUMAN_API_KEY"
    how_to_get = ("Sign up at hire-a-human.ai as 'AI Agent / MCP', pass Veriff KYC, then generate a key (hah_...) with the "
                  "write scope in the AI Agent Dashboard. Keys expire (30 days by default) and are shown once.")
    can_commission = True

    def _h(self, required=True):
        return {"x-api-key": self.key(required), "Content-Type": "application/json"}

    def _body(self, spec):
        b = {"title": (spec.get("title") or spec.get("need") or "Capture this in person")[:120],
             "description": spec.get("description") or spec.get("need") or spec.get("notes"),
             "offered_rate": spec.get("offered_rate"), "target_type": "human", "deadline": spec.get("deadline")}
        return {k: v for k, v in b.items() if v is not None}

    def prepare(self, req, route, operation=None, offered_rate=None):
        """Default for a commission ask: the task post (order). operation="read" lists tasks."""
        if route not in ("commission", None) and operation is None:
            return None
        req = req or {}
        h = {"x-api-key": "{" + self.env + "}", "Content-Type": "application/json"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header x-api-key", get_key_url=KEY_URL, docs_url=DOCS)
        op = operation or "order"
        if op == "read":
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/api-tasks", headers=h, **auth,
                                 notes="Lists tasks. The response shape is not published.")
        if op == "order":
            need, place = req.get("need") or "", req.get("place")
            body = self._body({"need": need, "description": need + (f" Location: {place}." if place and place not in need else ""),
                               "offered_rate": offered_rate if offered_rate is not None else "{HIREAHUMAN_OFFERED_RATE}",
                               "deadline": req.get("deadline")})
            return prepared_call(self.card_id, "order", "POST", f"{BASE}/api-tasks", headers=h, body=body, **auth,
                                 placeholders=None if offered_rate is not None else [RATE],
                                 notes="Posts the task to the marketplace at once; freelancers then apply. Hiring and "
                                       "escrow happen in the web app; no status or deliverable endpoint is published. "
                                       "deadline is a date (YYYY-MM-DD). The guide's example host serves the website; "
                                       "this is the host that answers. Needs the person's approval.")
        return None

    def commission(self, spec, confirm=False):
        """spec: title or need, description, offered_rate (USD; required, never assumed), deadline (YYYY-MM-DD).
        Without confirm: the call that would be sent. confirm=True posts the task (it goes live to freelancers)."""
        spec = spec or {}
        body = self._body(spec)
        missing = [m for m, ok in (("offered_rate", spec.get("offered_rate") is not None),
                                   ("description", body.get("description")), (self.env, self.configured())) if not ok]
        if not confirm:
            out = self._dry("POST", f"{BASE}/api-tasks", body, self._h(required=False))
            out["note"] = ("Nothing was sent. Pass confirm=True to post the task; it goes live to freelancers at once. "
                           "Hiring and escrow payment happen in the Hire a Human web app.")
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise ValueError(f"hire-a-human: spec is missing {', '.join(missing)}")
        try:
            return http.post(f"{BASE}/api-tasks", body=body, headers=self._h())
        except http.HTTPError as e:
            raise _err(e, "task endpoint") from None

    def tasks(self):
        """GET /api-tasks as returned (shape unpublished)."""
        try:
            return http.get(f"{BASE}/api-tasks", headers=self._h())
        except http.HTTPError as e:
            raise _err(e, "task list") from None

    def spec_from_request(self, req, ext_id, deliver_by):
        """offered_rate stays missing: a protocol request carries no rate, and none is invented."""
        need = req.get("need")
        return {k: v for k, v in {"title": (need or "")[:120] or None, "description": need,
                                  "deadline": deliver_by[:10] if isinstance(deliver_by, str) else None}.items() if v is not None}
