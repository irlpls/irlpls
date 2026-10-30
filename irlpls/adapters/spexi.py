"""Spexi: order a new drone capture (images, panoramas, orthomosaics) over an area, US and Canada. Key in SPEXI_API_KEY.
Ordering needs a service agreement with Spexi; typically days from request to data.

Docs: https://learn.spexi.com/api-reference/introduction, https://learn.spexi.com/api-reference/order-api/tasking-api/submit-tasking-order
(POST /api/v1/order/tasking: intent new_capture, products, geometry) and
https://learn.spexi.com/api-reference/order-api/tasking-api/list-tasking-order-details (GET /api/v1/order/tasking/{id});
header x-api-key. No price call is published (an order passes feasibility and pending_confirmation first)."""
from .. import geo, http
from .base import Adapter, pick_operation, prepared_call

BASE = "https://world.spexi.com/developer-api"


class Spexi(Adapter):
    card_id = "spexi"
    env = "SPEXI_API_KEY"
    how_to_get = "Spexi World > Settings > API Keys (ordering is linked to a service agreement)."
    can_commission = True

    def _h(self, required=True):
        return {"x-api-key": self.key(required)}

    def prepare(self, req, route, operation=None):
        """order: POST /api/v1/order/tasking for a small polygon around the point (default); status: one order."""
        op = pick_operation(route, operation, "commission", ("order", "status"), "order")
        if not op:
            return None
        h = {"x-api-key": "{" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header x-api-key", get_key_url="https://world.spexi.com",
                    docs_url="https://learn.spexi.com/api-reference/introduction")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/api/v1/order/tasking/{{SPEXI_ORDER_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "SPEXI_ORDER_ID", "what": "spexi_order_id from the order", "where": "its response"}],
                                 notes="Per-product status: submitted, in_review, feasibility_check, pending_confirmation, in_planning, "
                                       "capture_in_progress, processing, qa, delivered (or cancelled / unfulfillable). Delivered "
                                       "images: GET /api/v1/order/{id}/images.")
        if req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(
            self.card_id, "order", "POST", f"{BASE}/api/v1/order/tasking", headers=dict(h, **{"Content-Type": "application/json"}),
            body=self._body({"lat": req["lat"], "lon": req["lon"]}), **auth,
            notes="Submits a new-capture order (status submitted). Ordering needs a Spexi service agreement and an org_owner, "
                  "org_admin or Developer API key; the order is confirmed with Spexi before capture. US and Canada. "
                  "Needs the person's approval.")

    @staticmethod
    def _body(spec):
        w, s, e, n = geo.bbox(spec["lat"], spec["lon"], spec.get("radius_km", 0.3))
        return {"intent": "new_capture", "products": [{"product_type": spec.get("product", "image")}],
                "geometry": {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}}

    def commission(self, spec, confirm=False):
        """spec: lat, lon, radius_km (polygon around the point), product ("image" | "panorama" | "orthomosaic")."""
        body = self._body(spec)
        if not confirm:
            return self._dry("POST", f"{BASE}/api/v1/order/tasking", body, self._h(required=False))
        return http.post(f"{BASE}/api/v1/order/tasking", body=body, headers=self._h())

    def status(self, order):
        return http.get(f"{BASE}/api/v1/order/tasking/{order.get('spexi_order_id') or order['id']}", headers=self._h())
