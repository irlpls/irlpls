"""Sensor.Community: citizen particulate and temperature sensors worldwide, last 5 minutes. Free, no key.

Docs: https://github.com/opendata-stuttgart/meta/wiki/EN-APIs (GET /airrohr/v1/filter/area={lat},{lon},{km}; send a
User-Agent that identifies you)."""
from datetime import datetime

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call

KIND = {"P1": ("PM10", "µg/m³"), "P2": ("PM2.5", "µg/m³"), "temperature": ("temperature", "°C"), "humidity": ("relative humidity", "%")}
# Low-cost citizen sensors misreport; values outside what the instrument can physically measure are dropped, not passed on.
PLAUSIBLE = {"P1": (0, 1000), "P2": (0, 1000), "temperature": (-60, 60), "humidity": (0, 100)}


def _utc(ts):
    """'2026-10-27 02:38:01' (UTC, per the API) -> ISO 8601 with Z."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


class SensorCommunity(Adapter):
    card_id = "sensor-community"
    can_live = True

    def prepare(self, req, route, operation=None, radius_km=3):
        """Every measurement of the last 5 minutes within radius_km. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(
            self.card_id, "read", "GET", f"https://data.sensor.community/airrohr/v1/filter/area={req['lat']:.4f},{req['lon']:.4f},{radius_km:g}",
            headers={"User-Agent": "{YOUR_USER_AGENT}"}, docs_url="https://github.com/opendata-stuttgart/meta/wiki/EN-APIs",
            placeholders=[{"name": "YOUR_USER_AGENT", "what": "a User-Agent naming your app and how to contact you",
                           "where": "you choose it; the operators require one (not a secret)"}],
            notes="No key. Rows carry location {latitude, longitude, indoor}, timestamp (UTC, 'YYYY-MM-DD HH:MM:SS') and "
                  "sensordatavalues (P2 = PM2.5, P1 = PM10 in µg/m³, temperature, humidity). Skip indoor sensors; "
                  "low-cost sensors misreport, so drop impossible values.")

    def live(self, lat, lon, radius_km=3, limit=3, **kw):
        rows = http.get(f"https://data.sensor.community/airrohr/v1/filter/area={lat:.4f},{lon:.4f},{radius_km:g}") or []
        rows = [r for r in rows if not (r.get("location") or {}).get("indoor")]
        near = lambda r: geo.distance_km(lat, lon, float(r["location"]["latitude"]), float(r["location"]["longitude"]))
        out, seen = [], set()
        for r in sorted(rows, key=near):
            sid = (r.get("sensor") or {}).get("id")
            if sid in seen:
                continue
            seen.add(sid)
            slat, slon = float(r["location"]["latitude"]), float(r["location"]["longitude"])
            for v in r.get("sensordatavalues") or []:
                k = KIND.get(v.get("value_type"))
                if not k:
                    continue
                try:
                    val = float(v["value"])
                except (TypeError, ValueError):
                    continue
                lo, hi = PLAUSIBLE[v["value_type"]]
                if not lo <= val <= hi:
                    continue
                out.append(observation("sensor-community", "sensor", _utc(r.get("timestamp")), slat, slon, value=val, unit=k[1], label=k[0],
                                       station=f'sensor {sid} ({(r["sensor"].get("sensor_type") or {}).get("name", "")})',
                                       distance_km=round(near(r), 1)))
            if len(seen) >= limit:
                break
        return out
