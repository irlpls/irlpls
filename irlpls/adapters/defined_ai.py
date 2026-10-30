"""Defined.ai (formerly DefinedCrowd): enterprise data collection and annotation. An order clones one of the account's
project templates; the project starts once its input units are uploaded. Enterprise account by contract (sales);
OAuth 2.0 client credentials (Access Key ID + Secret from the Enterprise Portal), scope PublicAPIv2.

Sources (read 2026-10-28):
- Developer portal: https://developers.definedcrowd.com/ (Enterprise account, OAuth client credentials, base URL)
- OpenAPI 3.0.1: https://api.production-na01.definedcrowd.com/swagger/docs/external (paths, clone body
  CloneProjectConfigurationViewModel, ProjectDetails, deliverables, 202/400/401/404 codes)

Not documented: the values of a project's `status` string (the spec types it as a free string), so status_map is
empty and every status stays in_flight, passed through verbatim. No public price: quotes come from sales. The named
templates are audio and text evaluation; no image or video collection template is documented.
"""
import os
import uuid

from .. import http
from . import _a7
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, observation, prepared_call

BASE = "https://api.production-na01.definedcrowd.com/v2.0/public"
TOKEN_URL = "https://auth.definedcrowd.com/connect/token"
SCOPE = "PublicAPIv2"
DOCS = "https://developers.definedcrowd.com/"
OPENAPI = "https://api.production-na01.definedcrowd.com/swagger/docs/external"
SECRET_ENV = "DEFINED_AI_CLIENT_SECRET"
SOURCE_ENV = "DEFINED_AI_SOURCE_PROJECT_ID"
ID_NS = uuid.UUID("6f1b2c1e-5a7d-4c0e-9b1a-a7a7a7a7a7a7")   # turns an idempotency key into a stable targetProjectId
AUTH_NOTES = (f"OAuth 2.0 client credentials: POST {TOKEN_URL} (form: grant_type=client_credentials, client_id, "
              f"client_secret, scope={SCOPE}) with the Access Key ID and Secret from the Enterprise Portal "
              "(enterprise.definedcrowd.com); send the JWT as 'Authorization: Bearer <token>'.")


