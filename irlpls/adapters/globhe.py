"""GLOBHE Order API v2: ask a vetted local drone operator to capture an area (150 countries claimed). Key in
GLOBHE_API_KEY (Basic auth: the key is the username, the password is empty). Client access is approved by GLOBHE
support, so a fresh key does nothing until it is activated.

Flow, per the provider's docs:
1. POST /orders creates an *order request*: GLOBHE reviews it and asks operators to bid. Nothing is committed; it can
   be cancelled with DELETE /orders/{id} while it is still `reviewing`. irlpls treats this as the quote step.
2. POST /getorder/{id} shows `status`, `instruction` and, once ready, `quotes[]` (finalPrice, currency, deliveryBy,
   expiryDate).
3. POST /order/{orderId}/quote/{quoteId} {"status": "accept"} accepts a quote. That makes the order active and forms a
   contract (the money step). Only `accept_quote(..., confirm=True)` sends it.
4. When `status` is `completed`, POST /getorder/{id} carries `deliveryFiles` (baseFiles and processedData by map type).

commission(confirm=False) never sends anything, not even the order request: the request is free but it starts a human
review at GLOBHE, so it is sent only with confirm=True.

Sources (read 2026-10-28):
- https://developers.globhe.com/authentication (Basic auth, activation by support)
- https://developers.globhe.com/testing-environment (staging base us-central1-globhe-d3087, separate key)
- https://developers.globhe.com/create-order (POST /orders body, mapTypes, orderName <= 20 chars)
- https://developers.globhe.com/get-single-order (POST /getorder/:id response; flyingDateSet, flyingDateRescheduled)
- https://developers.globhe.com/accept-reject-quotes (quote fields; POST /order/:orderId/quote/:quoteId)
- https://developers.globhe.com/list-order (the status list mapped below)
- https://developers.globhe.com/cancel-order (DELETE /orders/:id while reviewing)
- https://developers.globhe.com/delivery (deliveryFiles.baseFiles / processedData; fileDataType filter)
Fixtures in tests/fixtures/adapters/globhe/ follow these documented examples (fixture: documented-shape).
"""
import base64
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .. import geo, http, ratelimit
from .base import Adapter, MissingCredentials, MissingDetails, observation, prepared_call

PROD = "https://us-central1-globhe-app.cloudfunctions.net/api/v2"
STAGING = "https://us-central1-globhe-d3087.cloudfunctions.net/api/v2"
DOCS = "https://developers.globhe.com/order-api"
GET_KEY = "https://app.globhe.com/crowddroning/client/sign-up/"
AUTH_IN = "header Authorization: Basic base64('{GLOBHE_API_KEY}:'), i.e. the key as username and an empty password (curl -u {GLOBHE_API_KEY}:)"

# Delivery products named on the create-order page, picked from words in the need. originalPhotos = the raw JPGs.
MAP_TYPES = (("thermal", "thermalMap"), ("ndvi", "ndvi"), ("plant health", "ndvi"), ("multispectral", "multispectral"),
             ("lidar", "LiDAR"), ("point cloud", "_3dMap"), ("3d", "_3dMap"), ("terrain", "digitalTerrainModel"),
             ("surface model", "digitalSurfaceModel"), ("contour", "topographicMap"), ("topograph", "topographicMap"),
             ("orthomosaic", "_2dMap"), ("ortho", "_2dMap"), ("2d map", "_2dMap"), ("panorama", "panoramas"),
             ("360", "panoramas"), ("live stream", "streamedData"), ("video", "streamedData"))


def _base():
    return STAGING if os.environ.get("GLOBHE_STAGING") == "1" else PROD


def map_types(need):
    n = (need or "").lower()
    out = []
    for word, t in MAP_TYPES:
        if word in n and t not in out:
            out.append(t)
    return out or ["originalPhotos"]


def _deadline(within_minutes, now=None):
    if not within_minutes:
        return None
    t = (now or datetime.now(timezone.utc)) + timedelta(minutes=int(within_minutes))
    return t.strftime("%Y-%m-%d")


