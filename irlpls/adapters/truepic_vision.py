"""Truepic Vision: send a person (your customer, contractor or policyholder) a link by text or email; they capture
photos and video in Truepic's app, each tested for time, place, device integrity and capture integrity, and the
inspection comes back for review. Contract only (organisation set up after a demo, no sandbox); OAuth 2.0 client
credentials from Dashboard > Settings > API Integration; tokens last 72 hours. Rate limit 20 requests / 10 s.

Sources (read 2026-10-28):
- Get API access (token endpoint, JSON body, audience, 72 h): https://vision.truepic.dev/docs/get-api-access.md
- API overview (no sandbox; texts and emails reach real recipients in testing): https://vision.truepic.dev/docs/api-overview.md
- Statuses (ids, transitions, which can be set by PUT): https://vision.truepic.dev/docs/inspection-statuses.md
- Inspection details (status.id_internal, result.photos fields, signed media URLs): https://vision.truepic.dev/docs/view-inspections.md
- Regions (vision-api.truepic.uk, vision-auth.truepic.uk): https://vision.truepic.dev/docs/regions.md
- OpenAPI 3.0 (v3.92.0): https://vision-api.truepic.com/v3/open-api (CreateInspectionBody, InspectionSerialized)

An order texts or emails a real person: commission() is a dry run until confirm=True, and a live test must target the owner.
"""
import os

from .. import http
from . import _a7
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, observation, prepared_call

DOCS = "https://vision.truepic.dev/docs/"
SECRET_ENV = "TRUEPIC_CLIENT_SECRET"
REGION_ENV = "TRUEPIC_REGION"   # "us" (default) or "uk"
STATUS_NAMES = {7: "STATUS_INSPECTION_SCHEDULED", 1: "STATUS_WAITING_ON_PHOTOS", 3: "STATUS_INSPECTION_STARTED",
                4: "STATUS_INSPECTION_IN_PROGRESS", 8: "STATUS_PROCESSING", 2: "STATUS_READY_FOR_REVIEW",
                5: "STATUS_INSPECTION_CLOSED", 6: "STATUS_CANCELED"}
# Spec fields copied into CreateInspectionBody as given (the rest of the body is built below).
PASS = ("customer_first_name", "customer_last_name", "customer_email_address", "customer_phone_number",
        "customer_phone_country", "customer_address", "message_supplement", "custom_field_values", "scheduled_for",
        "scheduled_tz", "inspection_ready_criteria_text")


def hosts(region=None):
    tld = "uk" if str(region or os.environ.get(REGION_ENV) or "").lower() in ("uk", "eu", "gb") else "com"
    return f"https://vision-api.truepic.{tld}/v3", f"https://vision-auth.truepic.{tld}/oauth/token", f"https://vision-api.truepic.{tld}"


