"""SkyWatch EarthCache: task (or buy archive) satellite imagery from many partners through one API. Prices in USD.

Sources (public docs, read 2026-10-28):
- OpenAPI 3.1 (servers, x-api-key scheme, /price/calculate request and USD response fields, /pipelines create body,
  pipeline.resource status enum active|inactive|complete|failed|deactivating|pending, DELETE /pipelines/{id} "only orders
  that have not started processing or are in a failed state"): https://docs.skywatch.com/openapi-spec.json
- Tasking workflow: offerings search -> cost calculation -> POST /pipelines with max_cost from the calculator, interval
  >= 7d, at least 2 days before start, output geotiff unstitched, product.configuration.offering_id:
  https://docs.skywatch.com/docs/tasking/tasking-workflow , https://docs.skywatch.com/docs/pricing/pricing
- Constraints (single Polygon, tasking min 7-day interval, cannot cancel within 48 h): https://docs.skywatch.com/llms.txt
- API key from Hub account settings, header x-api-key, no Bearer prefix:
  https://docs.skywatch.com/docs/general/api-authentication

Unverified (kept in notes): `product.configuration.offering_id` appears in the docs' calculator and tasking examples but
not in the OpenAPI create-pipeline schema, and `output.id` is a platform output id the docs do not list publicly.
Keys: SKYWATCH_API_KEY (Hub, by invitation), SKYWATCH_OFFERING_ID (or spec["offering_id"]), SKYWATCH_OUTPUT_ID.
"""
import os
from datetime import timedelta

from . import _a8
from .base import Adapter, MissingCredentials, observation, prepared_call

BASE = "https://api.skywatch.co/earthcache"
DOCS = "https://docs.skywatch.com/docs/tasking/tasking-workflow"
GET_KEY = "https://docs.skywatch.com/docs/general/api-authentication"


