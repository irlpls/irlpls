"""Vantor Hub (formerly Maxar) Tasking API: WorldView optical tasking (FastView) and radar via Umbra. Contract only:
needs a Vantor Hub subscription. Key in VANTOR_API_KEY, sent as the header maxar-api-key.

DRY RUN ONLY. Tasking moved to V2 endpoints "under the tasking/v2 base path" on 9 December 2025, and the public guides
give the V2 paths only relative to it (/tasking/vantor/estimate, /tasking/vantor, /tasking/umbra/estimate,
/tasking/umbra, /tasking/{id}, /tasking/cancel); the API reference pages that would state the full URL render only in a
browser. The URLs below join the documented path to https://api.maxar.com/tasking/v2, following the V1 layout
(https://api.maxar.com/tasking/v1/tasking/... in the maxar-platform SDK); that join is UNVERIFIED, so this adapter
never sends a request: no quote, order, status or cancel is called until the full paths are confirmed.

Sources (read 2026-10-28):
- V2 base path and endpoint list  https://developers.maxar.com/docs/tasking.md
- Quote, validate (?validation_only=true), submit, get, list, cancel paths; credits reserved at submission
  https://developers.maxar.com/docs/tasking/guides/tasking-guide.md
- FastView body: tasking_tier "fastView", geometry {latitude, longitude, buffer 8x8|2x50|5x10|5x20},
  start_collect_datetime (8 h to 14 days out), tasking_details.max_gsd [0.39, 0.59], product_details.product_type,
  product_details.licensing.end_use_code (required)  https://developers.maxar.com/docs/tasking/guides/fastview-parameters.md
- Statuses INITIAL ... FULFILLED, UNFULFILLED, PARTIALLY-FULFILLED, DENIED, CANCELED, FAILED, ERROR
  https://developers.maxar.com/docs/tasking/guides/tasking-status.md
- API key: header maxar-api-key or query maxar_api_key; 180-day default expiry
  https://developers.maxar.com/docs/authentication/guides/api-key.md
- 1000 new requests per hour across all customers, then 429  https://developers.maxar.com/docs/tasking/guides/request-limits.md"""
from . import _a9
from .base import Adapter, NotSupported, prepared_call

HOST = "https://api.maxar.com"
V2 = HOST + "/tasking/v2"   # documented base path; the join with the guides' relative paths is unverified
DOCS = "https://developers.maxar.com/docs/tasking/guides/tasking-guide.md"
GET_KEY = "https://developers.maxar.com/docs/authentication/guides/api-key.md"
UNVERIFIED = ("UNVERIFIED URL: the guides give '{path}' relative to the tasking/v2 base path; the full URL is inferred "
              "from the V1 layout. Check it in the Hub API reference before running.")
END_USE = _a9.ph("VANTOR_END_USE_CODE", "the intended use of the imagery (required)",
                 "https://developers.maxar.com/docs/tasking/guides/end-use-codes.md")


class VantorHub(Adapter):
    card_id = "vantor-hub"
    env = "VANTOR_API_KEY"
    how_to_get = ("Needs a Vantor Hub subscription: in the Hub UI, profile menu > API Key (expires after 180 days by "
                  "default).")
    can_commission = True

    def _body(self, spec, end_use):
        start, _ = _a9.window(spec, lead_h=8.5, min_width_h=0, max_end_h=None, default_width_h=0)
        body = {"tasking_tier": "fastView", "request_description": str(spec.get("description") or "irlpls tasking")[:200],
                "start_collect_datetime": _a9.iso(start),
                "geometry": {"latitude": spec["lat"], "longitude": spec["lon"], "buffer": spec.get("buffer", "8x8")},
                "tasking_details": {"max_gsd": spec.get("max_gsd", 0.59)},
                "product_details": {"product_type": spec.get("product_type", "system-ready"),
                                    "licensing": {"end_use_code": end_use}}}
        if spec.get("output_configs"):
            body["output_configs"] = spec["output_configs"]
        return body

    def prepare(self, req, route, operation=None, order_id=None):
        """Default: the usage estimate (POST /tasking/vantor/estimate). "order" is the FastView submit; "status" is
        GET /tasking/{id}. All three URLs are unverified joins (see the module docstring)."""
        if route != "commission" or not _a9.has_point(req):
            return None
        op = operation or "quote"
        h = {"maxar-api-key": "{" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header maxar-api-key", get_key_url=GET_KEY,
                    docs_url=DOCS)
        body = self._body(req, "{VANTOR_END_USE_CODE}")
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{V2}/tasking/vantor/estimate", headers=h, body=body,
                                 placeholders=[END_USE], notes=UNVERIFIED.format(path="/tasking/vantor/estimate") +
                                 " Returns the AOI area and the credits the request would consume; reserves nothing. "
                                 "POST /tasking/vantor?validation_only=true checks the FastView start date.", **auth)
        if op == "order":
            return prepared_call(self.card_id, "order", "POST", f"{V2}/tasking/vantor", headers=h, body=body,
                                 placeholders=[END_USE], notes=UNVERIFIED.format(path="/tasking/vantor") +
                                 " Submitting reserves account credits. FastView can be cancelled up to 24 h before "
                                 "start_collect_datetime.", **auth)
        if op == "status":
            tid = order_id or "{VANTOR_TASKING_ID}"
            pl = None if order_id else [_a9.ph("VANTOR_TASKING_ID", "the tasking id (V2 ids end in -TASK)", "the submit response")]
            return prepared_call(self.card_id, "status", "GET", f"{V2}/tasking/{tid}", headers=h, placeholders=pl,
                                 notes=UNVERIFIED.format(path="/tasking/{id}"), **auth)
        return None

    def spec_from_request(self, req, ext_id, deliver_by):
        s = {"lat": req.get("lat"), "lon": req.get("lon"), "description": req.get("need")}
        return {k: v for k, v in s.items() if v is not None}

    def commission(self, spec, confirm=False):
        """FastView request over a point. spec: lat, lon, end_use_code (required; or VANTOR_END_USE_CODE), buffer
        ("8x8"), max_gsd (0.59), product_type ("system-ready"), output_configs. Always a dry run: nothing is sent."""
        end_use = spec.get("end_use_code") or _a9.env("VANTOR_END_USE_CODE")
        missing = [m for m, ok in (("lat", spec.get("lat") is not None), ("lon", spec.get("lon") is not None),
                                   ("end_use_code", end_use)) if not ok]
        if missing:
            return _a9.dry("POST", f"{V2}/tasking/vantor", None, missing=missing)
        if confirm:
            raise NotSupported("vantor-hub: the V2 tasking URLs are not verified from public docs, so this SDK does not "
                               "submit orders; use the prepared call after checking the URL in the Hub API reference.")
        body = self._body(spec, end_use)
        return _a9.dry("POST", f"{V2}/tasking/vantor", body, quote_call={"method": "POST", "url": f"{V2}/tasking/vantor/estimate",
                       "body": body}, note="Nothing was sent: the V2 tasking URLs are unverified, so even the estimate "
                       "is not called. Submitting reserves Hub credits.")

    # Documented statuses (tasking-status guide). PARTIALLY-FULFILLED delivers part of the AOI and ends the request:
    # it is left unmapped rather than called delivered or failed.
    status_map = {**{s: "in_flight" for s in ("initial", "accepted", "awaiting-collection", "collecting", "in-production",
                                              "delivery-success")},
                  "fulfilled": "delivered",
                  **{s: "failed" for s in ("unfulfilled", "denied", "canceled", "failed", "error")}}
