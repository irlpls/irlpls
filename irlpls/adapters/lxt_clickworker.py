"""clickworker (LXT) Marketplace API v2: send a task, built from a task template on your account, to the clickworker crowd.
Credentials in CLICKWORKER_CREDENTIALS as "email:password" (HTTP Basic). API access is granted by clickworker support.

Sources (read 2026-10-28):
- Marketplace API v2 page, https://www.clickworker.com/clickworker-marketplace-api-v2/
- OpenAPI 3.0.3, version 2.0.42, https://www.clickworker.com/mpapi/cw-mp-api-v2.yaml. Servers
  https://api-production.clickworker.com and https://api-sandbox.clickworker.com (no active workers), paths under
  /api/marketplace/v2. basicAuth "with your customer email and password". GET /clickworkers?api_method=count_in_range
  (latitude, longitude, radius in km, minimum 10; response {"clickworker_count": n}); GET /products; POST /customer/tasks
  (body {"task": {customer_ref, due_date, template.link[{href, rel: task_template}], input.items[{code, content}],
  notifications}}; "Tasks are automatically confirmed and distributed to the crowd upon creation"); GET
  /customer/tasks/{taskId}; task states created, queued, running, finished, timedout, cancelled, feedback (awaiting your
  buyoff); Task.expire sets state "expired". Errors listed: 401, 404, 422.

Unverified (fixtures are documented-shape): the OpenAPI gives no response body for Task.show or Task.create beyond "the
created task with its ID, state, inputs, and pricing information"; this adapter reads {"task": {"id", "state", ...}},
the same envelope as the request and the notification example ({"task": {"id": 12345}}).
There is no quote call: creating a task sends it to workers at once, so commission() never runs without confirm=True.
Photo capture and store checks are custom products set up by clickworker Operations; a template must exist first.
"""
import base64
import os

from .. import http
from .base import Adapter, MissingCredentials, observation, prepared_call

PROD = "https://api-production.clickworker.com"
SANDBOX = "https://api-sandbox.clickworker.com"
PATH = "/api/marketplace/v2"
DOCS = "https://www.clickworker.com/clickworker-marketplace-api-v2/"
KEY_URL = "https://www.clickworker.com/contact-for-customers/"
TEMPLATE = {"name": "CLICKWORKER_TEMPLATE_CODE", "what": "the code of a task template on your account (a custom product "
            "for photo capture is configured by clickworker Operations)", "where": "GET /api/marketplace/v2/customer/task_templates"}
INPUT = {"name": "CLICKWORKER_INPUT_CODE", "what": "an item_code from the template's form with is_output false",
         "where": "GET /api/marketplace/v2/customer/task_templates/{templateCode}"}
TASK = {"name": "CLICKWORKER_TASK_ID", "what": "the task id returned on creation", "where": "POST /customer/tasks response"}


def _err(e, what):
    if not isinstance(e, http.HTTPError):
        return e
    if e.status in (401, 403):
        return MissingCredentials(f"lxt-clickworker: credentials refused ({e.status}). Set CLICKWORKER_CREDENTIALS to "
                                  "'email:password' of a customer account with API access (ask clickworker support).")
    if e.status == 404:
        return LookupError(f"lxt-clickworker: {what} not found (404)")
    if e.status == 429 and getattr(e, "retry_after", None) is None:
        from ..ratelimit import retry_after
        e.retry_after = retry_after(e.headers)
    return e


