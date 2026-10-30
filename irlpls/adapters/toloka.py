"""Toloka Platform (Pipeline API v2-beta): run a crowd data-collection pipeline you set up in Toloka, priced first by a
cost forecast. Key in TOLOKA_API_KEY, sent as "Authorization: ApiKey <key>".

Sources (read 2026-10-28):
- OpenAPI 3.1, https://platform.toloka.ai/api/v2-beta/doc (server https://platform.toloka.ai, paths /api/v2-beta/...):
  GET /projects; POST /pipelines/{pipelineId}/cost-estimate ("Does not start a run"); POST /pipelines/{pipelineId}/runs
  (no request body; 400 not runnable, 403 team not verified, 409 a run is already active); GET /runs/{runId} (status
  pending | running | succeeded | failed); GET /runs/{runId}/items; POST /runs/{runId}/cancel (a cancelled run ends
  "failed" with error "Run canceled"). 429 on every path; RateLimit-* headers; policy "60;w=60".
- Programmatic access, https://platform.toloka.ai/docs/integration/programmatic-access/ (API keys, "ApiKey" prefix).

What the API cannot do: assemble a pipeline (it must be imported or copied in the Toloka UI first, then bound to a
dataset), or top up the balance. So every quote and order needs the account's own pipeline id ({TOLOKA_PIPELINE_ID}).
The API is beta: shapes may change without notice. The cost forecast's totals are strings with no currency field, so a
quote is never a firm USD offer here.
"""
from .. import http
from .base import Adapter, MissingCredentials, observation, prepared_call

HOST = "https://platform.toloka.ai"
BASE = HOST + "/api/v2-beta"
DOCS = "https://platform.toloka.ai/api/v2-beta/doc"
KEY_URL = "https://platform.toloka.ai"
PIPELINE = {"name": "TOLOKA_PIPELINE_ID", "what": "the id of a pipeline you imported or copied in Toloka, bound to a dataset",
            "where": "GET /api/v2-beta/projects/{projectId}/pipelines, or the pipeline's page in the Toloka web app"}
RUN = {"name": "TOLOKA_RUN_ID", "what": "the run id returned when the run started", "where": "POST .../runs response field id"}


def _err(e, what):
    """Provider errors as SDK errors. The key is never in the message; the provider's body is not echoed."""
    if not isinstance(e, http.HTTPError):
        return e
    if e.status == 401:
        return MissingCredentials("toloka: the API key was refused (401). Set TOLOKA_API_KEY to a key from the Toloka "
                                  "web app (company > API keys).")
    if e.status == 403:
        return MissingCredentials("toloka: not permitted (403). Toloka blocks runs for teams that have not completed "
                                  "identity or business verification; finish it in the web app.")
    if e.status == 404:
        return LookupError(f"toloka: {what} not found (404)")
    if e.status == 429 and getattr(e, "retry_after", None) is None:
        from ..ratelimit import retry_after
        e.retry_after = retry_after(e.headers)
    return e


