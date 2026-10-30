"""Zeitview Partners API (formerly the DroneBase API): create drone missions server to server. Access is by contract:
"In order to access this API, please contact our team." Credentials go in two headers, X-Api-Client and X-Api-Secret
(ZEITVIEW_API_CLIENT, ZEITVIEW_API_SECRET), and every mission needs a package id the team defines with you
(ZEITVIEW_PACKAGE_ID or spec["package_id"]).

- Quote: POST /v2/partners/missions/preview is a documented dry run. It "will not persist your request", checks FAA
  airspace and returns four days of hourly timeslots with pilot availability and weather. It has no price, so an
  offer built from it is never firm. commission(confirm=False) calls it when credentials and a package id are set
  (it saves nothing and costs nothing); otherwise it sends nothing.
- Order: POST /v1/partners/missions/ with a unique external_id (Zeitview's own de-duplication key).
- Status: GET /v1/partners/missions/{mission_id}. Cancel: DELETE /v1/partners/missions/{mission_id} while the
  mission is created, confirmed, pilots_notified or pilot_accepted, and at least 24 h before a scheduled start.
- Images: GET /v1/partners/missions/{mission_id}/images (signed URLs, about 6 hours; taken_at and GPS per image).

Host: the docs list only https://sandbox-api.dronebase.com (labelled "production" in the Apiary metadata), and it does
not resolve (2026-10-28). The production host is not documented, so set ZEITVIEW_API_BASE to the host Zeitview gives
you. Ready calls write it as {ZEITVIEW_API_BASE}.

Sources (read 2026-10-28):
- https://dronebaseapi.docs.apiary.io/ and its blueprint https://jsapi.apiary.io/apis/dronebaseapi/blueprint
  (last updated 2026-03-18): paths, headers, request and response examples, the status list in status_timestamps
  and activity_logs ("Status or event (see list in status_timestamps)"), cancel rules, 403/404/422 examples.
Fixtures in tests/fixtures/adapters/zeitview/ follow those examples (fixture: documented-shape).
"""
import os
import uuid

from .. import http, ratelimit
from .base import Adapter, MissingCredentials, observation, prepared_call

DOCUMENTED_HOST = "https://sandbox-api.dronebase.com"
DOCS = "https://dronebaseapi.docs.apiary.io/"
GET_KEY = DOCS   # "In order to access this API, please contact our team."


def _base():
    return (os.environ.get("ZEITVIEW_API_BASE") or DOCUMENTED_HOST).rstrip("/")