class DefinedAI(Adapter):
    card_id = "defined-ai"
    env = "DEFINED_AI_CLIENT_ID"
    how_to_get = (f"Enterprise account via sales@defined.ai; create an Access Key ID ({env}) and Secret ({SECRET_ENV}) "
                  "in the Enterprise Portal, and set the template project to clone in DEFINED_AI_SOURCE_PROJECT_ID.")
    can_commission = True
    can_cancel = True

    status_path = ("projectDetails", "status")
    status_map = {}   # Defined.ai documents no status values; unmapped statuses stay in_flight, verbatim

    _tokens = TokenCache()

    def configured(self):
        return bool(os.environ.get(self.env) and os.environ.get(SECRET_ENV))

    def _h(self):
        cid, sec = os.environ.get(self.env), os.environ.get(SECRET_ENV)
        if not cid or not sec:
            raise MissingCredentials(f"{self.card_id}: set {self.env} and {SECRET_ENV}. {self.how_to_get}")
        return {"Authorization": "Bearer " + self._tokens.client_credentials(TOKEN_URL, cid, sec, scope=SCOPE)}

    def _get(self, path, what, params=None):
        return _a7.call(self.card_id, what, lambda: http.get(BASE + path, headers=self._h(), params=params),
                        hint=self.how_to_get)

    # ------------------------------------------------------------------------------------------------ the request
    def _clone(self, spec):
        src = spec.get("source_project_id") or os.environ.get(SOURCE_ENV)
        name = str(spec.get("project_name") or spec.get("title") or spec.get("notes") or spec.get("need") or "").strip()[:80]
        body = {"projectName": name or None, "desiredDeliveryDate": spec.get("desired_delivery_date"),
                "metatags": spec.get("metatags")}
        key = spec.get("idempotency_key") or spec.get("external_id")
        if key:
            body["targetProjectId"] = str(uuid.uuid5(ID_NS, str(key)))
        missing = [m for m, ok in (("source_project_id (a template project in your account; GET /projects lists them)", src),
                                   ("project_name (up to 80 characters)", name)) if not ok]
        return src, {k: v for k, v in body.items() if v is not None}, missing

    def spec_from_request(self, req, ext_id, deliver_by):
        spec = {"project_name": (req.get("need") or "")[:80] or None, "external_id": ext_id,
                "desired_delivery_date": str(deliver_by)[:10] if deliver_by else None}
        return {k: v for k, v in spec.items() if v is not None}

    # ---------------------------------------------------------------------------------------------- ready calls
    def _auth(self, **kw):
        return dict(headers={"Authorization": "Bearer {DEFINED_AI_ACCESS_TOKEN}", **kw.pop("headers", {})},
                    auth_kind="oauth2", key_name="DEFINED_AI_ACCESS_TOKEN", key_in="header Authorization: Bearer",
                    get_key_url="https://enterprise.definedcrowd.com", docs_url=OPENAPI, **kw)

    def prepare(self, req, route):
        if route != "commission":
            return None
        _, body, _ = self._clone({"project_name": req.get("need"), "desired_delivery_date": req.get("desired_delivery_date")})
        return prepared_call(self.card_id, "order", "POST", BASE + "/projects/{DEFINED_AI_SOURCE_PROJECT_ID}/clone",
                             body=body, **self._auth(
                                 headers={"Content-Type": "application/json"},
                                 notes=AUTH_NOTES + " Answers 202 with targetProjectId; then GET "
                                       "/projects/{id}/inputUnitsSchema and POST /projects/{id}/inputUnits. The project "
                                       "starts when the upload finishes. No public price: ask sales for a quote first.",
                                 placeholders=[{"name": "DEFINED_AI_SOURCE_PROJECT_ID",
                                                "what": "the template project to clone (a UUID)",
                                                "where": "GET /projects, or your account manager"}]))

    def prepared_calls(self, req, project_id=None):
        """read (the account's projects), order (clone), status."""
        read = prepared_call(self.card_id, "read", "GET", BASE + "/projects?pageNumber=1&itemsPerPage=50",
                             **self._auth(notes=AUTH_NOTES))
        pid = project_id or "{PROJECT_ID}"
        status = prepared_call(self.card_id, "status", "GET", f"{BASE}/projects/{pid}/status",
                               **self._auth(notes=AUTH_NOTES + " Deliverables: GET /projects/{id}/deliverables, then "
                                                               ".../deliverables/{deliverableId}/download."))
        return {"read": read, "order": self.prepare(req, "commission"), "status": status}

    # -------------------------------------------------------------------------------------------------- reads
    def projects(self, page=1, per_page=50):
        """The account's projects: id, name, projectType, status, dateTimeCreated, counts."""
        r = self._get("/projects", "projects", params={"pageNumber": page, "itemsPerPage": per_page}) or {}
        return [p for p in r.get("projects") or [] if isinstance(p, dict)]

    # ------------------------------------------------------------------------------------------------- orders
    def commission(self, spec, confirm=False):
        src, body, missing = self._clone(spec)
        url = f"{BASE}/projects/{src or '{DEFINED_AI_SOURCE_PROJECT_ID}'}/clone"
        dry = self._dry("POST", url, body, {"Authorization": "Bearer <token>"})
        dry["note"] = ("Nothing was sent. Cloning creates a project; it starts when input units are uploaded and "
                       "bills under your contract. No public price: ask sales for a quote.")
        if missing:
            dry["missing"] = missing
        if not confirm:
            return dry
        if missing:
            raise ValueError(f"{self.card_id}: missing {', '.join(missing)}")
        try:
            uuid.UUID(str(src))
        except ValueError:
            raise ValueError(f"{self.card_id}: source_project_id must be a UUID") from None
        r = _a7.call(self.card_id, "clone", lambda: http.post(url, body=body, headers=self._h()), hint=self.how_to_get)
        r = dict(r) if isinstance(r, dict) else {}
        r.setdefault("id", r.get("targetProjectId") or body.get("targetProjectId"))
        return r

    def status(self, order):
        """GET /projects/{id}/status, plus the deliverables list (GET /projects/{id}/deliverables)."""
        pid = str((order or {}).get("targetProjectId") or (order or {}).get("id") or "")
        try:
            uuid.UUID(pid)
        except ValueError:
            raise ValueError(f"{self.card_id}: the order needs its project id (a UUID)") from None
        st = self._get(f"/projects/{pid}/status", "status") or {}
        dl = self._get(f"/projects/{pid}/deliverables", "deliverables") or {}
        return {**st, "deliverables": dl.get("deliverables") or [], "projectId": pid}

    def cancel(self, order):
        """PUT /projects/{id}/cancel (documented; refunds follow the contract)."""
        pid = str((order or {}).get("targetProjectId") or (order or {}).get("id") or "")
        uuid.UUID(pid)
        return _a7.call(self.card_id, "cancel", lambda: http.request("PUT", f"{BASE}/projects/{pid}/cancel", headers=self._h()))

    def deliverables(self, resp):
        """One result per deliverable file. url is the download endpoint (it needs your bearer token);
        phenomenon_time is None: Defined.ai gives the file's creation time, not a capture time."""
        out = []
        for d in (resp or {}).get("deliverables") or []:
            if not isinstance(d, dict) or not d.get("deliverableId"):
                continue
            pid = d.get("projectId") or resp.get("projectId")
            out.append(observation(self.card_id, "dataset", None, None, None, label=d.get("fileName") or d.get("type"),
                                   url=f"{BASE}/projects/{pid}/deliverables/{d['deliverableId']}/download",
                                   result_time=d.get("createdTimestamp"), partial=d.get("isPartial"),
                                   deliverable_type=d.get("type")))
        return out
