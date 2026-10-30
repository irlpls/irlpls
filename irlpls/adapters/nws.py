"""US National Weather Service (api.weather.gov). Free, no key; a User-Agent is required.

Docs: https://www.weather.gov/documentation/services-web-api and the OpenAPI spec https://api.weather.gov/openapi.json
(GET /points/{lat},{lon} -> properties.observationStations; GET /stations/{id}/observations/latest)."""
import os

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call

BASE = "https://api.weather.gov"
DOCS = "https://www.weather.gov/documentation/services-web-api"


def _h():
    # NWS asks for a User-Agent that identifies you; set NWS_USER_AGENT to your app and contact.
    h = {"Accept": "application/geo+json"}
    if os.environ.get("NWS_USER_AGENT"):
        h["User-Agent"] = os.environ["NWS_USER_AGENT"]
    return h
FIELDS = [("temperature", "temperature"), ("dewpoint", "dewpoint"), ("windSpeed", "wind speed"), ("windGust", "wind gust"),
          ("windDirection", "wind direction"), ("barometricPressure", "pressure"), ("visibility", "visibility"),
          ("relativeHumidity", "relative humidity"), ("precipitationLastHour", "precipitation, last hour")]


def _unit(code):
    return (code or "").split(":")[-1].replace("degC", "°C").replace("km_h-1", "km/h").replace("percent", "%").replace("degree_(angle)", "°")


class NWS(Adapter):
    card_id = "nws"
    can_live = True

    def prepare(self, req, route, operation=None):
        """Step 1 of live(): the grid point, which names its observation stations. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(
            self.card_id, "read", "GET", f"{BASE}/points/{req['lat']:.4f},{req['lon']:.4f}",
            headers={"Accept": "application/geo+json", "User-Agent": "{NWS_USER_AGENT}"}, docs_url=DOCS,
            placeholders=[{"name": "NWS_USER_AGENT", "what": "a User-Agent naming your app and a contact (website or email)",
                           "where": "you choose it; NWS requires one (not a secret)"}],
            notes="No key. Then GET the URL in properties.observationStations, and for the nearest station "
                  "GET /stations/{stationIdentifier}/observations/latest. US points only.")

    def stations(self, lat, lon, limit=3):
        pt = http.get(f"{BASE}/points/{lat:.4f},{lon:.4f}", headers=_h())
        st = http.get(pt["properties"]["observationStations"], headers=_h())
        return st["features"][:limit]

    def live(self, lat, lon, radius_km=50, limit=1, **kw):
        out = []
        for s in self.stations(lat, lon, limit=max(limit, 1)):
            sid = s["properties"]["stationIdentifier"]
            slon, slat = s["geometry"]["coordinates"][:2]
            d = geo.distance_km(lat, lon, slat, slon)
            if d > radius_km:
                continue
            ob = http.get(f"{BASE}/stations/{sid}/observations/latest", headers=_h())["properties"]
            for key, label in FIELDS:
                v = ob.get(key) or {}
                if v.get("value") is None:
                    continue
                out.append(observation("nws", "sensor", ob["timestamp"], slat, slon, value=round(v["value"], 2), unit=_unit(v.get("unitCode")),
                                       label=label, station=f'{sid} {s["properties"]["name"]}', distance_km=round(d, 1),
                                       qc=v.get("qualityControl"), url=ob.get("@id")))
            if ob.get("textDescription"):
                out.append(observation("nws", "sensor", ob["timestamp"], slat, slon, value=ob["textDescription"], label="conditions",
                                       station=f'{sid} {s["properties"]["name"]}', distance_km=round(d, 1)))
        return out