def _square(lat, lon, radius_km):
    w, s, e, n = geo.bbox(lat, lon, radius_km)
    return [{"lat": round(s, 6), "lng": round(w, 6)}, {"lat": round(s, 6), "lng": round(e, 6)},
            {"lat": round(n, 6), "lng": round(e, 6)}, {"lat": round(n, 6), "lng": round(w, 6)}]


def _name(text):
    words = re.sub(r"\s+", " ", (text or "irlpls order")).strip()
    return words[:20].rstrip()


class Globhe(Adapter):
    card_id = "globhe"
    env = "GLOBHE_API_KEY"
    how_to_get = ("Sign up as a client at https://app.globhe.com/crowddroning/client/sign-up/, copy the key from your "
                  "profile, then ask support@globhe.com to activate it (reply in under 24 hours). Set GLOBHE_STAGING=1 "
                  "with a stagingpage.globhe.com key to use the testing environment.")
    can_commission = True
    can_cancel = True

    # ---- calls

    def _h(self, required=True):
        k = self.key(required)
        return {"Authorization": "Basic " + base64.b64encode(f"{k}:".encode()).decode()}

    def _send(self, method, path, body=None):
        url = _base() + path
        try:
            return http.request(method, url, body=body, headers=self._h())
        except http.HTTPError as e:
            raise map_error(self.card_id, e) from None

    # ---- the order request body

    def body(self, spec):
        """POST /orders body from a spec: lat, lon (or area: [{lat, lng}, ...]), radius_km, need/description, map_types,
        deliver_by (YYYY-MM-DD), name, webhook, poc_name, poc_phone."""
        area = spec.get("area")
        if not area and spec.get("lat") is not None and spec.get("lon") is not None:
            area = _square(float(spec["lat"]), float(spec["lon"]), float(spec.get("radius_km") or 0.15))
        b = {"deliveryDeadline": spec.get("deliver_by"), "locationMap": area,
             "mapTypes": spec.get("map_types") or map_types(spec.get("need") or spec.get("description")),
             "description": spec.get("description") or spec.get("need"),
             "orderName": _name(spec.get("name") or spec.get("need"))}
        if spec.get("webhook"):
            b["notifications"] = {"webhook": spec["webhook"], "email": True}
        if spec.get("poc_name") or spec.get("poc_phone"):
            b["pointOfContact"] = {"pocName": spec.get("poc_name"), "pocPhone": spec.get("poc_phone")}
        return {k: v for k, v in b.items() if v is not None}

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "need": req.get("need"),
             "deliver_by": (deliver_by or "")[:10] or _deadline(req.get("within_minutes"))}
        return {k: v for k, v in s.items() if v}

    @staticmethod
    def _missing(body):
        m = []
        if not body.get("locationMap"):
            m.append("lat: GLOBHE needs the area of interest (a point is widened to a small square)")
        if not body.get("deliveryDeadline"):
            m.append("within_minutes: GLOBHE needs a delivery deadline (deliveryDeadline, YYYY-MM-DD)")
        if not body.get("description"):
            m.append("need: describe what to capture")
        return m

    # ---- operations

    def commission(self, spec, confirm=False):
        """confirm=False: nothing is sent. confirm=True: POST /orders, an order request for review and bidding. It commits
        no money; accept a quote with accept_quote() to place the actual order."""
        body = self.body(spec)
        if not confirm:
            out = self._dry("POST", f"{_base()}/orders", body, self._h(required=False))
            out["note"] = ("Nothing was sent. confirm=True sends this order request; GLOBHE then reviews it and returns "
                           "quotes. No money is committed until a quote is accepted (accept_quote).")
            missing = self._missing(body)
            if missing:
                out["missing"] = missing
            return out
        missing = self._missing(body)
        if missing:
            raise MissingDetails(self.card_id, missing)
        return self._send("POST", "/orders", body)

    def accept_quote(self, order, quote_id, confirm=False, reject=False):
        """Accept (or reject) a quote from status(). Accepting forms a contract and may cost money: dry unless confirm."""
        oid = _order_id(order)
        body = {"status": "reject" if reject else "accept"}
        path = f"/order/{oid}/quote/{quote_id}"
        if not confirm:
            return self._dry("POST", _base() + path, body, self._h(required=False))
        return self._send("POST", path, body)

    def pending_approvals(self, resp):
        """Open quotes waiting for the client (status() quotes[]), one option each, in the quote's own currency."""
        return [{"id": q["quote_id"], "amount": q["price"] if isinstance(q.get("price"), (int, float)) and not isinstance(q.get("price"), bool) else None,
                 "currency": str(q.get("currency") or "").upper() or None, "label": q.get("description") or "GLOBHE quote",
                 "expires_at": q.get("expires_at"), "deliver_by": q.get("delivery_by")}
                for q in self.quotes(resp) if q.get("quote_id")]

    def approve(self, order, approval_id=None, confirm=False):
        """Accept one quote (accept_quote): forms a contract and may cost money; dry run unless confirm=True."""
        if not approval_id:
            raise ValueError("globhe: approval_id is the quoteId to accept")
        return self.accept_quote(order, approval_id, confirm=confirm)

    def status(self, order, file_types=None):
        body = {"fileDataType": list(file_types)} if file_types else {}
        return self._send("POST", f"/getorder/{_order_id(order)}", body)

    def cancel(self, order):
        """DELETE /orders/{id}. GLOBHE accepts it only while the order is still `reviewing`."""
        return self._send("DELETE", f"/orders/{_order_id(order)}")

    # ---- ready calls (offline)

    def prepare(self, req, route, operation="quote", order_id=None, quote_id=None):
        if route != "commission":
            return None
        auth = dict(auth_kind="basic", key_name=self.env, key_in=AUTH_IN, get_key_url=GET_KEY, docs_url=DOCS)
        hdr = {"Authorization": "Basic base64({GLOBHE_API_KEY}:)"}
        if operation == "status":
            return prepared_call(self.card_id, "status", "POST", f"{PROD}/getorder/{order_id or '{GLOBHE_ORDER_ID}'}",
                                 headers=hdr, body={}, placeholders=None if order_id else [_PH_ORDER], **auth,
                                 notes="Read-only despite POST. `status` is the order state; `quotes[]` lists open quotes "
                                       "(finalPrice, currency, deliveryBy, expiryDate); `deliveryFiles` appears once completed.")
        if operation == "order":
            ph = ([] if order_id else [_PH_ORDER]) + ([] if quote_id else [_PH_QUOTE])
            return prepared_call(self.card_id, "order", "POST",
                                 f"{PROD}/order/{order_id or '{GLOBHE_ORDER_ID}'}/quote/{quote_id or '{GLOBHE_QUOTE_ID}'}",
                                 headers=hdr, body={"status": "accept"}, placeholders=ph or None, **auth,
                                 notes="Accepting a quote makes the order active and forms a contract with GLOBHE. "
                                       "Quotes expire at expiryDate. Send {\"status\": \"reject\"} to decline instead.")
        if operation != "quote":
            return None
        spec = self.spec_from_request(req, None, req.get("deliver_by"))
        body = self.body(spec)
        ph = []
        if not body.get("locationMap"):
            body["locationMap"] = "{GLOBHE_AREA}"
            ph.append({"name": "GLOBHE_AREA", "what": "the area of interest as [{lat, lng}, ...] polygon corners",
                       "where": "from the place in the ask (the need gave no coordinates)"})
        if not body.get("deliveryDeadline"):
            body["deliveryDeadline"] = "{DELIVERY_DEADLINE}"
            ph.append({"name": "DELIVERY_DEADLINE", "what": "the date the data is needed by, YYYY-MM-DD",
                       "where": "ask your person (the need gave no deadline)"})
        return prepared_call(self.card_id, "quote", "POST", f"{PROD}/orders", headers=hdr, body=body,
                             placeholders=ph or None, **auth,
                             notes="An order request, not an order: GLOBHE reviews it and returns quotes in "
                                   "POST /getorder/{id}; nothing is committed until a quote is accepted "
                                   "(POST /order/{orderId}/quote/{quoteId}). Single-location orders only. It still "
                                   "starts a review at GLOBHE, so send it only when your person wants a quote. Staging: "
                                   + STAGING + ".")

    # ---- results

    # Statuses from the list-order page, plus flyingDateRescheduled from the get-order page. Quote received is still
    # in flight: the order waits for the client to accept a quote.
    status_map = {"reviewing": "in_flight", "quotereceived": "in_flight", "pendingoperator": "in_flight",
                  "operatorassigned": "in_flight", "flyingdateset": "in_flight", "flyingdaterescheduled": "in_flight",
                  "completed": "delivered", "ordernotfeasible": "failed", "rejected": "failed", "cancelled": "failed",
                  "deleted": "failed"}

    def provider_status(self, resp):
        if not isinstance(resp, dict):
            return None
        v = resp.get("status") if isinstance(resp.get("status"), str) else resp.get("orderStatus")
        return v if isinstance(v, str) else None

    @staticmethod
    def quotes(resp):
        """Open quotes in a status() response: [{quote_id, price, currency, delivery_by, expires_at, description}]."""
        out = []
        for q in (resp or {}).get("quotes") or []:
            if isinstance(q, dict):
                out.append({"quote_id": q.get("quoteId"), "price": q.get("finalPrice"), "currency": q.get("currency"),
                            "delivery_by": q.get("deliveryBy"), "expires_at": q.get("expiryDate"),
                            "description": q.get("description")})
        return out

    def order_total(self, resp):
        """USD the client agreed to pay: the accepted quote's finalPrice, only when its currency is USD."""
        q = (resp or {}).get("acceptedQuote") if isinstance(resp, dict) else None
        if isinstance(q, dict) and str(q.get("currency") or "").upper() == "USD":
            v = q.get("finalPrice")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                return Decimal(str(v))
        return None

    def deliverables(self, resp):
        """Files of a completed order. GLOBHE gives no capture time here, so phenomenon_time stays None; the point is the
        first corner of the ordered area, not a measured position."""
        r = resp if isinstance(resp, dict) else {}
        loc = (r.get("locationMap") or [{}])[0] if isinstance(r.get("locationMap"), list) else {}
        lat, lon = (loc or {}).get("lat"), (loc or {}).get("lng")
        files = r.get("deliveryFiles") or (r.get("collectedData") if isinstance(r.get("collectedData"), dict) else None)
        out = []
        if isinstance(files, list):   # the create/list examples show a flat list of URLs
            for u in files:
                if isinstance(u, str) and u:
                    out.append(observation(self.card_id, "photo", None, lat, lon, label="delivery file", url=u))
            return out
        if not isinstance(files, dict):
            return out
        for u in files.get("baseFiles") or []:
            if isinstance(u, str) and u:
                out.append(observation(self.card_id, "photo", None, lat, lon, label="base file (drone photo)", url=u))
        for t, urls in (files.get("processedData") or {}).items():
            for u in urls if isinstance(urls, list) else []:
                if isinstance(u, str) and u:
                    out.append(observation(self.card_id, "image", None, lat, lon, label=f"processed: {t}", url=u, map_type=t))
        return out


_PH_ORDER = {"name": "GLOBHE_ORDER_ID", "what": "the order id", "where": "the `id` returned by POST /orders"}
_PH_QUOTE = {"name": "GLOBHE_QUOTE_ID", "what": "the quote to accept", "where": "quotes[].quoteId in POST /getorder/{id}"}


def _order_id(order):
    if isinstance(order, dict):
        v = order.get("id") or order.get("orderId") or order.get("globhe_order_id")
    else:
        v = order
    if not v:
        raise ValueError("globhe: the order has no id")
    return str(v)


def map_error(card_id, e):
    """401/403 → MissingCredentials (the key is missing, wrong or not yet activated); other statuses stay HTTPError,
    with `retry_after` (seconds) set from Retry-After on 429/503."""
    if e.status in (401, 403):
        return MissingCredentials(f"{card_id}: the provider refused the credentials (HTTP {e.status}). A GLOBHE key "
                                  "works only after support activates it.")
    err = http.HTTPError(e.status, e.url, e.body, e.headers)
    err.retry_after = ratelimit.retry_after(e.headers) if e.status in (429, 503) else None
    return err
