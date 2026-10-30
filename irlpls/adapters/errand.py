"""Errand: dispatch a nearby person in Seoul (Gangnam / Yeoksam beta) to check, verify, photograph, queue or pick up,
and get back app-camera photos with a server GPS/time check and an AI verdict against your criteria. Key in
ERRAND_API_KEY (an er_live_ key: enter an e-mail at errand.be and type the 6-digit code). Prepaid in KRW.

Docs (public, read 2026-10-28):
- https://errand.be/docs : REST base https://errand.be/api/v1; keyless GET /api/public/coverage?lat=&lng=&radius_m=;
  POST /api/v1/quote (creates nothing; total_charge_krw, can_dispatch); POST /api/v1/dispatches (field rules);
  GET /api/v1/dispatches/{id}; GET /api/v1/dispatches/{id}/result; POST /api/v1/dispatches/{id}/cancel (only while
  open); lifecycle open -> accepted -> arrived -> submitted -> completed, or failed / expired / cancelled; error codes
  401 UNAUTHORIZED, 402 INSUFFICIENT_BALANCE, 403 CAP_*, 400 VALIDATION, 404 NOT_FOUND, 409, 422 UNSAFE_TASK, 429.
- https://errand.be/llms.txt (safe agent flow: quote, show total_charge_krw, dispatch only after a yes).

Text the worker reads (title, instructions, validation_criteria) should be Korean per the docs; this module passes the
need through as written and says so in the dry run. Quotes are in KRW, so they are never turned into a USD total (offers
stay firm: false; STANDARD 14.3). The keyless coverage read was live-checked once on 2026-10-28; the keyed calls are
built from the docs' examples (fixtures marked documented-shape).
"""
from .. import http
from . import _a4
from .base import Adapter, MissingCredentials, NotSupported, observation, prepared_call

SITE = "https://errand.be"
BASE = SITE + "/api/v1"
DOCS = SITE + "/docs"
TASK_TYPES = ("check", "verify", "photograph", "queue", "pickup")