class SkyWatch(Adapter):
    card_id = "skywatch"
    env = "SKYWATCH_API_KEY"
    env_offering = "SKYWATCH_OFFERING_ID"
    env_output = "SKYWATCH_OUTPUT_ID"
    how_to_get = ("Log in to a SkyWatch Hub account (by invitation from your organisation admin), open account settings and "
                  "copy the API key.")
    can_commission = True
    can_cancel = True

    def _h(self, required=True):
        return {"x-api-key": self.key(required)}

    def _call(self, method, path, **kw):
        return _a8.call(self.card_id, method, BASE + path, self.env, self.how_to_get, [os.environ.get(self.env)], **kw)

    def spec_from_request(self, req, ext_id, deliver_by):
        return _a8.spec_from_request(req, ext_id, deliver_by)

    # ---- request building
    @staticmethod
    def _dates(spec):
        start, end = _a8.window(spec, lead_h=48, min_days=7, default_days=7)
        days = max((end.date() - start.date()).days, 7)
        return start.date().isoformat(), (start + timedelta(days=days)).date().isoformat(), f"{days}d"

    def _price_body(self, spec, offering):
        s, e, interval = self._dates(spec)
        b = {"resolution": spec.get("resolution", "high"),
             "location": _a8.polygon(spec["lat"], spec["lon"], spec.get("radius_km", 2.5)),
             "start_date": s, "end_date": e, "interval": interval, "tasking": True}
        if offering:
            b["product"] = {"configuration": {"offering_id": offering}}
        return b

    def _pipeline_body(self, spec, offering, output, max_cost):
        s, e, interval = self._dates(spec)
        b = {"name": (spec.get("name") or "irlpls tasking")[:100], "start_date": s, "end_date": e, "interval": interval,
             "aoi": _a8.polygon(spec["lat"], spec["lon"], spec.get("radius_km", 2.5)), "max_cost": max_cost,
             "min_aoi_coverage_percentage": spec.get("min_aoi_coverage_percentage", 80),
             "output": {"id": output, "format": "geotiff", "mosaic": "unstitched"},
             "product": {"configuration": {"offering_id": offering}}}
        if spec.get("cloud_cover_percentage") is not None:
            b["cloud_cover_percentage"] = spec["cloud_cover_percentage"]
        return b

    def offerings(self, lat, lon, radius_km=2.5, category="tasking"):
        """Offerings over a point (needs the key): POST /products/offerings/search."""
        return self._call("POST", "/products/offerings/search", headers=self._h(),
                          body={"location": _a8.polygon(lat, lon, radius_km), "category": category})

    def quote(self, spec):
        """POST /price/calculate: data.max_cost is the USD total for all intervals, minimums and discounts included."""
        offering = spec.get("offering_id") or os.environ.get(self.env_offering)
        return self._call("POST", "/price/calculate", body=self._price_body(spec, offering), headers=self._h())

    def commission(self, spec, confirm=False):
        """Tasking pipeline over a square AOI (radius_km, default 2.5) around spec lat/lon, starting at least 2 days out,
        one interval of window_days (min 7). Without confirm: the pipeline it would create plus, with a key, the price
        calculator's USD max_cost as a firm quote. With confirm: creates the pipeline with max_cost from spec or a fresh
        calculation."""
        missing = _a8.missing_point(spec)
        if missing:
            return {"dry_run": True, "missing": missing, "note": "Nothing was sent: the place is needed."}
        offering = spec.get("offering_id") or os.environ.get(self.env_offering)
        output = spec.get("output_id") or os.environ.get(self.env_output)
        if not confirm:
            body = self._pipeline_body(spec, offering or "{" + self.env_offering + "}", output or "{" + self.env_output + "}",
                                       spec.get("max_cost", "{MAX_COST}"))
            out = self._dry("POST", f"{BASE}/pipelines", body, self._h(required=False))
            if not self.configured():
                return out
            if not offering:
                out["missing"] = ["offering_id: a SkyWatch tasking offering (POST /products/offerings/search, category tasking)"]
                return out
            q = self.quote(spec)
            data = (q or {}).get("data") or {}
            mc = data.get("max_cost")
            if isinstance(mc, (int, float)) and not isinstance(mc, bool):
                body["max_cost"] = mc
            return _a8.put_quote(out, mc, "USD", q)
        if not offering or not output:
            raise MissingCredentials(f"skywatch: set {self.env_offering} and {self.env_output} (or pass offering_id and "
                                     "output_id in spec) to order.")
        mc = spec.get("max_cost")
        if mc is None:
            mc = ((self.quote(spec) or {}).get("data") or {}).get("max_cost")
        if not isinstance(mc, (int, float)) or isinstance(mc, bool):
            raise ValueError("skywatch: no max_cost from the price calculator; nothing was ordered")
        return self._call("POST", "/pipelines", body=self._pipeline_body(spec, offering, output, mc), headers=self._h())

    @staticmethod
    def _pid(order):
        if not isinstance(order, dict):
            return None
        return (order.get("data") or {}).get("id") or order.get("id")

    def status(self, order):
        pid = self._pid(order)
        if not pid:
            raise ValueError("skywatch: the order has no pipeline id")
        return self._call("GET", f"/pipelines/{pid}", headers=self._h())

    def cancel(self, order):
        """DELETE /pipelines/{id}: only before processing starts (and not within 48 h of a tasking start) or after a failure."""
        pid = self._pid(order)
        if not pid:
            raise ValueError("skywatch: the order has no pipeline id")
        return self._call("DELETE", f"/pipelines/{pid}", headers=self._h())

    # pipeline.resource status: pending, active, complete, failed, inactive, deactivating. inactive and deactivating are
    # not described in the docs read, so they stay unmapped (in_flight, reported verbatim).
    status_path = ("data", "status")
    status_map = {"pending": "in_flight", "active": "in_flight", "complete": "delivered", "failed": "failed"}

    def deliverables(self, resp):
        d = (resp or {}).get("data") if isinstance(resp, dict) else None
        if not isinstance(d, dict) or str(d.get("status")).lower() != "complete":
            return []
        return [observation(self.card_id, "satellite-tasking", None, None, None, label=f"SkyWatch pipeline {d.get('id')} complete",
                            order_id=d.get("id"), url=f"{BASE}/pipelines/{d.get('id')}/interval_results",
                            window_start=d.get("start_date"), window_end=d.get("end_date"))]

    # ---- ready calls
    def prepare(self, req, route, operation=None):
        """read: offerings over the place. quote: price calculator (USD). order: create the pipeline. status: one pipeline."""
        op = operation or ("quote" if route == "commission" else None)
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header x-api-key", get_key_url=GET_KEY, docs_url=DOCS)
        h = {"x-api-key": "{" + self.env + "}"}
        if op == "status":
            pid = req.get("order_id") or "{PIPELINE_ID}"
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/pipelines/{pid}", headers=h, **auth,
                                 placeholders=None if req.get("order_id") else [{"name": "PIPELINE_ID", "what": "pipeline (order) id",
                                                                                "where": "data.id of the create response"}],
                                 notes="data.status: pending, active, complete or failed. Results at /pipelines/{id}/interval_results.")
        if op not in ("read", "quote", "order") or req.get("lat") is None or req.get("lon") is None:
            return None
        spec = {"lat": req["lat"], "lon": req["lon"], "name": req.get("need")}
        if op == "read":
            return prepared_call(self.card_id, "read", "POST", f"{BASE}/products/offerings/search", headers=h, **auth,
                                 body={"location": _a8.polygon(req["lat"], req["lon"], 2.5), "category": "tasking"},
                                 notes="Lists tasking offerings over the AOI; take an offering id for the quote and order.")
        ph = [{"name": self.env_offering, "what": "tasking offering id", "where": "POST /products/offerings/search"}]
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/price/calculate", headers=h, **auth,
                                 body=self._price_body(spec, "{" + self.env_offering + "}"), placeholders=ph,
                                 notes="Orders nothing. data.max_cost is the USD total; use it as max_cost in the order.")
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/pipelines", headers=h, **auth,
                             body=self._pipeline_body(spec, "{" + self.env_offering + "}", "{" + self.env_output + "}", "{MAX_COST}"),
                             placeholders=ph + [{"name": self.env_output, "what": "platform output id (geotiff)", "where": "SkyWatch Hub; not in the public docs"},
                                                {"name": "MAX_COST", "what": "USD max_cost", "where": "data.max_cost of the price calculator"}],
                             notes="Spends money; no refunds on machine-to-machine orders. product.configuration.offering_id "
                                   "follows the tasking docs but is not in the OpenAPI schema. No idempotency key is documented.")
