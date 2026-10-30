"""FlyGuys: order a drone mission (aerial photos, video, mapping) from a US pilot network. OAuth 2.0 client
credentials (FLYGUYS_CLIENT_ID, FLYGUYS_CLIENT_SECRET) from the developer portal dev-connect.flyguys.com; whether
sign-up is approved by FlyGuys was not verified. Every mission needs a package id from the catalogue FlyGuys gives
each client (FLYGUYS_PACKAGE_ID or spec["package_id"]); the docs' sample uses "demo-catalog-aerial".

- Token: POST /token, form grant_type=client_credentials, client_id, client_secret. Then send
  "Authorization: Bearer <token>" and a `client_id` header.
- Order: POST /Jobs (mission JSON: package.id, location {name, address, coordinate, annotations}, external_id,
  reference_id, instructions, onsite_contact, scheduled_at_start/end). The response wraps the job in `response`.
- Status: GET /Jobs/{id} (the job in `mission`). Files: GET /Files/{jobId} lists file names (downloads need the
  temporary storage credentials from /S3Credentials or /AzureStorageCredentials).
There is no quote or price endpoint (FAQ: "FlyGuys does not offer set packages"; pricing is per project), so a dry run
sends nothing and offers are never firm. No cancel call is documented (POST /Mission documents put_on_hold only).

Sources (read 2026-10-28): Swagger https://api.flyguys.com/swagger/docs/v1 (UI https://api.flyguys.com/swagger/ui/index):
/token, /Jobs, /Jobs/{id}, /Mission, /Files/{jobId}, request and response examples, 401/404 bodies.
Fixtures in tests/fixtures/adapters/flyguys/ follow those examples (fixture: documented-shape).
"""
import os
import uuid

from .. import http, ratelimit
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, prepared_call

BASE = "https://api.flyguys.com"
TOKEN_URL = BASE + "/token"
DOCS = "https://api.flyguys.com/swagger/ui/index"
GET_KEY = "https://dev-connect.flyguys.com"

_TOKENS = TokenCache()


