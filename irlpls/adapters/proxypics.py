"""ProxyPics: send a nearby crowd photographer to a US address to take photos in-app (geofenced, timestamped, camera
roll locked, per the provider). Key in PROXYPICS_API_KEY (self-serve; sandbox available). Usually under 24 hours.

Docs: https://docs.proxypics.com and its OpenAPI spec https://docs.proxypics.com/public/swagger.json (POST /photo-requests,
GET /photo-requests/{id}, GET /photo-request-templates; header x-api-key). The response to a create carries `cost` in
cents; there is no separate quote call. Sandbox: https://sandbox.proxypics.com/api/v3."""
from .. import http
from .base import Adapter, MissingDetails, observation, pick_operation, prepared_call, street_address

BASE = "https://api.proxypics.com/api/v3"


class ProxyPics(Adapter):
    card_id = "proxypics"
    env = "PROXYPICS_API_KEY"
    how_to_get = "Sign up at https://app.proxypics.com/auth/signup, then Profile > Integrations > API key."
    can_commission = True

    def _h(self, required=True):
        return {"x-api-key": self.key(required)}

    @staticmethod
    def _body(spec):
        return {k: v for k, v in {"address": spec.get("address"), "latitude": spec.get("lat"), "longitude": spec.get("lon"),
                                  "additional_notes": spec.get("notes"), "template_token": spec.get("template_token"),
                                  "expires_at": spec.get("expires_at"), "external_id": spec.get("external_id")}.items() if v is not None}

    def prepare(self, req, route, operation=None):
        """order: POST /photo-requests (default); read: the account's templates (template_token); status: one request."""
        op = pick_operation(route, operation, "commission", ("read", "order", "status"), "order")
        if not op:
            return None
        h = {"x-api-key": "{" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header x-api-key",
                    get_key_url="https://app.proxypics.com/profile", docs_url="https://docs.proxypics.com")
        if op == "read":
            return prepared_call(self.card_id, "read", "GET", f"{BASE}/photo-request-templates", headers=h, **auth,
                                 notes="Lists your templates (products); pass one's token as template_token in the order.")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/photo-requests/{{PHOTO_REQUEST_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "PHOTO_REQUEST_ID", "what": "the id POST /photo-requests returned", "where": "its response"}],
                                 notes="status: unassigned, assigned, completed (awaiting your approval: PUT .../approve), rejected, "
                                       "expired or fulfilled (download_photos_url, download_report_url).")
        spec = self.spec_from_request(req, None, req.get("deliver_by"))
        body = self._body(spec)
        ph = []
        if not body.get("address"):
            body["address"] = "{STREET_ADDRESS}"
            ph.append({"name": "STREET_ADDRESS", "what": "the US street address to photograph"
                       + (f" ({req['place']} is not a street address)" if req.get("place") else ""), "where": "your user"})
        body["template_token"] = "{TEMPLATE_TOKEN}"
        ph.append({"name": "TEMPLATE_TOKEN", "what": "the product to order (optional; the account default otherwise)",
                   "where": "GET /photo-request-templates (the read call)"})
        return prepared_call(
            self.card_id, "order", "POST", f"{BASE}/photo-requests", headers=dict(h, **{"Content-Type": "application/json"}), body=body, **auth,
            placeholders=ph,
            notes="Dispatches a nearby photographer to the address; the account's card is charged (the response's cost is in "
                  "cents). Needs the person's approval. US addresses only, one address per order. The sandbox host "
                  "https://sandbox.proxypics.com/api/v3 simulates the whole flow.")

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        """The order spec from an ask: the street address in its place (never a city or a landmark), the need as the
        photographer's notes, the deadline as expires_at, the ask's quantity (one address per order)."""
        s = {"address": street_address(req.get("place")), "lat": req.get("lat"), "lon": req.get("lon"), "notes": req.get("need"),
             "expires_at": deliver_by or req.get("deliver_by"), "external_id": ext_id, "quantity": req.get("quantity")}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def missing(spec, body):
        m = []
        if not body.get("address"):
            m.append("address: ProxyPics dispatches to a US street address (give the full address, e.g. as the place)")
        if not (body.get("additional_notes") or "").strip():
            m.append("notes: what the photographer should capture")
        q = spec.get("quantity")
        if isinstance(q, int) and q > 1:
            m.append(f"quantity: ProxyPics takes one address per order; {q} photos means {q} orders, one per address")
        return m

    def commission(self, spec, confirm=False):
        """spec: address (required by ProxyPics), lat, lon, notes (what to photograph), template_token, expires_at, external_id."""
        body = self._body(spec)
        missing = self.missing(spec, body)
        if not confirm:
            out = self._dry("POST", f"{BASE}/photo-requests", body, self._h(required=False))
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise MissingDetails(self.card_id, missing)
        return http.post(f"{BASE}/photo-requests", body=body, headers=self._h())

    def status(self, order):
        return http.get(f"{BASE}/photo-requests/{order['id']}", headers=self._h())

    # GET /photo-requests/{id} status, per the provider's OpenAPI spec: unassigned, assigned, completed, rejected,
    # expired or fulfilled. "completed" means photos are uploaded but await the client's approval (PUT .../approve;
    # 120 hours to reject), so it is not yet a delivery; "rejected" sends the request back for re-capture, so the job
    # is still running. Only "fulfilled" (approved, paid, download_photos_url and download_report_url present) is
    # delivered. "cancelled" appears in the documented webhook events, not the status list; mapped as failed.
    status_map = {"unassigned": "in_flight", "assigned": "in_flight", "completed": "in_flight", "rejected": "in_flight",
                  "fulfilled": "delivered", "expired": "failed", "cancelled": "failed"}

    def deliverables(self, resp):
        """A fulfilled request: the photo ZIP and the PDF report. ProxyPics gives no capture time here, so
        phenomenon_time stays unknown (None) rather than invented."""
        r = resp if isinstance(resp, dict) else {}
        lat, lon = r.get("latitude"), r.get("longitude")
        out = []
        for key, kind, label in (("download_photos_url", "photo", "photos (ZIP)"),
                                 ("download_report_url", "report", "report (PDF)")):
            if isinstance(r.get(key), str) and r[key]:
                out.append(observation(self.card_id, kind, None, lat, lon, label=label, url=r[key]))
        return out