class LXTClickworker(Adapter):
    card_id = "lxt-clickworker"
    env = "CLICKWORKER_CREDENTIALS"
    how_to_get = ("'email:password' of a clickworker customer account; ask support for API access "
                  "(https://www.clickworker.com/contact-for-customers/). CLICKWORKER_SANDBOX=1 uses the sandbox host.")
    can_commission = True
    status_path = ("task", "state")
    status_map = {"created": "in_flight", "queued": "in_flight", "running": "in_flight", "feedback": "in_flight",
                  "finished": "delivered", "timedout": "failed", "cancelled": "failed", "expired": "failed"}

    def base(self):
        return (SANDBOX if os.environ.get("CLICKWORKER_SANDBOX") == "1" else PROD) + PATH

    def _h(self, required=True):
        cred = self.key(required)
        return {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode(), "Accept": "application/json",
                "Content-Type": "application/json"}

    def _call(self, method, path, what, body=None, params=None):
        try:
            if method == "GET":
                return http.get(self.base() + path, headers=self._h(), params=params)
            return http.post(self.base() + path, body=body, headers=self._h())
        except http.HTTPError as e:
            raise _err(e, what) from None

    def _body(self, spec, base):
        items = [{"code": c, "content": str(v)} for c, v in (spec.get("inputs") or {}).items()]
        task = {"customer_ref": spec.get("customer_ref") or spec.get("idempotency_key"), "due_date": spec.get("due_date"),
                "template": {"link": [{"href": f"{base}/customer/task_templates/{spec.get('template_code') or '{CLICKWORKER_TEMPLATE_CODE}'}",
                                       "rel": "task_template", "type": "application/json"}]},
                "input": {"items": items}}
        if spec.get("notify_url"):
            task["notifications"] = [{"event": ev, "callback_url": spec["notify_url"], "callback_method": "POST",
                                      "payload_format": "JSON"} for ev in ("TASK_COMPLETED", "TASK_TIMEDOUT", "CUSTOMER_INPUT_REQUIRED")]
        return {"task": {k: v for k, v in task.items() if v is not None}}

    # ---- ready calls

    def prepare(self, req, route, operation=None, template_code=None, task_id=None, radius_km=10):
        """Default for a commission ask: the worker count near the place (read; nothing to buy). operation="order" builds
        the task (it goes to workers at once), "status" reads it back."""
        if route not in ("commission", None) and operation is None:
            return None
        req = req or {}
        base = self.base()
        h = {"Authorization": "Basic {" + self.env + "}", "Accept": "application/json", "Content-Type": "application/json"}
        auth = dict(auth_kind="basic", key_name=self.env, get_key_url=KEY_URL, docs_url=DOCS,
                    key_in="header Authorization: 'Basic ' + base64 of {CLICKWORKER_CREDENTIALS} (email:password); curl -u does this")
        op = operation or ("read" if req.get("lat") is not None and req.get("lon") is not None else "order")
        if op == "read":
            if req.get("lat") is not None and req.get("lon") is not None:
                url = (f"{base}/clickworkers?api_method=count_in_range&latitude={req['lat']:.4f}&longitude={req['lon']:.4f}"
                       f"&radius={max(10, int(radius_km))}")
                note = ("Counts clickworkers within the radius (km, minimum 10): {\"clickworker_count\": n}. Workers exist, "
                        "not that one will take the task.")
            else:
                url, note = f"{base}/products", "Products (task types) your account can use; photo capture is a custom product."
            return prepared_call(self.card_id, "read", "GET", url, headers=h, **auth, notes=note)
        if op == "order":
            spec = {"template_code": template_code, "due_date": req.get("deliver_by"),
                    "inputs": {"{CLICKWORKER_INPUT_CODE}": req.get("need") or ""}}
            ph = ([] if template_code else [TEMPLATE]) + [INPUT]
            return prepared_call(self.card_id, "order", "POST", f"{base}/customer/tasks", headers=h, body=self._body(spec, base),
                                 placeholders=ph, **auth,
                                 notes="Creating the task confirms it and sends it to workers at once; there is no quote "
                                       "call (fees show when the order is submitted). Add customer_ref (your own id) to "
                                       "find it with GET /customer/tasks/search. Needs the person's approval.")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{base}/customer/tasks/{task_id or '{CLICKWORKER_TASK_ID}'}",
                                 headers=h, placeholders=None if task_id else [TASK], **auth,
                                 notes="State: created, queued, running, finished, timedout, cancelled, or feedback "
                                       "(awaiting your accept or reject: PUT /customer/tasks/{taskId}/accept or /reject).")
        return None

    # ---- running

    def workers_near(self, lat, lon, radius_km=10):
        """Workers within radius_km (minimum 10) of the point: an int, or None if the response has no count."""
        r = self._call("GET", "/clickworkers", "worker count", params={"api_method": "count_in_range", "latitude": f"{lat:.4f}",
                                                                       "longitude": f"{lon:.4f}", "radius": max(10, int(radius_km))})
        n = r.get("clickworker_count") if isinstance(r, dict) else None
        return n if isinstance(n, int) else None

    def commission(self, spec, confirm=False):
        """spec: template_code (required), inputs {item_code: content} (required; the template's input fields),
        customer_ref (defaults to the broker's idempotency_key), due_date (ISO 8601), notify_url. No quote exists, so
        without confirm this only returns the call (plus the worker count near lat/lon when credentials are set)."""
        spec = spec or {}
        base = self.base()
        body = self._body(spec, base)
        missing = [m for m, ok in (("template_code", spec.get("template_code")), ("inputs", spec.get("inputs")),
                                   (self.env, self.configured())) if not ok]
        if not confirm:
            out = self._dry("POST", f"{base}/customer/tasks", body, self._h(required=False))
            out["note"] = ("Nothing was sent. clickworker has no quote call: creating the task sends it to workers at once. "
                           "Pass confirm=True to create it; worker payments and fees are charged to your account.")
            if missing:
                out["missing"] = missing
            if self.configured() and spec.get("lat") is not None and spec.get("lon") is not None:
                try:
                    out["workers_in_range"] = self.workers_near(spec["lat"], spec["lon"], spec.get("radius_km", 10))
                except (http.HTTPError, LookupError, MissingCredentials) as e:
                    out["warnings"] = [f"worker count unavailable: {type(e).__name__}"]
            return out
        if missing:
            raise ValueError(f"lxt-clickworker: spec is missing {', '.join(missing)}")
        return self._call("POST", "/customer/tasks", "task template", body=body)

    def status(self, order):
        t = order.get("task") if isinstance(order.get("task"), dict) else order
        return self._call("GET", f"/customer/tasks/{t['id']}", "task")

    def deliverables(self, resp):
        """A finished task's result, as clickworker returns it. No capture time is given, so phenomenon_time is None."""
        t = (resp or {}).get("task") if isinstance(resp, dict) else None
        if not isinstance(t, dict) or t.get("result") in (None, "", [], {}):
            return []
        return [observation(self.card_id, "task-result", None, None, None, value=t.get("result"),
                            label=f"task {t.get('id')} result", task_id=t.get("id"), customer_ref=t.get("customer_ref"))]

    def spec_from_request(self, req, ext_id, deliver_by):
        """A template code and its input codes belong to the account, so they stay missing; ext_id becomes customer_ref."""
        return {k: v for k, v in {"customer_ref": ext_id, "due_date": deliver_by, "lat": req.get("lat"),
                                  "lon": req.get("lon")}.items() if v is not None}