class TruepicVision(Adapter):
    card_id = "truepic-vision"
    env = "TRUEPIC_CLIENT_ID"
    how_to_get = (f"Contract with Truepic (demo, no self-serve); an admin generates the client id ({env}) and secret "
                  f"({SECRET_ENV}) under Dashboard > Settings > API Integration. UK organisations set {REGION_ENV}=uk.")
    can_commission = True
    can_cancel = True

    status_path = ("result", "status", "id_internal")
    status_map = {"status_inspection_scheduled": "in_flight", "status_waiting_on_photos": "in_flight",
                  "status_inspection_started": "in_flight", "status_inspection_in_progress": "in_flight",
                  "status_processing": "in_flight", "status_ready_for_review": "delivered",
                  "status_inspection_closed": "delivered", "status_canceled": "failed"}

    _tokens = TokenCache(json=True)   # Truepic's token endpoint takes a JSON body (get-api-access.md)

    def configured(self):
        return bool(os.environ.get(self.env) and os.environ.get(SECRET_ENV))

    def _h(self):
        cid, sec = os.environ.get(self.env), os.environ.get(SECRET_ENV)
        if not cid or not sec:
            raise MissingCredentials(f"{self.card_id}: set {self.env} and {SECRET_ENV}. {self.how_to_get}")
        _, token_url, audience = hosts()
        return {"Authorization": "Bearer " + self._tokens.client_credentials(token_url, cid, sec, audience=audience)}

    def _call(self, what, fn):
        return _a7.call(self.card_id, what, fn, hint=self.how_to_get)

    def provider_status(self, resp):
        """status.id_internal, or the name for a status id when only the id is present."""
        s = ((resp or {}).get("result") or {}).get("status") if isinstance(resp, dict) else None
        if isinstance(s, dict):
            return s.get("id_internal") or STATUS_NAMES.get(s.get("id"))
        return None

    # ------------------------------------------------------------------------------------------------ the request
    def _body(self, spec):
        team = spec.get("team_id") or os.environ.get("TRUEPIC_TEAM_ID")
        itype = spec.get("inspection_type_id") or os.environ.get("TRUEPIC_INSPECTION_TYPE_ID")
        body = {k: spec[k] for k in PASS if spec.get(k) not in (None, "")}
        body["team_id"] = int(team) if str(team or "").isdigit() else team
        body["inspection_type_id"] = int(itype) if str(itype or "").isdigit() else itype
        if spec.get("lat") is not None and spec.get("lon") is not None:
            body.update(customer_address_lat=f"{float(spec['lat']):.6f}", customer_address_lng=f"{float(spec['lon']):.6f}")
        if not body.get("customer_address") and (spec.get("address") or spec.get("place")):
            body["customer_address"] = spec.get("address") or spec.get("place")
        if not body.get("message_supplement") and (spec.get("notes") or spec.get("need")):
            body["message_supplement"] = str(spec.get("notes") or spec.get("need"))[:500]
        body["send_methods"] = {"text": bool(body.get("customer_phone_number")), "email": bool(body.get("customer_email_address"))}
        missing = []
        if not body["team_id"]:
            missing.append("team_id (GET /teams; or TRUEPIC_TEAM_ID)")
        if not body["inspection_type_id"]:
            missing.append("inspection_type_id (GET /inspection-types; or TRUEPIC_INSPECTION_TYPE_ID)")
        if not (body.get("customer_first_name") or body.get("customer_last_name")):
            missing.append("customer_first_name / customer_last_name (the person who will capture)")
        if not any(body["send_methods"].values()):
            missing.append("customer_phone_number or customer_email_address (where the link is sent)")
        return {k: v for k, v in body.items() if v not in (None, "")}, missing

    def spec_from_request(self, req, ext_id, deliver_by):
        spec = {"notes": req.get("need"), "place": req.get("place"), "lat": req.get("lat"), "lon": req.get("lon")}
        return {k: v for k, v in spec.items() if v is not None}

    # ---------------------------------------------------------------------------------------------- ready calls
    def _auth(self, **kw):
        _, token_url, audience = hosts()
        notes = (f"OAuth 2.0 client credentials: POST {token_url} with a JSON body {{grant_type: client_credentials, "
                 f"client_id, client_secret, audience: {audience}}}; reuse the token for its 72 hours. "
                 "Rate limit 20 requests / 10 s. " + kw.pop("notes", ""))
        return dict(headers={"Authorization": "Bearer {TRUEPIC_ACCESS_TOKEN}", **kw.pop("headers", {})},
                    auth_kind="oauth2", key_name="TRUEPIC_ACCESS_TOKEN", key_in="header Authorization: Bearer",
                    get_key_url="https://vision.truepic.dev/docs/get-api-access.md", notes=notes.strip(), **kw)

    def prepare(self, req, route):
        if route != "commission":
            return None
        api, _, _ = hosts()
        body, missing = self._body(req)
        ph = []
        for name, field, what, where in (("TRUEPIC_TEAM_ID", "team_id", "your team id", "GET /teams"),
                                         ("TRUEPIC_INSPECTION_TYPE_ID", "inspection_type_id", "the inspection type (the "
                                          "photo list)", "GET /inspection-types")):
            if not body.get(field):
                body[field] = "{" + name + "}"
                ph.append({"name": name, "what": what, "where": where})
        for name, field, what in (("CUSTOMER_FIRST_NAME", "customer_first_name", "first name of the person who captures"),
                                  ("CUSTOMER_PHONE_NUMBER", "customer_phone_number", "their mobile number (SMS in US, "
                                   "GB, IT, DE, NL, FR) or use customer_email_address with send_methods.email")):
            if not body.get(field) and not (field == "customer_phone_number" and body.get("customer_email_address")):
                body[field] = "{" + name + "}"
                ph.append({"name": name, "what": what, "where": "from your person"})
        if body["send_methods"] == {"text": False, "email": False}:
            body["send_methods"] = {"text": True, "email": False}
        return prepared_call(self.card_id, "order", "POST", f"{api}/inspections", body=body, **self._auth(
            headers={"Content-Type": "application/json"}, docs_url="https://vision.truepic.dev/docs/inspections.md",
            notes="Sends the inspection link to a real person by text or email at once (even in testing). The response's "
                  "result.id is the inspection; poll GET /inspections/{id}. Price per your plan (from $5 per inspection).",
            placeholders=ph))

    def prepared_calls(self, req, inspection_id=None):
        """read (your inspections), order (send one), status."""
        api, _, _ = hosts()
        read = prepared_call(self.card_id, "read", "GET", f"{api}/inspections", **self._auth(docs_url=DOCS))
        status = prepared_call(self.card_id, "status", "GET", f"{api}/inspections/{inspection_id or '{INSPECTION_ID}'}",
                               **self._auth(docs_url="https://vision.truepic.dev/docs/view-inspections.md",
                                            notes="result.status.id_internal; result.photos carry per-photo test "
                                                  "results and signed, time-limited media URLs."))
        return {"read": read, "order": self.prepare(req, "commission"), "status": status}

    # -------------------------------------------------------------------------------------------------- reads
    def inspections(self):
        """GET /inspections: your organisation's inspections, as Truepic returns them."""
        api, _, _ = hosts()
        return self._call("inspections", lambda: http.get(f"{api}/inspections", headers=self._h()))

    # ------------------------------------------------------------------------------------------------- orders
    def commission(self, spec, confirm=False):
        api, _, _ = hosts()
        body, missing = self._body(spec)
        dry = self._dry("POST", f"{api}/inspections", body, {"Authorization": "Bearer <token>"})
        dry["note"] = ("Nothing was sent. Placing this texts or emails the inspection link to a real person at once. "
                       "Billed per inspection under your plan; no per-order price from the API.")
        if missing:
            dry["missing"] = missing
        if not confirm:
            return dry
        if missing:
            raise ValueError(f"{self.card_id}: missing {', '.join(missing)}")
        r = self._call("create inspection", lambda: http.post(f"{api}/inspections", body=body, headers=self._h()))
        r = dict(r) if isinstance(r, dict) else {}
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        r.setdefault("id", res.get("id"))
        return r

    def _id(self, order):
        o = order or {}
        iid = o.get("id") or (o.get("result") or {}).get("id") if isinstance(o, dict) else None
        if not str(iid or "").isdigit():
            raise ValueError(f"{self.card_id}: the order needs its inspection id (a number)")
        return int(iid)

    def status(self, order):
        api, _, _ = hosts()
        iid = self._id(order)
        return self._call("status", lambda: http.get(f"{api}/inspections/{iid}", headers=self._h()))

    def cancel(self, order):
        """PUT /inspections/{id} with inspection_status_id 6: allowed only while Scheduled, Created or Started."""
        api, _, _ = hosts()
        iid = self._id(order)
        return self._call("cancel", lambda: http.request("PUT", f"{api}/inspections/{iid}", body={"inspection_status_id": 6},
                                                         headers=self._h()))

    def deliverables(self, resp):
        """One result per captured photo or video (not deleted): phenomenon_time is the device's capture time, lat/lon
        the photo's GPS, url the full-resolution signed link (time-limited), plus Truepic's per-photo verdict."""
        res = (resp or {}).get("result") if isinstance(resp, dict) else None
        out = []
        for p in (res or {}).get("photos") or []:
            if not isinstance(p, dict) or p.get("is_deleted"):
                continue
            lat, lon = _num(p.get("lat")), _num(p.get("lng"))
            out.append(observation(self.card_id, "video" if p.get("type") == "video" else "photo", p.get("device_time"),
                                   lat, lon, label=p.get("formatted_address") or p.get("verification_code"),
                                   url=p.get("original_image") or p.get("image") or p.get("web_image"),
                                   verification=p.get("status"), is_existing_image=p.get("is_existing_image"),
                                   gps_accuracy_m=_num(p.get("gps_accuracy")),
                                   distance_from_address_m=_num(p.get("distance_from_customer_address")),
                                   verification_code=p.get("verification_code"), photo_id=p.get("id")))
        return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None