class Errand(Adapter):
    card_id = "errand"
    env = "ERRAND_API_KEY"
    how_to_get = "Enter an e-mail at https://errand.be, type the 6-digit code, and copy the er_live_ key (shown once)."
    can_commission = True
    can_cancel = True

    def _h(self, required=True):
        return {"Authorization": "Bearer " + self.key(required)}

    def _call(self, method, url, body=None):
        try:
            return http.request(method, url, body=body, headers=self._h())
        except http.HTTPError as e:
            _a4.raise_if_unauthorized(self.card_id, e, self.env, self.how_to_get)
            raise

    # ---- reads
    def coverage(self, lat, lon, radius_m=3000):
        """Keyless: {online_workers, reachable_workers, dispatchable, radius_m}. reachable_workers is what counts.
        Returns (coverage dict or None, warning or None)."""
        try:
            return http.get(f"{SITE}/api/public/coverage", params={"lat": lat, "lng": lon, "radius_m": int(radius_m)}), None
        except http.HTTPError as e:
            return None, _a4.http_warning(self.card_id, e, "checking coverage")

    def quote(self, spec):
        """POST /api/v1/quote: creates nothing. Returns the provider's quote (KRW fields) as is."""
        body = {"task_type": spec.get("task_type") or "check", "lat": spec.get("lat"), "lng": spec.get("lon"),
                "reward_krw": spec.get("reward_krw")}
        return self._call("POST", f"{BASE}/quote", body)

    # ---- order
    def _body(self, spec):
        need = spec.get("instructions") or spec.get("notes") or ""
        mins = spec.get("expires_in_minutes") or _a4.minutes_until(spec.get("deadline"), None) or 180
        body = {"task_type": spec.get("task_type") or "check",
                "title": _a4.clip(spec.get("title") or need, 120),
                "instructions": _a4.clip(need, 2000),
                "validation_criteria": _a4.clip(spec.get("validation_criteria") or ("The photo clearly shows: " + need), 1000),
                "lat": spec.get("lat"), "lng": spec.get("lon"),
                "radius_m": int(_a4.clamp(spec.get("radius_m") or 3000, 100, 10000)),
                "reward_krw": spec.get("reward_krw"),
                "expires_in_minutes": int(_a4.clamp(mins, 10, 1440)),
                "address_hint": spec.get("address") or spec.get("address_hint"),
                "report_fields": spec.get("report_fields"), "webhook_url": spec.get("webhook_url")}
        body = {k: v for k, v in body.items() if v is not None}
        missing = []
        if body["task_type"] not in TASK_TYPES:
            missing.append("task_type: one of " + ", ".join(TASK_TYPES))
        if body.get("lat") is None or body.get("lng") is None:
            missing.append("lat: Errand dispatches to coordinates (lat and lon)")
        r = body.get("reward_krw")
        if not (isinstance(r, int) and not isinstance(r, bool) and 1000 <= r <= 200000):
            missing.append("reward_krw: the worker's reward in KRW, 1,000-200,000 (you set it; a 20% fee, at least 500, is added)")
        if len(body["title"]) < 4 or len(body["instructions"]) < 10 or len(body["validation_criteria"]) < 10:
            missing.append("instructions: what to do on site (title 4+, instructions 10+, validation_criteria 10+ characters)")
        return body, missing

    def commission(self, spec, confirm=False):
        """spec: lat, lon, reward_krw (int), task_type (check|verify|photograph|queue|pickup), title, instructions (or
        notes), validation_criteria, radius_m, expires_in_minutes (or deadline), address, report_fields, webhook_url.
        Dry run: with a key, the provider's own quote (POST /quote, creates nothing); without one, the keyless coverage
        check. confirm=True dispatches: reward + fee are escrowed in KRW at once."""
        body, missing = self._body(spec)
        url = f"{BASE}/dispatches"
        if confirm:
            if missing:
                raise NotSupported(f"{self.card_id}: cannot dispatch, missing " + "; ".join(m.split(":")[0] for m in missing))
            return self._call("POST", url, body)
        out = self._dry("POST", url, body, self._h(required=False))
        out["note"] = ("Nothing was sent. Show the user total_charge_krw from the quote and dispatch only on a yes and "
                       "when can_dispatch is true. Errand prices in KRW, so irlpls gives no USD total. The worker reads "
                       "title, instructions and validation_criteria on a phone: Errand asks for them in Korean.")
        if missing:
            out["missing"] = missing
        warnings = []
        if body.get("lat") is not None and body.get("lng") is not None:
            if self.configured() and isinstance(body.get("reward_krw"), int):
                try:
                    out["quote"] = self.quote({**spec, "task_type": body["task_type"]})
                    tot = out["quote"].get("total_charge_krw") if isinstance(out["quote"], dict) else None
                    if type(tot) is int and tot >= 0:   # shown in KRW, never converted, never a firm USD offer
                        out["provider_quote"] = {"amount": tot, "currency": "KRW", "basis": "total_charge_krw"}
                except http.HTTPError as e:
                    warnings.append(_a4.http_warning(self.card_id, e, "quoting"))
                except MissingCredentials as e:
                    warnings.append({"provider": self.card_id, "code": "needs_key", "msg": str(e)})
            else:
                cov, w = self.coverage(body["lat"], body["lng"], body.get("radius_m", 3000))
                if cov is not None:
                    out["coverage"] = cov
                if w:
                    warnings.append(w)
        if warnings:
            out["warnings"] = warnings
        return out

    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or ""
        tt = ("queue" if _a4.QUEUE.search(need) else "photograph" if _a4.PHOTO.search(need)
              else "verify" if _a4.VERIFY.search(need) else "check")
        mins = _a4.minutes_until(deliver_by, req.get("within_minutes"))
        s = {"task_type": tt, "title": need, "instructions": need, "lat": req.get("lat"), "lon": req.get("lon"),
             "address": req.get("place"), "reward_krw": req.get("reward_krw"),
             "expires_in_minutes": int(_a4.clamp(mins, 10, 1440)) if mins else None}
        return {k: v for k, v in s.items() if v is not None}

    def prepare(self, req, route):
        """The quote call (creates nothing): the step Errand asks agents to take before any dispatch."""
        if route != "commission" or req.get("lat") is None or req.get("lon") is None:
            return None
        spec = self.spec_from_request(req)
        body = {"task_type": spec["task_type"], "lat": round(float(req["lat"]), 6), "lng": round(float(req["lon"]), 6),
                "reward_krw": "{REWARD_KRW}"}
        return prepared_call(
            self.card_id, "quote", "POST", f"{BASE}/quote",
            headers={"Authorization": "Bearer {ERRAND_API_KEY}", "Content-Type": "application/json"}, body=body,
            auth_kind="bearer", key_name=self.env, key_in="header Authorization: Bearer", get_key_url=SITE, docs_url=DOCS,
            placeholders=[{"name": "REWARD_KRW", "what": "the worker's reward in KRW, an integer 1,000-200,000 (a 20% fee, "
                           "at least 500, is added)", "where": "your user's choice; the key's per-task cap applies"}],
            notes="Creates nothing; returns total_charge_krw and can_dispatch. Keyless first look: GET "
                  f"{SITE}/api/public/coverage?lat=..&lng=..&radius_m=3000 (reachable_workers). Then, after the user "
                  "approves the KRW total: POST /api/v1/dispatches {task_type, title, instructions, validation_criteria "
                  "(Korean), lat, lng, radius_m, reward_krw, expires_in_minutes}; poll GET /api/v1/dispatches/{id} and read "
                  "GET /api/v1/dispatches/{id}/result. Coverage is Seoul (Gangnam) only.")

    # ---- status / cancel
    def _id(self, order):
        did = (order or {}).get("dispatch_id") or (order or {}).get("id")
        if not did:
            raise NotSupported(f"{self.card_id}: the order has no dispatch_id")
        return did

    def status(self, order):
        """GET /dispatches/{id}; once completed, the result (GET /dispatches/{id}/result) is attached under "result"."""
        did = self._id(order)
        d = self._call("GET", f"{BASE}/dispatches/{did}") or {}
        if isinstance(d, dict) and str(d.get("status", "")).lower() == "completed":
            d = dict(d, result=self._call("GET", f"{BASE}/dispatches/{did}/result"))
        return d

    def cancel(self, order):
        """POST /dispatches/{id}/cancel: only while open (409 INVALID_TRANSITION after a worker accepts); full refund."""
        return self._call("POST", f"{BASE}/dispatches/{self._id(order)}/cancel")

    # Lifecycle per the docs: open -> accepted -> arrived (GPS on site) -> submitted (being checked) -> completed;
    # failed (both attempts rejected), expired and cancelled end it, all refunded.
    status_map = {"open": "in_flight", "accepted": "in_flight", "arrived": "in_flight", "submitted": "in_flight",
                  "completed": "delivered", "failed": "failed", "expired": "failed", "cancelled": "failed"}

    def deliverables(self, resp):
        """The result: each photo (signed URL, expires in 1 h) with the capture time and GPS fix the server checked, and
        each structured answer. The verdict and checks ride along on every item."""
        r = resp if isinstance(resp, dict) else {}
        res = r.get("result") if isinstance(r.get("result"), dict) else r
        ev = res.get("evidence") if isinstance(res.get("evidence"), dict) else {}
        gps = ev.get("gps") if isinstance(ev.get("gps"), dict) else {}
        lat, lon, t = gps.get("lat"), gps.get("lng"), ev.get("captured_at")
        common = {k: v for k, v in {"verdict": res.get("verdict"), "checks": ev.get("checks"),
                                    "distance_from_task_m": ev.get("distance_from_task_m"),
                                    "gps_accuracy_m": gps.get("accuracy_m"), "share_url": res.get("share_url")}.items()
                  if v is not None}
        out = []
        for i, u in enumerate(ev.get("photo_urls") or []):
            if isinstance(u, str) and u:
                out.append(observation(self.card_id, "photo", t, lat, lon, label=f"photo {i + 1} (link expires in 1 h)",
                                       url=u, **common))
        for k, v in (res.get("answers") or {}).items() if isinstance(res.get("answers"), dict) else ():
            out.append(observation(self.card_id, "report", t, lat, lon, value=v, label=str(k), url=res.get("share_url"),
                                   **{x: y for x, y in common.items() if x != "share_url"}))
        return out
