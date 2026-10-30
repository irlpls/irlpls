"""Snappr for Enterprise: book a vetted photographer for a shoot at a place (photos, video). Key in SNAPPR_API_KEY
(Authorization: Bearer). The API is for Enterprise customers only; the sandbox (SNAPPR_SANDBOX=1, host
sandbox.snappr.com) "returns dummy data; write actions are not persisted" and takes a separate sandbox key.

- Read (safe): GET /coverage (is there a photographer for this place and shoot type), GET /availability (start times
  in UTC for a local date), GET /shoottypes (names for `shoottype`).
- Order: POST /bookings. It charges credits immediately (402 InsufficientCredits when short); 400 NoCoverage or
  NotAvailable when no photographer can take it. Credits are not dollars, so no USD total is reported.
- Status: GET /bookings/{uid}; images and videos at /bookings/{uid}/images and /videos (signed URLs) once `completed`.
No price or quote endpoint exists, so offers are never firm. No idempotency key is documented; internal_id tags a
booking with your own id.

Sources (read 2026-10-28): https://docs.snappr.com/ and its OpenAPI 3.1 file https://docs.snappr.com/openapi.json
(servers, bearer scheme, accept-version 1.0.0, parameters, the booking status examples paid, paid_pending_schedule,
completed, cancelled, error codes and the 1,000 requests per minute limit).
Fixtures in tests/fixtures/adapters/snappr/ follow the OpenAPI examples (fixture: documented-shape).
"""
import os
import urllib.parse

from .. import http, ratelimit
from .base import Adapter, MissingCredentials, observation, prepared_call

PROD = "https://api.snappr.com"
SANDBOX = "https://sandbox.snappr.com"
DOCS = "https://docs.snappr.com/"
GET_KEY = DOCS   # the key is issued in the Snappr portal (Enterprise accounts)
VERSION = {"accept-version": "1.0.0"}
BOOKING_REQUIRED = ("shoottype", "duration", "customer_firstname", "customer_email", "customer_mobilephone")


def _base():
    return SANDBOX if os.environ.get("SNAPPR_SANDBOX") == "1" else PROD


def _where(spec):
    if spec.get("lat") is not None and spec.get("lon") is not None:
        return {"latitude": float(spec["lat"]), "longitude": float(spec["lon"])}
    return {"address": spec["address"]} if spec.get("address") else {}


