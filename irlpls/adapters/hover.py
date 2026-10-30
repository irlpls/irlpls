"""Hover: send a capture request so the person at a property scans it with the Hover app; Hover turns the photos into
a 3D model, measurements and images. REST with an OAuth 2.0 authorization-code token in HOVER_ACCESS_TOKEN (valid 2
hours). HOVER_SANDBOX=1 sends everything to https://sandbox.hover.to instead of https://hover.to.

Sources (read 2026-10-28, https://developers.hover.to/llms.txt):
- OAuth: https://developers.hover.to/docs/oauth-20-authentication.md, .../reference/get-authorization-code.md,
  .../reference/generate-access-token.md (GET /oauth/authorize?response_type=code&client_id&redirect_uri, a person must
  click Allow, code valid 10 minutes; POST /oauth/token grant_type authorization_code or refresh_token, JSON or
  multipart body; access token 2 hours, refresh token valid until used; scope "all"; fails if the org's plan has
  integrations disabled).
- Create: .../reference/create-capture-request.md (POST /api/v2/capture_requests; capture_request[capturing_user_name],
  [capturing_user_email], [job_attributes][location_line_1], current_user_email or current_user_id; deliverable_id 2
  roof only, 3 complete, 5 total living area plus, 6 total living area, 7 capture only/photos, 8 interior; 201 with id,
  state, pending_job_id).
- Status: .../reference/show-capture-request-details.md (GET /api/v2/capture_requests/{id}/details) and
  .../docs/capture-requests-via-the-api.md (states new, connected, complete, deleted); job:
  .../reference/show-job-details.md (GET /api/v3/jobs/{job_id}: reconstruction_state, models[].state, artifacts, images)
  and .../docs/hovers-available-webhooks.md (job states working, completed, failed; job starts as draft).
- Cancel: .../reference/delete-capture-request.md (DELETE /api/v2/capture_requests/{capture_request_id}; only while the
  job is a draft, before photos are uploaded).
- Limits: .../docs/api-usage-and-limits.md (120 requests/min or 7,200/hour, 429; 60 s timeout).
- Test jobs: .../docs/testing-your-integration.md (PATCH /api/v2/jobs/{job_id}/set_test_state.json?state=complete|failed
  simulates a capture in development).

The authorization-code flow is documented in every ready call, never run here. A capture request invites a named person
(usually the homeowner) by email or SMS and charges the org's wallet per project, so it is an order: confirm=True only.
"""
import os

from .. import http
from . import _mcp
from .base import Adapter, observation, prepared_call

LIVE = "https://hover.to"
SANDBOX = "https://sandbox.hover.to"
DOCS = "https://developers.hover.to/docs/capture-requests-via-the-api"

DELIVERABLES = {"roof": 2, "complete": 3, "total_living_area_plus": 5, "total_living_area": 6, "photos": 7, "interior": 8}


def _base():
    return SANDBOX if os.environ.get("HOVER_SANDBOX") == "1" else LIVE


def _oauth(base):
    return {"grant": "authorization_code", "pkce": None, "dynamic_registration": False, "scopes": ["all"],
            "setup": "a Hover Team Admin creates an integration (Settings > Developer) for the client id and secret and "
                     "sets its redirect URI; the org's plan must allow integrations",
            "sign_in": "the person opens the authorization URL in a browser, logs in to Hover and clicks Allow; Hover "
                       "redirects to the redirect URI with ?code= (valid 10 minutes)",
            "authorize_url": f"{base}/oauth/authorize?response_type=code&client_id={{HOVER_CLIENT_ID}}"
                             f"&redirect_uri={{HOVER_REDIRECT_URI}}",
            "token_url": f"{base}/oauth/token",
            "token_body": {"grant_type": "authorization_code", "code": "{CODE}", "redirect_uri": "{HOVER_REDIRECT_URI}",
                           "client_id": "{HOVER_CLIENT_ID}", "client_secret": "{HOVER_CLIENT_SECRET}"},
            "refresh_body": {"grant_type": "refresh_token", "refresh_token": "{HOVER_REFRESH_TOKEN}",
                             "client_id": "{HOVER_CLIENT_ID}", "client_secret": "{HOVER_CLIENT_SECRET}"},
            "token_lifetime_s": 7200,
            "refresh_note": "each refresh returns a new pair and invalidates the old refresh token; store the new one"}


AUTH_NOTE = ("Auth: OAuth 2.0 authorization code (auth.oauth). Your person approves once in a browser at "
             "/oauth/authorize; exchange the code at POST /oauth/token for an access token (2 hours) and a refresh token. "
             "Rate limit 120 requests/min.")


def deliverable_for(need):
    n = (need or "").lower()
    if "interior" in n or "floor plan" in n:
        return DELIVERABLES["interior"]
    if "roof" in n:
        return DELIVERABLES["roof"]
    if any(w in n for w in ("photo", "picture", "image")) and not any(w in n for w in ("measure", "3d", "model")):
        return DELIVERABLES["photos"]
    return DELIVERABLES["complete"]