class Toloka(Adapter):
    card_id = "toloka"
    env = "TOLOKA_API_KEY"
    how_to_get = "platform.toloka.ai: open your company, API keys, Generate API key. Sent as 'Authorization: ApiKey <key>'."
    can_commission = True
    can_cancel = True
    status_path = ("run", "status")
    status_map = {"pending": "in_flight", "running": "in_flight", "succeeded": "delivered", "failed": "failed"}

    def _h(self, required=True):
        return {"Authorization": f"ApiKey {self.key(required)}", "Content-Type": "application/json"}

    def _call(self, method, path, what, body=None, params=None):
        try:
            if method == "GET":
                return http.get(BASE + path, headers=self._h(), params=params)
            return http.post(BASE + path, body=body if body is not None else {}, headers=self._h())
        except http.HTTPError as e:
            raise _err(e, what) from None

    # ---- ready calls

    def prepare(self, req, route, operation=None, pipeline_id=None, run_id=None):
        """The ready call. Default for a commission ask: the cost forecast (quote). operation="read" lists projects (a
        connection check), "order" starts the run, "status" polls it. Ids not given stay {PLACEHOLDERS}."""
        if route not in ("commission", None) and operation is None:
            return None
        op = operation or "quote"
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header Authorization (value 'ApiKey {key}')",
                    get_key_url=KEY_URL, docs_url=DOCS)
        h = {"Authorization": "ApiKey {" + self.env + "}", "Content-Type": "application/json"}
        pid = pipeline_id or "{TOLOKA_PIPELINE_ID}"
        need = (req or {}).get("need") or ""
        qty = (req or {}).get("quantity")
        if op == "read":
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/projects?limit=20", headers=h, **auth,
                                 notes="Lists your projects (read-only). Then GET /api/v2-beta/projects/{projectId}/pipelines "
                                       "for the pipeline that collects this data.")
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/pipelines/{pid}/cost-estimate", headers=h, body={},
                                 placeholders=None if pipeline_id else [PIPELINE], **auth,
                                 notes=f"Cost forecast for the pipeline over its bound dataset; starts nothing. Ask: {need!r}"
                                       + (f", {qty} items" if qty else "") + ". The item count comes from the pipeline's "
                                       "root itemLimit (set in Toloka). total.expected and total.worstCase are strings with "
                                       "no currency field, so treat them as indicative. A pipeline must already exist: the "
                                       "API cannot assemble one.")
        if op == "order":
            return prepared_call(self.card_id, "order", "POST", f"{BASE}/pipelines/{pid}/runs", headers=h, body={},
                                 placeholders=None if pipeline_id else [PIPELINE], **auth,
                                 notes="Starts a paid run against the team's prepaid balance. Get the person's approval of "
                                       "the cost forecast first. 400: lints not finished (retry once they finish); 403: "
                                       "team not verified; 409: a run is already active.")
        if op == "status":
            rid = run_id or "{TOLOKA_RUN_ID}"
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/runs/{rid}", headers=h, **auth,
                                 placeholders=None if run_id else [RUN],
                                 notes="Poll until succeeded or failed (a cancelled run reports failed, 'Run canceled'). "
                                       "Then GET /runs/{runId}/items, or POST /datasets/{datasetId}/exports for files.")
        return None

    # ---- running

    def commission(self, spec, confirm=False):
        """spec: pipeline_id (required: a pipeline already set up in Toloka). Without confirm: the cost forecast when the key
        is set (Toloka's own quote; it starts nothing), else the call that would be sent. confirm=True starts the run."""
        spec = spec or {}
        pid = spec.get("pipeline_id")
        missing = [m for m, ok in (("pipeline_id", pid), (self.env, self.configured())) if not ok]
        if not confirm:
            out = self._dry("POST", f"{BASE}/pipelines/{pid or '{TOLOKA_PIPELINE_ID}'}/runs", {}, self._h(required=False))
            if missing:
                out["missing"] = missing
                return out
            out["quote"] = self._call("POST", f"/pipelines/{pid}/cost-estimate", "pipeline", body={})
            out["note"] = ("Cost forecast from Toloka (strings, no currency field: indicative). Nothing was started. "
                           "Pass confirm=True to start the run; it is charged to the team's balance.")
            return out
        if not pid:
            raise ValueError("toloka: spec needs pipeline_id (a pipeline set up in Toloka)")
        return self._call("POST", f"/pipelines/{pid}/runs", "pipeline", body={})

    def status(self, order):
        rid = order["id"]
        run = self._call("GET", f"/runs/{rid}", "run")
        items = None
        if isinstance(run, dict) and run.get("status") == "succeeded":
            items = self._call("GET", f"/runs/{rid}/items", "run items", params={"limit": 100})
        return {"run": run, "items": items}

    def cancel(self, order):
        """POST /runs/{runId}/cancel. Asynchronous: the run later reports failed with 'Run canceled'. Completed items stay
        paid; 409 when the run cannot be cancelled."""
        return self._call("POST", f"/runs/{order['id']}/cancel", "run", body={})

    def deliverables(self, resp):
        """Completed run items. Toloka reports processing times, not capture times, so phenomenon_time stays None."""
        page = (resp or {}).get("items") if isinstance(resp, dict) else None
        out = []
        for it in (page or {}).get("items", []) if isinstance(page, dict) else []:
            st = it.get("state") or {}
            if st.get("status") != "completed":
                continue
            out.append(observation(self.card_id, "dataset-item", None, None, None, label=f"run item {it.get('index')}",
                                   result_time=st.get("finishedAt"), item_id=it.get("id"),
                                   dataset_item_id=it.get("datasetItemId"), output=st.get("output")))
        return out

    def spec_from_request(self, req, ext_id, deliver_by):
        """Toloka runs a pipeline the account already set up, so a protocol request alone can't name it: pipeline_id stays
        missing and the dry run says so. Toloka's run call takes no idempotency key."""
        return {k: v for k, v in {"need": req.get("need"), "quantity": req.get("quantity"),
                                  "pipeline_id": req.get("pipeline_id")}.items() if v is not None}