class Zeitview(Adapter):
    card_id = "zeitview"
    env = "ZEITVIEW_API_SECRET"
    client_env = "ZEITVIEW_API_CLIENT"
    how_to_get = ("Contract only: ask Zeitview's team for Partners API access. They issue the X-Api-Client and "
                  "X-Api-Secret values (ZEITVIEW_API_CLIENT, ZEITVIEW_API_SECRET), define a mission package with you "
                  "(ZEITVIEW_PACKAGE_ID) and give the API host (ZEITVIEW_API_BASE).")
    can_commission = True
    can_cancel = True

    def configured(self):
        return bool(os.environ.get(self.env)) and bool(os.environ.get(self.client_env))

    def _h(self, required=True):
        cid = os.environ.get(self.client_env)
        if required and not cid:
            raise MissingCredentials(f"{self.card_id}: set {self.client_env}. {self.how_to_get}")
        return {"X-Api-Client": cid or "<" + self.client_env + ">", "X-Api-Secret": self.key(required)}

    def _send(self, method, path, body=None, params=None):
        try:
            return http.request(method, _base() + path, body=body, params=params, headers=self._h())
        except http.HTTPError as e:
            raise map_error(self.card_id, e) from None

    # ---- mission body

    def body(self, spec):
        """Mission body from a spec: lat, lon and/or address, name, need/instructions, package_id, external_id,
        reference_id, portfolio, scheduled_at_start/end (same day, 24 h ahead), annotations (GeoJSON FeatureCollection)."""
        loc = {"name": (spec.get("name") or spec.get("address") or spec.get("need") or "irlpls mission")[:256]}
        if spec.get("lat") is not None and spec.get("lon") is not None:
            loc["coordinate"] = {"latitude": float(spec["lat"]), "longitude": float(spec["lon"])}
        if spec.get("address"):
            loc["address"] = {"address": spec["address"]}
        if spec.get("annotations"):
            loc["annotations"] = spec["annotations"]
        b = {"external_id": spec.get("external_id"), "package": {"id": spec.get("package_id") or os.environ.get("ZEITVIEW_PACKAGE_ID")},
             "location": loc, "instructions": (spec.get("instructions") or spec.get("need") or "")[:512] or None,
             "reference_id": spec.get("reference_id"), "portfolio": spec.get("portfolio"),
             "scheduled_at_start": spec.get("scheduled_at_start"), "scheduled_at_end": spec.get("scheduled_at_end")}
        if not b["package"]["id"]:
            b["package"] = None
        return {k: v for k, v in b.items() if v is not None}

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "address": req.get("place"), "need": req.get("need"),
             "external_id": ext_id}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _missing(body):
        m = []
        if not body.get("package"):
            m.append("package_id: Zeitview missions need a package id defined with Zeitview's team (ZEITVIEW_PACKAGE_ID)")
        loc = body.get("location") or {}
        if not loc.get("coordinate") and not loc.get("address"):
            m.append("address: Zeitview needs an address or coordinates for the mission")
        return m

    # ---- operations

    def commission(self, spec, confirm=False):
        body = self.body(spec)
        missing = self._missing(body)
        if not confirm:
            preview_body = dict(body, external_id=body.get("external_id") or "preview")
            out = self._dry("POST", f"{_base()}/v1/partners/missions/", dict(body, external_id=body.get("external_id") or "{EXTERNAL_ID}"),
                            {"X-Api-Client": "***", "X-Api-Secret": "***"})
            if missing:
                out["missing"] = missing
                return out
            if not self.configured():
                out["note"] = ("Nothing was sent. With ZEITVIEW_API_CLIENT and ZEITVIEW_API_SECRET set, the dry run asks "
                               "POST /v2/partners/missions/preview (saves nothing) for airspace and pilot availability.")
                return out
            out["quote"] = self.preview(preview_body)
            out["note"] = ("Only the preview was sent; it saves nothing and has no price. Pass confirm=True to create the "
                           "mission.")
            return out
        if missing:
            raise ValueError("zeitview: " + "; ".join(missing))
        if not body.get("external_id"):
            body["external_id"] = "irlpls-" + uuid.uuid4().hex
        return self._send("POST", "/v1/partners/missions/", body)

    def preview(self, body):
        """POST /v2/partners/missions/preview: airspace check plus four days of timeslots. Never persists anything.
        Returns {"airspace": "ok", "scheduling_options": [...], "available_slots": n} or, on a 422 airspace rejection,
        {"airspace": "rejected", "errors": {...}}. No price is ever in it."""
        try:
            r = self._send("POST", "/v2/partners/missions/preview", body)
        except http.HTTPError as e:
            if e.status == 422:
                return {"airspace": "rejected", "errors": _json(e.body)}
            raise
        opts = (r or {}).get("scheduling_options") or []
        slots = [t for d in opts if isinstance(d, dict) for t in d.get("timeslots") or [] if isinstance(t, dict)]
        return {"airspace": "ok", "scheduling_options": opts,
                "available_slots": sum(1 for t in slots if t.get("pilot_availability") is True), "slots": len(slots)}

    def status(self, order):
        return self._send("GET", f"/v1/partners/missions/{_mission_id(order)}")

    def cancel(self, order):
        return self._send("DELETE", f"/v1/partners/missions/{_mission_id(order)}")

    def images(self, order, page=1, per_page=100):
        return self._send("GET", f"/v1/partners/missions/{_mission_id(order)}/images",
                          params={"page": page, "per_page": min(int(per_page), 1000)})

    # ---- ready calls (offline)

    def prepare(self, req, route, operation="quote", order_id=None):
        if route != "commission":
            return None
        base = "{ZEITVIEW_API_BASE}"
        hdr = {"X-Api-Client": "{ZEITVIEW_API_CLIENT}", "X-Api-Secret": "{ZEITVIEW_API_SECRET}", "Content-Type": "application/json"}
        ph = [{"name": "ZEITVIEW_API_BASE", "what": "the Partners API host Zeitview gives you",
               "where": "from Zeitview's team; the documented https://sandbox-api.dronebase.com does not resolve"},
              {"name": "ZEITVIEW_API_CLIENT", "what": "your X-Api-Client value", "where": "from Zeitview's team, with the secret"}]
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header X-Api-Secret (plus X-Api-Client)",
                    get_key_url=GET_KEY, docs_url=DOCS)
        if operation == "status":
            if not order_id:
                ph.append({"name": "ZEITVIEW_MISSION_ID", "what": "the mission id", "where": "`id` from the create response"})
            return prepared_call(self.card_id, "status", "GET", f"{base}/v1/partners/missions/{order_id or '{ZEITVIEW_MISSION_ID}'}",
                                 headers=hdr, placeholders=ph, **auth,
                                 notes="`status` is one of the documented mission statuses; `complete` means ready for "
                                       "delivery. Image URLs are signed and expire; fetch fresh ones from /images.")
        spec = self.spec_from_request(req)
        body = self.body(spec)
        if not body.get("package"):
            body["package"] = {"id": "{ZEITVIEW_PACKAGE_ID}"}
            ph.append({"name": "ZEITVIEW_PACKAGE_ID", "what": "the mission package (which shots to fly)",
                       "where": "defined with Zeitview's team; there is no package API"})
        loc = body["location"]
        if not loc.get("coordinate") and not loc.get("address"):
            loc["address"] = {"address": "{ADDRESS}"}
            ph.append({"name": "ADDRESS", "what": "the street address of the site", "where": "ask your person"})
        if operation == "order":
            body["external_id"] = "{EXTERNAL_ID}"
            ph.append({"name": "EXTERNAL_ID", "what": "a unique id for this mission (Zeitview de-duplicates on it)",
                       "where": "generate one per mission and reuse it on retry"})
            return prepared_call(self.card_id, "order", "POST", f"{base}/v1/partners/missions/", headers=hdr, body=body,
                                 placeholders=ph, **auth,
                                 notes="Creates the mission. Airspace is checked asynchronously; a rejection arrives by "
                                       "email or webhook. Schedule with scheduled_at_start/end (same day, 24 h ahead). "
                                       "Billing is by invoice under your contract; no price is returned.")
        if operation != "quote":
            return None
        body["external_id"] = "irlpls-preview"
        return prepared_call(self.card_id, "quote", "POST", f"{base}/v2/partners/missions/preview", headers=hdr, body=body,
                             placeholders=ph, **auth, safe_to_run_without_approval=True,
                             notes="Dry run: 'will not persist your request'. Returns FAA airspace validity and four days "
                                   "of hourly timeslots with pilot availability and weather. No price: Zeitview bills "
                                   "by contract, so the offer is never firm. Then POST /v1/partners/missions/ to order.")

    # ---- results

    # The documented status list (status_timestamps keys; activity_logs "Status or event"). The mission "will not
    # necessarily pass through all the statuses". complete = "Mission is complete and ready for delivery".
    status_map = {s: "in_flight" for s in ("created", "confirmed", "scheduling_call", "pilots_notified", "pilot_accepted",
                                           "pilot_arrived", "pilot_departed", "assets_uploaded", "assets_classified",
                                           "in_production", "put_on_hold", "resumed", "rescheduled")}
    status_map.update({"complete": "delivered", "canceled": "failed"})

    def provider_status(self, resp):
        if isinstance(resp, dict) and isinstance(resp.get("mission"), dict):   # webhook payloads wrap the mission
            resp = resp["mission"]
        return super().provider_status(resp)

    def deliverables(self, resp):
        """Images (taken_at and GPS from the image metadata, per the docs), orthomosaics, videos and attachments of a
        mission. Accepts a mission, a webhook payload ({mission: ...}) or an /images page ({data: [...]})."""
        r = resp if isinstance(resp, dict) else {}
        if isinstance(r.get("mission"), dict):
            r = r["mission"]
        coord = ((r.get("location") or {}).get("coordinate") or {}) if isinstance(r.get("location"), dict) else {}
        lat0, lon0 = coord.get("latitude"), coord.get("longitude")
        out = []
        for im in (r.get("images") or r.get("data") or []):
            if not isinstance(im, dict) or not isinstance(im.get("url"), str):
                continue
            shot = (im.get("shot") or {}).get("name") if isinstance(im.get("shot"), dict) else None
            out.append(observation(self.card_id, "photo", im.get("taken_at"), im.get("gps_latitude", lat0),
                                   im.get("gps_longitude", lon0), label=shot or im.get("name"), url=im["url"],
                                   altitude_m=im.get("gps_altitude"), file_name=im.get("name"),
                                   note="signed URL; expires after about 6 hours"))
        for m in r.get("ortho_photo_maps") or []:
            urls = (m or {}).get("remote_urls") or {}
            u = urls.get("tif") or urls.get("jpg")
            if isinstance(u, str):
                out.append(observation(self.card_id, "image", None, lat0, lon0, label="orthomosaic", url=u,
                                       file_name=(m or {}).get("name")))
        for v in r.get("videos") or []:
            if isinstance(v, dict) and isinstance(v.get("url"), str):
                out.append(observation(self.card_id, "video", None, lat0, lon0, label=v.get("name") or "video", url=v["url"]))
        for a in r.get("attachments") or []:
            if isinstance(a, dict) and isinstance(a.get("url"), str):
                out.append(observation(self.card_id, "report", None, lat0, lon0, label=a.get("name") or "attachment", url=a["url"]))
        return out


def _json(text):
    import json
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"error": (text or "")[:300]}


def _mission_id(order):
    v = order.get("id") or order.get("mission_id") if isinstance(order, dict) else order
    if not v:
        raise ValueError("zeitview: the order has no mission id")
    return str(v)


def map_error(card_id, e):
    """401/403 → MissingCredentials; the rest stay HTTPError with `retry_after` set from Retry-After on 429/503."""
    if e.status in (401, 403):
        return MissingCredentials(f"{card_id}: the provider refused the credentials (HTTP {e.status}); check "
                                  "ZEITVIEW_API_CLIENT, ZEITVIEW_API_SECRET and ZEITVIEW_API_BASE.")
    err = http.HTTPError(e.status, e.url, e.body, e.headers)
    err.retry_after = ratelimit.retry_after(e.headers) if e.status in (429, 503) else None
    return err
