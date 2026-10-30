"""SkyFi: task a new satellite capture (orders cost money).
Key in SKYFI_API_KEY (every account gets one). Public pricing: archive from $5/km², new optical from $8/km², 25 km² minimum.

Docs: https://app.skyfi.com/platform-api/redoc (POST /order-tasking/validate returns the estimated cost without creating an
order; POST /order-tasking charges the account; GET /orders/{order_id}; header X-Skyfi-Api-Key)."""
from datetime import datetime, timedelta, timezone

from .. import geo, http
from .base import Adapter, pick_operation, prepared_call

BASE = "https://app.skyfi.com/platform-api"


def wkt_box(lat, lon, radius_km):
    w, s, e, n = geo.bbox(lat, lon, radius_km)
    return f"POLYGON(({w:.6f} {s:.6f}, {e:.6f} {s:.6f}, {e:.6f} {n:.6f}, {w:.6f} {n:.6f}, {w:.6f} {s:.6f}))"


class SkyFi(Adapter):
    card_id = "skyfi"
    env = "SKYFI_API_KEY"
    how_to_get = "Create a SkyFi account; the API key is in account settings."
    can_commission = True

    def _h(self, required=True):
        return {"X-Skyfi-Api-Key": self.key(required)}

    def prepare(self, req, route, operation=None):
        """quote: POST /order-tasking/validate (default; nothing ordered); order: POST /order-tasking; status: one order."""
        op = pick_operation(route, operation, "commission", ("quote", "order", "status"), "quote")
        if not op:
            return None
        h = {"X-Skyfi-Api-Key": "{" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header X-Skyfi-Api-Key",
                    get_key_url="https://app.skyfi.com/profile/api-keys-management", docs_url="https://app.skyfi.com/platform-api/redoc")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/orders/{{ORDER_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "ORDER_ID", "what": "the order id POST /order-tasking returned", "where": "its response"}],
                                 notes="Status history CREATED ... PROVIDER_PENDING ... DELIVERY_COMPLETED, or a *_FAILED status; "
                                       "GET /orders/{id}/image redirects to a signed download.")
        if req.get("lat") is None or req.get("lon") is None:
            return None
        mins = req.get("within_minutes")
        days = max(1, -(-int(mins) // 1440)) if isinstance(mins, (int, float)) and mins > 0 else 7
        body = self._body({"lat": req["lat"], "lon": req["lon"], "window_days": days})
        h = dict(h, **{"Content-Type": "application/json"})
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/order-tasking/validate", headers=h, body=body, **auth,
                                 notes="Returns the estimated cost and resolved provider; creates nothing. 25 km² minimum AOI. "
                                       "If approved, POST the same body to /order-tasking.")
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/order-tasking", headers=h, body=body, **auth,
                             notes="Tasks a new capture and charges the account. Needs the person's approval of the "
                                   "/order-tasking/validate estimate first.")

    @staticmethod
    def _body(spec):
        lat, lon = spec["lat"], spec["lon"]
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        return {"aoi": wkt_box(lat, lon, max(spec.get("radius_km", 2.5), 2.5)), "windowStart": start.isoformat(),
                "windowEnd": (start + timedelta(days=spec.get("window_days", 7))).isoformat(),
                "productType": spec.get("product_type", "DAY"), "resolution": spec.get("resolution", "HIGH"),
                "maxCloudCoveragePercent": spec.get("max_cloud", 20)}

    def commission(self, spec, confirm=False):
        """New capture. spec: lat, lon, radius_km (≥2.5 → 25 km²), window_days, product_type ("DAY"), resolution ("HIGH").
        Without confirm, calls /order-tasking/validate for a real price quote and places nothing."""
        body = self._body(spec)
        if not confirm:
            if not spec.get("quote", True):
                return self._dry("POST", f"{BASE}/order-tasking", body, self._h(required=False))
            quote = http.post(f"{BASE}/order-tasking/validate", body=body, headers=self._h())
            return {"dry_run": True, "quote": quote, "would_send": {"method": "POST", "url": f"{BASE}/order-tasking", "body": body}}
        return http.post(f"{BASE}/order-tasking", body=body, headers=self._h())

    def status(self, order):
        oid = order.get("id") or order.get("orderId")
        return http.get(f"{BASE}/orders/{oid}", headers=self._h())

    # GET /orders/{id} reports CREATED ... PROVIDER_PENDING ... DELIVERY_COMPLETED, or a *_FAILED status.
    status_map = {"created": "in_flight", "provider_pending": "in_flight", "delivery_completed": "delivered"}

    def map_status(self, raw):
        if isinstance(raw, str) and raw.upper().endswith("_FAILED"):
            return "failed"
        return super().map_status(raw)
