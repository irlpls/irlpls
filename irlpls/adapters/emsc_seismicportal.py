"""EMSC seismic portal (European-Mediterranean Seismological Centre) through the FDSN Event Web Service. Free, no key.

Docs: https://www.seismicportal.eu/fdsn-wsevent.html (format=json gives GeoJSON; latitude, longitude, maxradius in
degrees, starttime, orderby, limit max 20,000; no events -> HTTP 204). Event properties seen live: unid, time (ISO),
lastupdate, lat, lon, depth, mag, magtype, flynn_region, auth, evtype, source_catalog. Event page:
https://www.seismicportal.eu/eventdetails.html?unid=<unid>. The site's terms bar bulk copying, so every query here
is filtered by place, time and limit.
"""
from .. import http
from . import _fdsn
from .base import Adapter, Results, observation, prepared_call

BASE = "https://www.seismicportal.eu/fdsnws/event/1/query"
DOCS = "https://www.seismicportal.eu/fdsn-wsevent.html"
EVENT_PAGE = "https://www.seismicportal.eu/eventdetails.html?unid="


class EMSCSeismicPortal(Adapter):
    card_id = "emsc-seismicportal"
    can_live = True

    def _params(self, lat, lon, radius_km, limit, days, now=None, minmag=None):
        return _fdsn.query_params(lat, lon, radius_km, limit, days, "json", radius_in_km=False, now=now, minmag=minmag)

    def prepare(self, req, route, radius_km=300, limit=10, days=7, now=None):
        """The query live() sends: events within radius_km (sent as degrees) in the last `days`, newest first."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(self.card_id, "read", "GET", _fdsn.url_with(BASE, self._params(req["lat"], req["lon"], radius_km, limit, days, now)),
                             docs_url=DOCS,
                             notes="GeoJSON FeatureCollection; properties.time and .lastupdate are ISO 8601 UTC, depth is "
                                   "in km, maxradius is in degrees (1 degree = 111.2 km). HTTP 204 (no body) means no "
                                   "event matched. Filtered queries only: the terms bar bulk copying.")

    def live(self, lat, lon, radius_km=300, limit=10, days=7, minmag=None, **kw):
        def run():
            doc = http.get(BASE, params=self._params(lat, lon, radius_km, limit, days, minmag=minmag))
            return Results([self._obs(lat, lon, f) for f in _fdsn.features(doc)][:limit])
        return _fdsn.guarded(self.card_id, run)

    def _obs(self, lat, lon, f):
        p = f.get("properties") or {}
        c = f["geometry"]["coordinates"]
        mag, region, unid = p.get("mag"), p.get("flynn_region"), p.get("unid") or f.get("id")
        label = f"M {mag} - {region}" if mag is not None and region else (region or None)
        return observation(self.card_id, "sensor", p.get("time"), c[1], c[0], value=mag, unit=p.get("magtype"), label=label,
                           result_time=p.get("lastupdate"), url=(EVENT_PAGE + unid) if unid else None, event_id=unid,
                           place=region, depth_km=p.get("depth"), author=p.get("auth"), event_type=p.get("evtype"),
                           catalog=p.get("source_catalog"), distance_km=_fdsn.distance(lat, lon, f))