class Snappr(Adapter):
    card_id = "snappr"
    env = "SNAPPR_API_KEY"
    how_to_get = ("Snappr for Enterprise customers only: get the key in the Snappr portal (one active key per user). "
                  "For the sandbox, use the separate sandbox key and set SNAPPR_SANDBOX=1.")
    can_commission = True

    def _h(self):
        return {"Authorization": "Bearer " + self.key(), **VERSION}

    def _send(self, method, path, params=None, body=None):
        try:
            return http.request(method, _base() + path, params=params, body=body, headers=self._h())
        except http.HTTPError as e:
            raise map_error(self.card_id, e) from None

    # ---- reads

    def coverage(self, lat=None, lon=None, shoottype=None, address=None):
        return self._send("GET", "/coverage", params={**_where({"lat": lat, "lon": lon, "address": address}),
                                                      "shoottype": shoottype})

    def availability(self, date, shoottype, duration, lat=None, lon=None, address=None, days_span=None):
        return self._send("GET", "/availability", params={**_where({"lat": lat, "lon": lon, "address": address}),
                                                          "shoottype": shoottype, "duration": int(duration), "date": date,
                                                          "days_span": days_span})

    def shoottypes(self, limit=100):
        return self._send("GET", "/shoottypes", params={"limit": limit})

    # ---- booking

    def body(self, spec):
        """POST /bookings body. spec: lat, lon or address, shoottype, start_at (UTC ISO, or None to let the end-customer
        pick through scheduling_url), duration (minutes), customer_firstname, customer_surname, customer_email,
        customer_mobilephone, title, need/style_notes, location_notes, internal_id, tags."""
        b = {**_where(spec), "shoottype": spec.get("shoottype"), "start_at": spec.get("start_at"),
             "duration": spec.get("duration"), "title": spec.get("title"),
             "style_notes": spec.get("style_notes") or spec.get("need"), "location_notes": spec.get("location_notes"),
             "customer_firstname": spec.get("customer_firstname"), "customer_surname": spec.get("customer_surname"),
             "customer_email": spec.get("customer_email"), "customer_mobilephone": spec.get("customer_mobilephone"),
             "customer_company": spec.get("customer_company"), "internal_id": spec.get("internal_id"), "tags": spec.get("tags")}
        out = {k: v for k, v in b.items() if v is not None}
        out["start_at"] = spec.get("start_at")   # required key; null means the end-customer picks the time
        return out

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "address": req.get("place"), "need": req.get("need"),
             "internal_id": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _missing(body):
        m = [f"{k}: Snappr requires {k} to book" for k in BOOKING_REQUIRED if body.get(k) in (None, "")]
        if not body.get("address") and body.get("latitude") is None:
            m.insert(0, "address: Snappr books a shoot at an address or coordinates")
        return m

    def commission(self, spec, confirm=False):
        """confirm=False sends no booking. With a key set it runs the free coverage read and reports it as the quote
        ({"coverage", "available_providers"}, no price). confirm=True books, and Snappr charges credits at once."""
        body = self.body(spec)
        missing = self._missing(body)
        if not confirm:
            out = self._dry("POST", f"{_base()}/bookings", body, {"Authorization": "***", **VERSION})
            out["note"] = "Nothing was booked. confirm=True books the shoot and Snappr charges credits immediately."
            if missing:
                out["missing"] = missing
            if self.configured() and _where(spec):
                c = self.coverage(spec.get("lat"), spec.get("lon"), spec.get("shoottype"), spec.get("address"))
                out["quote"] = {"coverage": (c or {}).get("coverage"), "available_providers": (c or {}).get("available_providers"),
                                "shoottype": (c or {}).get("shoottype")}
            return out
        if missing:
            raise ValueError("snappr: " + "; ".join(missing))
        return self._send("POST", "/bookings", body=body)

    def status(self, order):
        return self._send("GET", f"/bookings/{_uid(order)}")

    def images(self, order, limit=1000, offset=0):
        """Delivered images; Snappr answers 400 BadRequest until the booking is `completed`."""
        return self._send("GET", f"/bookings/{_uid(order)}/images", params={"limit": limit, "offset": offset})

    def videos(self, order, limit=1000, offset=0):
        r = self._send("GET", f"/bookings/{_uid(order)}/videos", params={"limit": limit, "offset": offset})
        if isinstance(r, dict):
            r["_kind"] = "video"   # so deliverables() labels these results as video
        return r

    # ---- ready calls (offline)

    def prepare(self, req, route, operation="read", order_id=None, date=None, shoottype=None, duration=None):
        if route != "commission":
            return None
        hdr = {"Authorization": "Bearer {SNAPPR_API_KEY}", **VERSION}
        auth = dict(auth_kind="bearer", key_name=self.env, key_in="header Authorization: Bearer", get_key_url=GET_KEY,
                    docs_url=DOCS)
        where = _where(self.spec_from_request(req))
        ph = []
        if not where:
            where = {"address": "{ADDRESS}"}
            ph.append({"name": "ADDRESS", "what": "the shoot location as a Google Maps-valid address", "where": "ask your person"})
        if operation == "status":
            if not order_id:
                ph.append({"name": "SNAPPR_BOOKING_UID", "what": "the booking uid", "where": "`uid` in the POST /bookings response"})
            return prepared_call(self.card_id, "status", "GET", f"{PROD}/bookings/{order_id or '{SNAPPR_BOOKING_UID}'}",
                                 headers=hdr, placeholders=ph or None, **auth,
                                 notes="status: paid, paid_pending_schedule, completed or cancelled (documented examples). "
                                       "When completed, GET /bookings/{uid}/images and /videos for signed URLs.")
        if operation == "read":
            q = urllib.parse.urlencode({**where, **({"shoottype": shoottype} if shoottype else {})}, safe="{}")
            return prepared_call(self.card_id, "read", "GET", f"{PROD}/coverage?{q}", headers=hdr,
                                 placeholders=ph or None, **auth,
                                 notes="Step 1: is there a photographer here (all shoot types unless shoottype is given; "
                                       "names from GET /shoottypes). Step 2: GET /availability?shoottype&duration&date "
                                       "for start times in UTC. Step 3 (order, charges credits): POST /bookings. "
                                       "Sandbox: " + SANDBOX + " with the sandbox key.")
        if operation == "availability":
            for name, val, what in (("SNAPPR_SHOOTTYPE", shoottype, "the shoot type name from GET /shoottypes"),
                                    ("DATE", date, "the local date to check, YYYY-MM-DD"),
                                    ("DURATION_MINUTES", duration, "the shoot length in minutes")):
                if not val:
                    ph.append({"name": name, "what": what, "where": "ask your person" if name != "SNAPPR_SHOOTTYPE" else "GET /shoottypes"})
            q = urllib.parse.urlencode({**where, "shoottype": shoottype or "{SNAPPR_SHOOTTYPE}", "duration": duration or "{DURATION_MINUTES}",
                                        "date": date or "{DATE}"}, safe="{}")
            return prepared_call(self.card_id, "read", "GET", f"{PROD}/availability?{q}", headers=hdr,
                                 placeholders=ph or None, **auth, notes="Available start times in UTC for that local date.")
        if operation != "order":
            return None
        body = {**where, "shoottype": shoottype or "{SNAPPR_SHOOTTYPE}", "start_at": None,
                "duration": duration or "{DURATION_MINUTES}", "style_notes": req.get("need"),
                "customer_firstname": "{CUSTOMER_FIRSTNAME}", "customer_email": "{CUSTOMER_EMAIL}",
                "customer_mobilephone": "{CUSTOMER_MOBILEPHONE}"}
        ph += [{"name": "SNAPPR_SHOOTTYPE", "what": "the shoot type name", "where": "GET /shoottypes"}] if not shoottype else []
        ph += [{"name": "DURATION_MINUTES", "what": "the shoot length in minutes", "where": "ask your person"}] if not duration else []
        ph += [{"name": n, "what": w, "where": "the on-site contact Snappr will message; ask your person"} for n, w in (
            ("CUSTOMER_FIRSTNAME", "the end-customer's first name"), ("CUSTOMER_EMAIL", "the end-customer's email"),
            ("CUSTOMER_MOBILEPHONE", "the end-customer's mobile, e.g. +14153339966"))]
        return prepared_call(self.card_id, "order", "POST", f"{PROD}/bookings", headers=hdr, body=body, placeholders=ph,
                             **auth, notes="Charges credits immediately. start_at null lets the end-customer pick a time "
                                           "via scheduling_url; set start_at (UTC) from GET /availability to fix it. "
                                           "400 NoCoverage/NotAvailable means no photographer; 402 InsufficientCredits.")

    # ---- results

    # Documented booking status examples ("e.g. paid, paid_pending_schedule, completed, cancelled"); anything else stays
    # in flight as Snappr sent it.
    status_map = {"paid": "in_flight", "paid_pending_schedule": "in_flight", "completed": "delivered", "cancelled": "failed"}

    def deliverables(self, resp):
        """A booking's gallery ZIP (when the account has ZIP downloads) or a page of /images or /videos results.
        Snappr gives no capture time per image; phenomenon_time is the booking's start_at (when the shoot was booked
        to start), else None."""
        r = resp if isinstance(resp, dict) else {}
        lat, lon, t = r.get("latitude"), r.get("longitude"), r.get("start_at")
        out = []
        if isinstance(r.get("gallery_zip_url"), str):
            out.append(observation(self.card_id, "photo", t, lat, lon, label="gallery (ZIP)", url=r["gallery_zip_url"]))
        for it in r.get("results") or []:
            if isinstance(it, dict) and isinstance(it.get("url_original"), str):
                kind = "video" if r.get("_kind") == "video" else "photo"
                out.append(observation(self.card_id, kind, None, lat, lon, label=it.get("file_name") or "image",
                                       url=it["url_original"], thumb=it.get("url_thumb")))
        return out


def _uid(order):
    v = (order.get("uid") or order.get("booking_uid") or order.get("id")) if isinstance(order, dict) else order
    if not v:
        raise ValueError("snappr: the order has no booking uid")
    return str(v)


def map_error(card_id, e):
    """401 → MissingCredentials (key missing, invalid, or its user not an account admin). Others stay HTTPError with
    `provider_code` (Snappr's `code`: NoCoverage, NotAvailable, InsufficientCredits, ...) and `retry_after` on 429."""
    import json
    if e.status in (401, 403):
        return MissingCredentials(f"{card_id}: the provider refused the key (HTTP {e.status}); Snappr keys belong to an "
                                  "Enterprise account admin, and the sandbox needs its own key.")
    err = http.HTTPError(e.status, e.url, e.body, e.headers)
    try:
        err.provider_code = (json.loads(e.body) or {}).get("code")
    except (TypeError, ValueError, AttributeError):
        err.provider_code = None
    err.retry_after = ratelimit.retry_after(e.headers) if e.status in (429, 503) else None
    return err