class FlyGuys(Adapter):
    card_id = "flyguys"
    env = "FLYGUYS_CLIENT_SECRET"
    client_env = "FLYGUYS_CLIENT_ID"
    how_to_get = ("Sign up at the FlyGuys developer portal https://dev-connect.flyguys.com for a client id and secret "
                  "(FLYGUYS_CLIENT_ID, FLYGUYS_CLIENT_SECRET); FlyGuys gives each client its package catalogue "
                  "(FLYGUYS_PACKAGE_ID).")
    can_commission = True

    def configured(self):
        return bool(os.environ.get(self.env)) and bool(os.environ.get(self.client_env))

    def _h(self):
        cid = os.environ.get(self.client_env)
        if not cid:
            raise MissingCredentials(f"{self.card_id}: set {self.client_env}. {self.how_to_get}")
        tok = _TOKENS.client_credentials(TOKEN_URL, cid, self.key())
        return {"Authorization": "Bearer " + tok, "client_id": cid}

    def _send(self, method, path, body=None):
        try:
            return http.request(method, BASE + path, body=body, headers=self._h())
        except http.HTTPError as e:
            raise map_error(self.card_id, e) from None

    def body(self, spec):
        """POST /Jobs body. spec: lat, lon, address, name, need/instructions, package_id, external_id, reference_id,
        portfolio, annotations (GeoJSON FeatureCollection with catalog_item_ids), scheduled_at_start/end."""
        loc = {"name": (spec.get("name") or spec.get("address") or spec.get("need") or "irlpls mission")[:256]}
        if spec.get("address"):
            loc["address"] = {"address": spec["address"]}
        if spec.get("lat") is not None and spec.get("lon") is not None:
            loc["coordinate"] = {"latitude": float(spec["lat"]), "longitude": float(spec["lon"])}
        if spec.get("annotations"):
            loc["annotations"] = spec["annotations"]
        pkg = spec.get("package_id") or os.environ.get("FLYGUYS_PACKAGE_ID")
        b = {"package": {"id": pkg} if pkg else None, "location": loc, "external_id": spec.get("external_id"),
             "reference_id": spec.get("reference_id"), "portfolio": spec.get("portfolio"),
             "instructions": spec.get("instructions") or spec.get("need"),
             "scheduled_at_start": spec.get("scheduled_at_start"), "scheduled_at_end": spec.get("scheduled_at_end")}
        return {k: v for k, v in b.items() if v is not None}

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "address": req.get("place"), "need": req.get("need"),
             "external_id": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _missing(body):
        m = []
        if not body.get("package"):
            m.append("package_id: FlyGuys missions need a package id from your FlyGuys catalogue (FLYGUYS_PACKAGE_ID)")
        loc = body.get("location") or {}
        if not loc.get("address") and not loc.get("coordinate"):
            m.append("address: FlyGuys needs the site's address or coordinates")
        return m

    def commission(self, spec, confirm=False):
        """confirm=False sends nothing (FlyGuys has no quote endpoint). confirm=True creates the mission (POST /Jobs);
        FlyGuys prices per project, so the cost is settled with FlyGuys, not returned here."""
        body = self.body(spec)
        missing = self._missing(body)
        if not confirm:
            out = self._dry("POST", f"{BASE}/Jobs", body, {"Authorization": "***", "client_id": "***"})
            out["note"] = ("Nothing was sent. FlyGuys has no quote endpoint; pricing is per project. confirm=True "
                           "creates the mission.")
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise ValueError("flyguys: " + "; ".join(missing))
        body.setdefault("external_id", str(uuid.uuid4()))
        return self._send("POST", "/Jobs", body)

    def status(self, order):
        return self._send("GET", f"/Jobs/{_job_id(order)}")

    def files(self, order):
        """GET /Files/{jobId}: file names and catalog item ids (no download URLs; those need storage credentials)."""
        return self._send("GET", f"/Files/{_job_id(order)}")

    def prepare(self, req, route, operation="order", order_id=None):
        if route != "commission":
            return None
        hdr = {"Authorization": "Bearer {FLYGUYS_ACCESS_TOKEN}", "client_id": "{FLYGUYS_CLIENT_ID}"}
        ph = [{"name": "FLYGUYS_ACCESS_TOKEN", "what": "a bearer token",
               "where": "POST https://api.flyguys.com/token, form grant_type=client_credentials, client_id, client_secret"},
              {"name": "FLYGUYS_CLIENT_ID", "what": "your client id", "where": "the FlyGuys developer portal"}]
        auth = dict(auth_kind="oauth2", key_name=self.env, key_in="form field client_secret at POST /token",
                    get_key_url=GET_KEY, docs_url=DOCS)
        token_note = "OAuth 2.0 client credentials: POST " + TOKEN_URL + " (form) for the bearer token. "
        if operation == "status":
            if not order_id:
                ph.append({"name": "FLYGUYS_JOB_ID", "what": "the job id", "where": "response.id from POST /Jobs"})
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/Jobs/{order_id or '{FLYGUYS_JOB_ID}'}", headers=hdr,
                                 placeholders=ph, **auth,
                                 notes=token_note + "The job is in `mission`; its `status` and `activity_logs` show "
                                       "progress. Files: GET /Files/{jobId}.")
        if operation != "order":
            return None
        body = self.body(self.spec_from_request(req))
        if not body.get("package"):
            body["package"] = {"id": "{FLYGUYS_PACKAGE_ID}"}
            ph.append({"name": "FLYGUYS_PACKAGE_ID", "what": "the mission package", "where": "the catalogue FlyGuys gives you"})
        loc = body["location"]
        if not loc.get("address") and not loc.get("coordinate"):
            loc["address"] = {"address": "{ADDRESS}"}
            ph.append({"name": "ADDRESS", "what": "the site's street address", "where": "ask your person"})
        body["external_id"] = "{EXTERNAL_ID}"
        ph.append({"name": "EXTERNAL_ID", "what": "your unique id for this mission", "where": "generate one (a UUID)"})
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/Jobs", headers=hdr, body=body, placeholders=ph,
                             **auth, notes=token_note + "Creates the mission. No quote endpoint exists and pricing is per "
                                                        "project, so agree the price with FlyGuys first.")

    # Only statuses that appear in the FlyGuys docs' own examples (created, confirmed, put_on_hold). The docs list no
    # completion or cancellation status, so none is mapped: anything else stays in flight as FlyGuys sent it.
    status_map = {"created": "in_flight", "confirmed": "in_flight", "put_on_hold": "in_flight"}

    def provider_status(self, resp):
        if isinstance(resp, dict):
            for k in ("mission", "response"):
                if isinstance(resp.get(k), dict):
                    resp = resp[k]
                    break
        return super().provider_status(resp)


def _job_id(order):
    if isinstance(order, dict):
        inner = order.get("response") or order.get("mission") or {}
        v = (inner.get("id") if isinstance(inner, dict) else None) or order.get("id")
    else:
        v = order
    if not v:
        raise ValueError("flyguys: the order has no job id")
    return str(v)


def map_error(card_id, e):
    """401/403 → MissingCredentials; the rest stay HTTPError with `retry_after` set from Retry-After on 429/503."""
    if e.status in (401, 403):
        _TOKENS.clear()
        return MissingCredentials(f"{card_id}: the provider refused the token (HTTP {e.status}); check "
                                  "FLYGUYS_CLIENT_ID and FLYGUYS_CLIENT_SECRET.")
    err = http.HTTPError(e.status, e.url, e.body, e.headers)
    err.retry_after = ratelimit.retry_after(e.headers) if e.status in (429, 503) else None
    return err
