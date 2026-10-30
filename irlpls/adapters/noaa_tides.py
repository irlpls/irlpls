"""NOAA CO-OPS tide and current stations: water level, wind, temperatures, pressure at US coastal stations.
Free, no key. Data are preliminary until NOAA verifies them (flag q = p / v).

Docs: https://api.tidesandcurrents.noaa.gov/api/prod/ (datagetter) and https://api.tidesandcurrents.noaa.gov/mdapi/prod/
(station metadata, stations.json?type=waterlevels)."""
from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

META = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
PRODUCTS = {"water_level": "water level", "wind": "wind speed", "air_temperature": "air temperature",
            "water_temperature": "water temperature", "air_pressure": "air pressure"}
_stations = None


def stations():
    global _stations
    if _stations is None:
        _stations = http.get(META, params={"type": "waterlevels"})["stations"]
    return _stations


class NOAATides(Adapter):
    card_id = "noaa-tides"
    can_live = True

    def prepare(self, req, route, operation=None):
        """Step 1 of live(): the water-level station list, to pick the nearest station. Free; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None:
            return None
        return prepared_call(
            self.card_id, "read", "GET", META + "?" + query({"type": "waterlevels"}),
            docs_url="https://api.tidesandcurrents.noaa.gov/mdapi/prod/",
            notes=f"No key. Pick the station nearest ({req['lat']:.4f}, {req['lon']:.4f}) from stations[] (lat, lng, id), then "
                  f"GET {DATA}?station={{id}}&product=water_level&date=latest&datum=MLLW&units=metric&time_zone=gmt"
                  "&format=json&application=<your app> (also wind, air_temperature, water_temperature, air_pressure). "
                  "Docs: https://api.tidesandcurrents.noaa.gov/api/prod/")

    def live(self, lat, lon, radius_km=40, limit=1, **kw):
        near = sorted(((geo.distance_km(lat, lon, s["lat"], s["lng"]), s) for s in stations()), key=lambda x: x[0])
        out = []
        for d, s in near[:limit]:
            if d > radius_km:
                break
            for prod, label in PRODUCTS.items():
                params = {"station": s["id"], "product": prod, "date": "latest", "units": "metric", "time_zone": "gmt",
                          "format": "json", "application": "irlpls"}
                if prod == "water_level":
                    params["datum"] = "MLLW"
                r = http.get(DATA, params=params)
                if "data" not in r or not r["data"]:
                    continue
                v = r["data"][0]
                val = v.get("s") if prod == "wind" else v.get("v")
                if val in (None, ""):
                    continue
                unit = {"water_level": "m above MLLW", "wind": "m/s", "air_pressure": "mb"}.get(prod, "°C")
                out.append(observation("noaa-tides", "sensor", v["t"].replace(" ", "T") + ":00Z", s["lat"], s["lng"], value=float(val),
                                       unit=unit, label=label, station=f'{s["id"]} {s["name"]}', distance_km=round(d, 1),
                                       quality={"p": "preliminary", "v": "verified"}.get(v.get("q"), v.get("q")), flags=v.get("f"),
                                       url=f'https://tidesandcurrents.noaa.gov/stationhome.html?id={s["id"]}'))
        return out
