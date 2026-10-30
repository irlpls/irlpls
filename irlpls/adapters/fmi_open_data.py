"""Finnish Meteorological Institute open data (WFS): the latest weather observations at the stations nearest a point.
Free, no key; CC BY 4.0.

Sources (public docs, read 2026-10-28):
- Open data manual: https://en.ilmatieteenlaitos.fi/open-data-manual
- WFS services and limits: https://en.ilmatieteenlaitos.fi/open-data-manual-fmi-wfs-services (600 requests per 5 min
  combined, 20,000 download requests per day).
- Stored query fmi::observations::weather::simple, described by
  https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=describeStoredQueries&storedquery_id=fmi::observations::weather::simple
  takes starttime, endtime, timestep (minutes), parameters, bbox (lon,lat,lon,lat), place, fmisid, maxlocations, crs.
  The "simple" response is GML: one BsWfs:BsWfsElement per station, time and parameter, with gml:pos "lat lon",
  BsWfs:Time (UTC), ParameterName and ParameterValue ("NaN" when missing). Errors are an OWS ExceptionReport (HTTP 400).
The mareograph (sea level) query takes no place or bbox, only fmisid, so it is not wired here.
"""
from datetime import datetime, timedelta, timezone
import urllib.parse
import xml.etree.ElementTree as ET

from .. import geo, http
from .base import Adapter, Results, observation, prepared_call

WFS = "https://opendata.fmi.fi/wfs"
DOCS = "https://en.ilmatieteenlaitos.fi/open-data-manual"
QUERY = "fmi::observations::weather::simple"
# parameter -> (label, unit). Names and units per FMI's observation parameter list (checked against a live response).
PARAMS = {"t2m": ("air temperature", "degC"), "ws_10min": ("wind speed (10 min mean)", "m/s"),
          "wg_10min": ("wind gust (10 min max)", "m/s"), "wd_10min": ("wind direction (from)", "deg"),
          "rh": ("relative humidity", "%"), "p_sea": ("sea-level pressure", "hPa"), "r_1h": ("precipitation (1 h)", "mm")}
LOOKBACK_MIN = 30
NS = {"wfs": "http://www.opengis.net/wfs/2.0", "gml": "http://www.opengis.net/gml/3.2",
      "BsWfs": "http://xml.fmi.fi/schema/wfs/2.0", "ows": "http://www.opengis.net/ows/1.1"}


def _since(now=None, minutes=LOOKBACK_MIN):
    t = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
    return t.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _params(lat, lon, radius_km, start, parameters=tuple(PARAMS)):
    w, s, e, n = geo.bbox(lat, lon, radius_km)
    return {"service": "WFS", "version": "2.0.0", "request": "getFeature", "storedquery_id": QUERY,
            "bbox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "parameters": ",".join(parameters), "starttime": start,
            "timestep": 10}


def _parse_xml(text):
    if "<!DOCTYPE" in text or "<!ENTITY" in text:   # never expand entities from a remote document
        raise ValueError("FMI response has a DTD; refused")
    return ET.fromstring(text.encode())


def exception_text(text):
    """The ExceptionText lines of an OWS ExceptionReport (FMI's error body), or None if it isn't one."""
    try:
        root = _parse_xml(text or "")
    except (ET.ParseError, ValueError):
        return None
    if not root.tag.endswith("ExceptionReport"):
        return None
    return "; ".join(t.text.strip() for t in root.iter(f"{{{NS['ows']}}}ExceptionText") if t.text and "URI:" not in t.text) or None


def parse_simple(text):
    """Latest non-missing value per (station position, parameter) from a 'simple' WFS response:
    {(lat, lon): {param: (time, value)}}."""
    root = _parse_xml(text)
    if root.tag.endswith("ExceptionReport"):
        raise ValueError(f"FMI exception: {exception_text(text)}")
    out = {}
    for el in root.iter(f"{{{NS['BsWfs']}}}BsWfsElement"):
        pos = el.findtext("BsWfs:Location/gml:Point/gml:pos", namespaces=NS)
        t, name = el.findtext("BsWfs:Time", namespaces=NS), el.findtext("BsWfs:ParameterName", namespaces=NS)
        raw = (el.findtext("BsWfs:ParameterValue", namespaces=NS) or "").strip()
        try:
            lat, lon = (float(x) for x in (pos or "").split()[:2])
            v = float(raw)
        except ValueError:
            continue
        if v != v or not name or not t:   # NaN = missing
            continue
        cur = out.setdefault((lat, lon), {})
        if name not in cur or t.strip() > cur[name][0]:
            cur[name] = (t.strip(), v)
    return out


class FMIOpenData(Adapter):
    card_id = "fmi-open-data"
    can_live = True

    def prepare(self, req, route, radius_km=25):
        if route != "live":
            return None
        if req.get("lat") is not None and req.get("lon") is not None:
            p = _params(req["lat"], req["lon"], radius_km, _since())
        elif req.get("place"):   # the stored query also takes a place name (Finnish places)
            p = {k: v for k, v in _params(0, 0, 1, _since()).items() if k != "bbox"}
            p["place"] = req["place"]
        else:
            return None
        return prepared_call(self.card_id, "read", "GET", f"{WFS}?{urllib.parse.urlencode(p)}", headers={"Accept": "application/xml"},
                             docs_url=DOCS,
                             notes="GML (XML). Each wfs:member/BsWfs:BsWfsElement has gml:pos (\"lat lon\"), BsWfs:Time (UTC), "
                                   "ParameterName and ParameterValue (NaN = missing); keep the newest value per station and "
                                   "parameter. Units: t2m degC, ws_10min/wg_10min m/s, wd_10min deg, rh %, p_sea hPa, r_1h mm. "
                                   "Limit: 600 requests per 5 minutes.")

    def live(self, lat, lon, radius_km=25, limit=2, parameters=tuple(PARAMS), **kw):
        text = http.get(WFS, params=_params(lat, lon, radius_km, _since(), parameters), raw=True,
                        headers={"Accept": "application/xml"})
        stations = parse_simple(text)
        dist = {p: geo.distance_km(lat, lon, *p) for p in stations}
        out = []
        for p in sorted((p for p in stations if dist[p] <= radius_km), key=dist.get)[:limit]:
            for name, (t, v) in stations[p].items():
                label, unit = PARAMS.get(name, (name, None))
                out.append(observation(self.card_id, "sensor", t, p[0], p[1], value=v, unit=unit, label=label,
                                       parameter=name, distance_km=round(dist[p], 1)))
        return Results(out, [])