class Hover(Adapter):
    card_id = "hover"
    env = "HOVER_ACCESS_TOKEN"
    how_to_get = ("A Hover Team Admin creates an integration (Settings > Developer); the person approves it once at "
                  "https://hover.to/oauth/authorize and the code is exchanged at POST https://hover.to/oauth/token. "
                  "Set the access token here (it lasts 2 hours).")
    can_commission = True
    can_cancel = True

    def _h(self):
        return {"Authorization": "Bearer " + self.key()}

    def _pc(self, base, operation, method, url, **kw):
        pc = prepared_call(self.card_id, operation, method, url, headers={"Authorization": "Bearer {" + self.env + "}"},
                           auth_kind="oauth2", key_name=self.env, key_in="header Authorization: Bearer",
                           get_key_url=f"{base}/oauth/authorize", **kw)
        pc["auth"]["oauth"] = _oauth(base)
        return pc

    # ---- ready calls (offline)
    def prepare(self, req, route, operation=None):
        """route "commission": the capture request (default "order"), or operation="status" for its details."""
        if route != "commission":
            return None
        base = _base()
        op = operation or "order"
        if op == "status":
            u = f"{base}/api/v2/capture_requests/{{HOVER_CAPTURE_REQUEST_ID}}/details?id={{HOVER_CAPTURE_REQUEST_ID}}"
            return self._pc(base, "status", "GET", u, docs_url="https://developers.hover.to/reference/show-capture-request-details",
                            placeholders=[{"name": "HOVER_CAPTURE_REQUEST_ID", "what": "the capture request id",
                                           "where": "the id in the create response"}],
                            notes="state: new, connected, complete (captured) or deleted. Once pending_job_id is set, GET "
                                  "/api/v3/jobs/{pending_job_id}: reconstruction_state completed means models, measurements "
                                  "and images are ready. " + AUTH_NOTE)
        if op != "order":
            return None
        body = self._body(self.spec_from_request(req), placeholders=True)
        ph = [{"name": "CAPTURING_USER_NAME", "what": "name of the person who will scan the property (usually the owner)",
               "where": "ask your person"},
              {"name": "CAPTURING_USER_EMAIL", "what": "their email; Hover sends them the invitation",
               "where": "ask your person"},
              {"name": "HOVER_USER_EMAIL", "what": "the email of your person's Hover user who requests the capture",
               "where": "your person's Hover account"}]
        if "{ADDRESS}" in repr(body):
            ph.append({"name": "ADDRESS", "what": "street address of the property", "where": "ask your person"})
        return self._pc(base, "order", "POST", f"{base}/api/v2/capture_requests", body=body, docs_url=DOCS, placeholders=ph,
                        notes="Creates a draft job and emails (or texts, with capturing_user_phone) the named person an "
                              "invitation to scan the property with the Hover app. The project is charged to the org's "
                              "wallet at Hover's price for the deliverable (deliverable_id 2 roof, 3 complete, 7 photos, "
                              "8 interior). Only send with your person's approval and the capturing person's consent. "
                              "Cancel with DELETE /api/v2/capture_requests/{id} while the job is still a draft. "
                              "Sandbox: https://sandbox.hover.to. " + AUTH_NOTE)

    # ---- protocol-shim hook
    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"address": req.get("place"), "deliverable_id": deliverable_for(req.get("need")),
             "name": (req.get("need") or "")[:80] or None, "external_identifier": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _body(spec, placeholders=False):
        def ph(v, name):
            return v if v not in (None, "") else ("{" + name + "}" if placeholders else None)
        job = {"location_line_1": ph(spec.get("address"), "ADDRESS"), "location_line_2": spec.get("address_line_2"),
               "location_city": spec.get("city"), "location_region": spec.get("region"),
               "location_postal_code": spec.get("postal_code"), "deliverable_id": spec.get("deliverable_id", 3),
               "name": spec.get("name"), "external_identifier": spec.get("external_identifier"),
               "wallet_id": spec.get("wallet_id")}
        cr = {"capturing_user_name": ph(spec.get("capturing_user_name"), "CAPTURING_USER_NAME"),
              "capturing_user_email": ph(spec.get("capturing_user_email"), "CAPTURING_USER_EMAIL"),
              "capturing_user_phone": spec.get("capturing_user_phone"), "signup_type": spec.get("signup_type"),
              "job_attributes": {k: v for k, v in job.items() if v is not None}}
        b = {"capture_request": {k: v for k, v in cr.items() if v is not None}}
        if spec.get("current_user_id") is not None:
            b["current_user_id"] = spec["current_user_id"]
        else:
            email = ph(spec.get("current_user_email"), "HOVER_USER_EMAIL")
            if email is not None:
                b["current_user_email"] = email
        return b

    # ---- running
    def _err(self, e):
        _mcp.raise_for_auth(self.card_id, e, "The access token lasts 2 hours: refresh it at POST /oauth/token "
                                             "(grant_type refresh_token) and set HOVER_ACCESS_TOKEN.")

    def commission(self, spec, confirm=False):
        """spec: address (line 1), city, region, postal_code, capturing_user_name, capturing_user_email,
        capturing_user_phone (optional, for SMS), current_user_email or current_user_id (your Hover user),
        deliverable_id (2 roof, 3 complete, 7 photos, 8 interior), name, external_identifier, wallet_id."""
        base = _base()
        body = self._body(spec)
        cr = body["capture_request"]
        missing = [m for m, ok in (("address: the property's street address", "location_line_1" in cr["job_attributes"]),
                                   ("capturing_user_name: who will scan the property", "capturing_user_name" in cr),
                                   ("capturing_user_email: where Hover sends the invitation", "capturing_user_email" in cr),
                                   ("current_user_email or current_user_id: your Hover user",
                                    "current_user_email" in body or "current_user_id" in body)) if not ok]
        url = f"{base}/api/v2/capture_requests"
        if not confirm:
            out = self._dry("POST", url, body, {"Authorization": "Bearer <" + self.env + ">"})
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise ValueError("hover: cannot create a capture request without " + "; ".join(missing))
        try:
            return http.post(url, body=body, headers=self._h())
        except http.HTTPError as e:
            self._err(e)

    def status(self, order):
        """The capture request's details, plus its job once one exists: {"capture_request": ..., "job": ...}."""
        base = _base()
        cid = order.get("id") if isinstance(order, dict) else order
        try:
            cr = http.get(f"{base}/api/v2/capture_requests/{cid}/details", params={"id": cid}, headers=self._h())
            jid = (cr or {}).get("pending_job_id") if isinstance(cr, dict) else None
            job = http.get(f"{base}/api/v3/jobs/{jid}", headers=self._h()) if jid else None
        except http.HTTPError as e:
            self._err(e)
        out = {"capture_request": cr}
        if job is not None:
            out["job"] = job
        return out

    def cancel(self, order):
        """Delete the capture request (documented; works only while its job is a draft, before photos are uploaded)."""
        cid = order.get("id") if isinstance(order, dict) else order
        try:
            http.request("DELETE", f"{_base()}/api/v2/capture_requests/{cid}", headers=self._h())
        except http.HTTPError as e:
            self._err(e)
        return {"id": cid, "cancelled": True}

    # Capture request states (new, connected, complete, deleted) and job states (draft, working, completed, failed),
    # per Hover's docs. "complete" on a capture request means the property was scanned, not that the model is ready, so
    # it stays in flight until the job's reconstruction_state is completed.
    status_map = {"new": "in_flight", "connected": "in_flight", "complete": "in_flight", "deleted": "failed",
                  "draft": "in_flight", "working": "in_flight", "completed": "delivered", "failed": "failed"}

    def provider_status(self, resp):
        r = resp if isinstance(resp, dict) else {}
        job = r.get("job") if isinstance(r.get("job"), dict) else None
        if job and isinstance(job.get("reconstruction_state"), str):
            return job["reconstruction_state"]
        cr = r.get("capture_request") if isinstance(r.get("capture_request"), dict) else r
        return cr.get("state") if isinstance(cr.get("state"), str) else None

    def deliverables(self, resp):
        """For each completed model: its measurements (JSON and PDF links) and its photos. Hover documents no capture
        time per image, so phenomenon_time stays None (the image's created_at is kept as stored_at). The job's address
        coordinates are Hover's geocode of the address, not a capture fix."""
        job = (resp or {}).get("job") if isinstance(resp, dict) else None
        if not isinstance(job, dict) or job.get("reconstruction_state") != "completed":
            return []
        addr = job.get("address") or {}
        lat, lon = _num(addr.get("latitude")), _num(addr.get("longitude"))
        out = []
        for m in job.get("models") or []:
            if not isinstance(m, dict) or m.get("state") != "complete":
                continue
            meas = ((m.get("artifacts") or {}).get("measurements") or {})
            for fmt in ("json", "pdf"):
                if isinstance(meas.get(fmt), str):
                    out.append(observation(self.card_id, "measurements", None, lat, lon, url=meas[fmt],
                                           label=f"{m.get('name') or 'model'} measurements ({fmt.upper()})",
                                           result_time=m.get("updated_at"), model_id=m.get("id"), job_id=job.get("id")))
            for img in m.get("images") or []:
                if isinstance(img, dict) and isinstance(img.get("url"), str):
                    # created_at is when Hover stored the image, not a documented capture time: kept apart
                    out.append(observation(self.card_id, "photo", None, lat, lon, url=img["url"],
                                           label=f"{m.get('name') or 'model'} photo", model_id=m.get("id"),
                                           job_id=job.get("id"), stored_at=img.get("created_at")))
        return out


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
