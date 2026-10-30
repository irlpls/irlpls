"""Field Nation REST API v2: post a work order that sends an independent technician to a US or Canadian site (photos,
check-in, sign-off). Buyers only, after an executed Integration Contract. Credentials: FIELDNATION_CLIENT_ID,
FIELDNATION_CLIENT_SECRET, FIELDNATION_USERNAME, FIELDNATION_PASSWORD (the token request on the authentication page
is a password grant); FIELDNATION_COMPANY_ID goes in the x-fn-company-id header. FIELDNATION_SANDBOX=1 uses the
sandbox (separate credentials; types-of-work and template ids differ from production).

- Token: POST https://api.fieldnation.com/authentication/api/oauth/token with a JSON body (grant_type=password,
  client_id, client_secret, username, password); about 1 hour. The token is sent as the `access_token` QUERY
  parameter, so this module strips it from every error it raises.
- Order: POST /workorders creates a Draft ("invisible to anyone but you"); POST /workorders/{id}/publish makes it
  visible in the marketplace. commission(confirm=True) does both (publish=False keeps the draft).
- Status: GET /workorders/{id}; `status` is an object {id, name}: Draft 1, Published 2, Routed 9, Assigned 3 (with
  sub-statuses), Work Done 4, Approved 5, Paid 6, Cancelled 7, Deleted 10. The response includes attachments
  (folders such as "Completion Photos", each file with `link` and `created.utc`).
- Cancel: DELETE /workorders/{id}?cancel_reason=... (a fee may apply after dispatch).
There is no quote endpoint: the buyer sets the pay. The pay offered is not the total charged (fees follow the signed
Sales Order), so no USD total is reported. No idempotency key is documented.

Sources (read 2026-10-28):
- https://developer.fieldnation.com/docs/rest-api/getting-started/authentication/ (token request, query-param token)
- https://developer.fieldnation.com/docs/getting-started/prerequisites/ (Company ID, x-fn-company-id header)
- https://developer.fieldnation.com/docs/rest-api/getting-started/basics/ ({utc: "YYYY-MM-DD HH:mm:ss"}, errors)
- https://developer.fieldnation.com/docs/rest-api/work-orders/basics/create/ (body: title, types_of_work, location,
  schedule.service_window, pay, template)
- https://developer.fieldnation.com/docs/rest-api/work-orders/workflow/lifecycle/ (status names and ids, publish)
- https://developer.fieldnation.com/docs/rest-api/work-orders/basics/operations/ (cancel, require_gps, details)
- https://developer.fieldnation.com/docs/rest-api/work-orders/communication/attachments/ (attachment shape)
Unverified: the sandbox token URL (assumed to mirror production on the sandbox host); the exact create response body.
Fixtures in tests/fixtures/adapters/field-nation/ follow the documented shapes (fixture: documented-shape).
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import http, ratelimit
from .base import Adapter, MissingCredentials, observation, prepared_call

PROD = "https://api.fieldnation.com"
SANDBOX = "https://api-sandbox.fndev.net"
API = "/api/rest/v2"
TOKEN_PATH = "/authentication/api/oauth/token"
DOCS = "https://developer.fieldnation.com/docs/rest-api/introduction/"
GET_KEY = "https://developer.fieldnation.com/docs/getting-started/prerequisites/"

STATUS_IDS = {1: "draft", 2: "published", 9: "routed", 3: "assigned", 4: "work done", 5: "approved", 6: "paid",
              7: "cancelled", 10: "deleted"}


def _host():
    return SANDBOX if os.environ.get("FIELDNATION_SANDBOX") == "1" else PROD


def _utc(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def redact(text):
    """Drop access tokens from a URL or message (the token travels in the query string)."""
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", str(text or ""))


class _Tokens:
    def __init__(self):
        self._lock, self._tok = threading.Lock(), {}

    def get(self, host, client_id, client_secret, username, password):
        key = (host, client_id, username)
        with self._lock:
            hit = self._tok.get(key)
            if hit and hit[1] > time.monotonic():
                return hit[0]
        try:
            r = http.post(host + TOKEN_PATH, body={"grant_type": "password", "client_id": client_id,
                                                    "client_secret": client_secret, "username": username, "password": password})
        except http.HTTPError as e:
            if e.status in (400, 401, 403):
                raise MissingCredentials(f"field-nation: the token request was refused (HTTP {e.status}); check the "
                                         "client id, secret, username and password") from None
            raise
        tok = (r or {}).get("access_token") if isinstance(r, dict) else None
        if not isinstance(tok, str) or not tok:
            raise MissingCredentials("field-nation: the token response had no access_token")
        ttl = r.get("expires_in") if isinstance(r.get("expires_in"), (int, float)) else 3600
        with self._lock:
            self._tok[key] = (tok, time.monotonic() + max(ttl - 60, 1))
        return tok

    def clear(self):
        with self._lock:
            self._tok.clear()


_TOKENS = _Tokens()


class FieldNation(Adapter):
    card_id = "field-nation"
    env = "FIELDNATION_CLIENT_SECRET"
    others = ("FIELDNATION_CLIENT_ID", "FIELDNATION_USERNAME", "FIELDNATION_PASSWORD")
    how_to_get = ("Needs an active Field Nation buyer account and an executed Integration Contract (Contact Sales). "
                  "Then Integration > Field Services > REST API v3 card: client id and secret "
                  "(FIELDNATION_CLIENT_ID, FIELDNATION_CLIENT_SECRET), your API user (FIELDNATION_USERNAME, "
                  "FIELDNATION_PASSWORD) and Company ID (FIELDNATION_COMPANY_ID).")
    can_commission = True
    can_cancel = True

    def configured(self):
        return all(os.environ.get(k) for k in (self.env,) + self.others)

    def _token(self):
        missing = [k for k in self.others if not os.environ.get(k)]
        if missing:
            raise MissingCredentials(f"{self.card_id}: set {', '.join(missing)}. {self.how_to_get}")
        return _TOKENS.get(_host(), os.environ["FIELDNATION_CLIENT_ID"], self.key(), os.environ["FIELDNATION_USERNAME"],
                           os.environ["FIELDNATION_PASSWORD"])

    def _send(self, method, path, params=None, body=None):
        q = dict(params or {})
        q["access_token"] = self._token()
        h = {"x-fn-company-id": os.environ["FIELDNATION_COMPANY_ID"]} if os.environ.get("FIELDNATION_COMPANY_ID") else {}
        try:
            return http.request(method, _host() + API + path, params=q, body=body, headers=h)
        except http.HTTPError as e:
            raise map_error(self.card_id, e) from None
        except (http.UnsafeURL, http.TooLarge, OSError) as e:
            raise type(e)(redact(str(e))) from None

    # ---- work order body

    def body(self, spec, now=None):
        """POST /workorders body. spec: title, need/description, type_of_work_id (or FIELDNATION_TYPE_OF_WORK_ID),
        template_id, location {address1, city, state, zip, country} or address (one line), start/end (aware datetimes or
        UTC "YYYY-MM-DD HH:MM:SS"), deliver_by (ISO), pay_usd (fixed) or pay_hourly_usd + hours, require_gps (default
        True: GPS check-in), project_id."""
        now = now or datetime.now(timezone.utc)
        loc = spec.get("location")
        if not loc and spec.get("address"):
            loc = {"address1": spec["address"]}
        if loc:
            loc = {"mode": "custom", **{k: v for k, v in loc.items() if v is not None}}
        tow = spec.get("type_of_work_id") or os.environ.get("FIELDNATION_TYPE_OF_WORK_ID")
        start, end = spec.get("start"), spec.get("end")
        if not end and spec.get("deliver_by"):
            try:
                end = datetime.fromisoformat(str(spec["deliver_by"]).replace("Z", "+00:00"))
            except ValueError:
                end = None
        if end and not start:
            start = now + timedelta(hours=1)
        window = None
        if start and end:
            window = {"mode": "between", "start": {"utc": _fmt(start)}, "end": {"utc": _fmt(end)}}
        elif start:
            window = {"mode": "exact", "start": {"utc": _fmt(start)}}
        pay = None
        if spec.get("pay_usd") is not None:
            pay = {"type": "fixed", "base": {"amount": float(spec["pay_usd"]), "units": 1}}
        elif spec.get("pay_hourly_usd") is not None:
            pay = {"type": "hourly", "base": {"amount": float(spec["pay_hourly_usd"]), "units": float(spec.get("hours") or 1)}}
        need = spec.get("description") or spec.get("need")
        b = {"title": (spec.get("title") or need or "Site visit")[:120],
             "description": {"html": "<p>" + _esc(need) + "</p>"} if need else None,
             "types_of_work": [{"id": int(tow), "isPrimary": True}] if tow else None,
             "template": {"id": int(spec["template_id"])} if spec.get("template_id") else None,
             "project": {"id": int(spec["project_id"])} if spec.get("project_id") else None,
             "location": loc, "schedule": {"service_window": window} if window else None, "pay": pay,
             "require_gps": spec.get("require_gps", True)}
        return {k: v for k, v in b.items() if v is not None}

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"address": req.get("place"), "need": req.get("need"), "deliver_by": deliver_by}
        if not deliver_by and req.get("within_minutes"):
            s["deliver_by"] = (datetime.now(timezone.utc) + timedelta(minutes=int(req["within_minutes"]))).isoformat()
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _missing(body):
        m = []
        if not body.get("location"):
            m.append("address: Field Nation needs the site address (address1, city, state, zip)")
        if not body.get("types_of_work") and not body.get("template"):
            m.append("type_of_work_id: from GET /types-of-work (or a template_id from GET /templates)")
        if not body.get("schedule") and not body.get("template"):
            m.append("within_minutes: Field Nation needs a service window")
        if not body.get("pay") and not body.get("template"):
            m.append("pay_usd: the buyer sets the pay (fixed or hourly, USD)")
        return m

    # ---- operations

    def commission(self, spec, confirm=False):
        """confirm=False sends nothing. confirm=True creates the draft and, unless spec["publish"] is False, publishes it
        to the marketplace (POST /workorders/{id}/publish). Pay is due when the buyer approves the work."""
        body = self.body(spec)
        missing = self._missing(body)
        if not confirm:
            out = self._dry("POST", f"{_host()}{API}/workorders?access_token=***", body, {"x-fn-company-id": "{FIELDNATION_COMPANY_ID}"})
            out["note"] = ("Nothing was sent. confirm=True creates the work order (a Draft) and publishes it to Field "
                           "Nation's marketplace. Field Nation has no quote endpoint; the pay above is your offer.")
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise ValueError("field-nation: " + "; ".join(missing))
        wo = self._send("POST", "/workorders", body=body)
        wid = _wo_id(wo)
        if spec.get("publish", True):
            wo = dict(wo or {}, publish=self._send("POST", f"/workorders/{wid}/publish"))
        return wo

    def status(self, order):
        return self._send("GET", f"/workorders/{_wo_id(order)}")

    def attachments(self, order):
        return self._send("GET", f"/workorders/{_wo_id(order)}/attachments")

    def cancel(self, order, reason="Cancelled by the requester"):
        return self._send("DELETE", f"/workorders/{_wo_id(order)}", params={"cancel_reason": reason})

    # ---- ready calls (offline)

    def prepare(self, req, route, operation="order", order_id=None):
        if route != "commission":
            return None
        ph = [{"name": "FIELDNATION_ACCESS_TOKEN", "what": "an OAuth access token (about 1 hour)",
               "where": f"POST {PROD}{TOKEN_PATH} JSON {{grant_type: password, client_id, client_secret, username, password}}"},
              {"name": "FIELDNATION_COMPANY_ID", "what": "your Company ID", "where": "Field Nation integration settings"}]
        hdr = {"x-fn-company-id": "{FIELDNATION_COMPANY_ID}", "Content-Type": "application/json"}
        auth = dict(auth_kind="oauth2", key_name=self.env, key_in="token request body; the access token then goes in "
                    "the access_token query parameter", get_key_url=GET_KEY, docs_url=DOCS)
        token_note = (f"OAuth 2.0 password grant: POST {PROD}{TOKEN_PATH}; the access token goes in the access_token "
                      "query parameter. ")
        if operation == "status":
            if not order_id:
                ph.append({"name": "FIELDNATION_WORK_ORDER_ID", "what": "the work order id", "where": "`id` from POST /workorders"})
            return prepared_call(self.card_id, "status", "GET",
                                 f"{PROD}{API}/workorders/{order_id or '{FIELDNATION_WORK_ORDER_ID}'}?access_token={{FIELDNATION_ACCESS_TOKEN}}",
                                 headers={"x-fn-company-id": "{FIELDNATION_COMPANY_ID}"}, placeholders=ph, **auth,
                                 notes=token_note + "status.name: Draft, Published, Routed, Assigned, Work Done, Approved, "
                                                    "Paid, Cancelled or Deleted. Attachments are in the response.")
        if operation != "order":
            return None
        body = self.body(self.spec_from_request(req))
        if not body.get("location"):
            body["location"] = {"mode": "custom", "address1": "{ADDRESS1}", "city": "{CITY}", "state": "{STATE}", "zip": "{ZIP}"}
            ph.append({"name": "ADDRESS1", "what": "street, city, state and zip of the site", "where": "ask your person"})
        else:
            ph.append({"name": "CITY/STATE/ZIP", "what": "split the address into address1, city, state, zip, country",
                       "where": "Field Nation's location object has separate fields"})
        if not body.get("types_of_work"):
            body["types_of_work"] = [{"id": "{FIELDNATION_TYPE_OF_WORK_ID}", "isPrimary": True}]
            ph.append({"name": "FIELDNATION_TYPE_OF_WORK_ID", "what": "the type of work", "where": "GET /types-of-work"})
        if not body.get("schedule"):
            body["schedule"] = {"service_window": {"mode": "exact", "start": {"utc": "{START_UTC}"}}}
            ph.append({"name": "START_UTC", "what": "arrival time, UTC 'YYYY-MM-DD HH:MM:SS'", "where": "ask your person"})
        if not body.get("pay"):
            body["pay"] = {"type": "fixed", "base": {"amount": "{PAY_USD}", "units": 1}}
            ph.append({"name": "PAY_USD", "what": "what you offer the technician, in USD", "where": "your person's budget"})
        return prepared_call(self.card_id, "order", "POST", f"{PROD}{API}/workorders?access_token={{FIELDNATION_ACCESS_TOKEN}}",
                             headers=hdr, body=body, placeholders=ph, **auth,
                             notes=token_note + "Creates a Draft (visible only to you). Then POST /workorders/{id}/publish "
                                                "(marketplace), /route (chosen providers) or /assignee (direct). Approve with "
                                                "POST /workorders/{id}/approve to release pay. Sandbox: " + SANDBOX + API + ".")

    # ---- results

    status_map = {"draft": "in_flight", "published": "in_flight", "routed": "in_flight", "assigned": "in_flight",
                  "work done": "in_flight",   # done by the provider, awaiting the buyer's approval
                  "approved": "delivered", "paid": "delivered", "cancelled": "failed", "deleted": "failed"}

    def provider_status(self, resp):
        s = (resp or {}).get("status") if isinstance(resp, dict) else None
        if isinstance(s, str):
            return s
        if isinstance(s, dict):
            if isinstance(s.get("name"), str) and s["name"].lower() in self.status_map:
                return s["name"]
            if s.get("id") in STATUS_IDS:   # Assigned shows sub-status names; its id is always 3
                return STATUS_IDS[s["id"]]
            return s.get("name") if isinstance(s.get("name"), str) else None
        return None

    def deliverables(self, resp):
        """Files in a work order's attachment folders (provider uploads such as "Completion Photos"). created.utc is the
        upload time, not the capture time, so phenomenon_time stays None and the upload time is kept as uploaded_at."""
        r = resp if isinstance(resp, dict) else {}
        att = r.get("attachments", r)
        folders = att.get("results") if isinstance(att, dict) else att
        out = []
        for f in folders if isinstance(folders, list) else []:
            for it in (f or {}).get("results") or [] if isinstance(f, dict) else []:
                file = (it or {}).get("file") or {}
                link = file.get("link")
                if not isinstance(link, str) or not link:
                    continue
                name = file.get("name") or "file"
                kind = "photo" if re.search(r"\.(jpe?g|png|gif)$", name, re.I) else "report"
                up = ((it.get("created") or {}).get("utc") or "").replace(" ", "T")
                out.append(observation(self.card_id, kind, None, None, None, label=f"{f.get('name')}: {name}", url=link,
                                       uploaded_at=(up + "Z") if up else None))
        return out


def _fmt(t):
    return _utc(t) if isinstance(t, datetime) else str(t)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wo_id(order):
    v = (order.get("id") or order.get("work_order_id")) if isinstance(order, dict) else order
    if not v:
        raise ValueError("field-nation: the order has no work order id")
    return str(v)


def map_error(card_id, e):
    """Every error loses the access token from its URL and body. 401/403 → MissingCredentials (and the cached token is
    dropped); the rest stay HTTPError with `retry_after` from Retry-After on 429/503."""
    if e.status in (401, 403):
        _TOKENS.clear()
        return MissingCredentials(f"{card_id}: the provider refused the request (HTTP {e.status}): the token is missing, "
                                  "expired or lacks permission for this resource.")
    err = http.HTTPError(e.status, redact(e.url), redact(e.body), e.headers)
    err.retry_after = ratelimit.retry_after(e.headers) if e.status in (429, 503) else None
    return err

