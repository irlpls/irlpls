"""Umbra: task a synthetic-aperture radar satellite (sees through cloud and at night). Bearer token in UMBRA_API_KEY.
List prices from $675 per 5x5 km scene at 1 m; data typically 1-4 h after the capture window closes.

Docs: https://docs.canopy.umbra.space (POST /tasking/feasibilities, POST /tasking/tasks, GET /tasking/tasks/{id};
Authorization: Bearer). Feasibility, not price: Umbra publishes no pricing call."""
from datetime import datetime, timedelta, timezone

from .. import http
from .base import Adapter, observation, pick_operation, prepared_call

BASE = "https://api.canopy.umbra.space"


class Umbra(Adapter):
    card_id = "umbra"
    env = "UMBRA_API_KEY"
    how_to_get = "Copy an access token from https://canopy.umbra.space/account, or use machine client credentials."
    can_commission = True

    def _h(self, required=True):
        return {"Authorization": f"Bearer {self.key(required)}"}

    @staticmethod
    def _parts(spec):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        window = {"windowStartAt": start.isoformat(), "windowEndAt": (start + timedelta(days=spec.get("days", 7))).isoformat()}
        constraints = {"collectionType": "SPOTLIGHT", "geometry": {"type": "Point", "coordinates": [spec["lon"], spec["lat"]]},
                       "rangeResolutionMinMeters": spec.get("resolution_m", 1)}
        return window, constraints, {"taskName": spec.get("name", "irlpls tasking"), "constraints": constraints, **window}

    def prepare(self, req, route, operation=None):
        """quote: POST /tasking/feasibilities (default; feasibility, not a price); order: POST /tasking/tasks; status: one task."""
        op = pick_operation(route, operation, "commission", ("quote", "order", "status"), "quote")
        if not op:
            return None
        h = {"Authorization": "Bearer {" + self.env + "}"}
        auth = dict(auth_kind="bearer", key_name=self.env, key_in="header Authorization: Bearer",
                    get_key_url="https://canopy.umbra.space/account", docs_url="https://docs.canopy.umbra.space")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/tasking/tasks/{{TASK_ID}}", headers=h, **auth,
                                 placeholders=[{"name": "TASK_ID", "what": "the task id POST /tasking/tasks returned", "where": "its response"}],
                                 notes="Terminal statuses DELIVERED, REJECTED, CANCELED, ERROR, ANOMALY; collectIds lead to the "
                                       "STAC v2 items with signed download URLs.")
        if req.get("lat") is None or req.get("lon") is None:
            return None
        mins = req.get("within_minutes")
        days = max(1, -(-int(mins) // 1440)) if isinstance(mins, (int, float)) and mins > 0 else 7
        window, constraints, body = self._parts({"lat": req["lat"], "lon": req["lon"], "days": days})
        h = dict(h, **{"Content-Type": "application/json"})
        tok = " UMBRA_API_KEY is an access token (24 h) from the account page or the client-credentials grant."
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/tasking/feasibilities", headers=h,
                                 body={"constraints": constraints, **window}, **auth,
                                 notes="Feasibility, not a price, and free: poll GET /tasking/feasibilities/{id} until COMPLETED "
                                       "for opportunities (kept ~5 minutes). List price from $675 per 5x5 km scene at 1 m." + tok)
        return prepared_call(self.card_id, "order", "POST", f"{BASE}/tasking/tasks", headers=h, body=body, **auth,
                             notes="Creates a billable SAR capture. Needs the person's approval. Cancel with PATCH "
                                   "/tasking/tasks/{id}/cancel while SCHEDULED, SUBMITTED, ACCEPTED or ACTIVE." + tok)

    def commission(self, spec, confirm=False):
        """Spotlight collect over a point. spec: lat, lon, days (window), resolution_m (0.25-2, default 1).
        Without confirm, runs a feasibility check (when a key is set) and returns the task it would create."""
        window, constraints, body = self._parts(spec)
        if not confirm:
            out = self._dry("POST", f"{BASE}/tasking/tasks", body, self._h(required=False))
            if self.configured():
                out["feasibility"] = http.post(f"{BASE}/tasking/feasibilities", body={"constraints": constraints, **window}, headers=self._h())
            return out
        return http.post(f"{BASE}/tasking/tasks", body=body, headers=self._h())

    def status(self, order):
        return http.get(f"{BASE}/tasking/tasks/{order['id']}", headers=self._h())

    # GET /tasking/tasks/{id}: RECEIVED ... SCHEDULED, TASKED, PROCESSING, DELIVERING; terminal DELIVERED, REJECTED,
    # CANCELED, ERROR, ANOMALY. Statuses between RECEIVED and SCHEDULED are not listed, so they stay unmapped.
    status_map = {**{s: "in_flight" for s in ("received", "scheduled", "tasked", "processing", "delivering")},
                  "delivered": "delivered", **{s: "failed" for s in ("rejected", "canceled", "error", "anomaly")}}

    def deliverables(self, resp):
        return [observation(self.card_id, "sar-collect", None, None, None, label=f"collect {cid}", collect_id=cid)
                for cid in (resp or {}).get("collectIds") or []]
