"""USGS earthquakes (ComCat) through the FDSN Event Web Service: recent events near a point. Free, no key.

Docs: https://earthquake.usgs.gov/fdsnws/event/1/ (parameters: format=geojson, latitude, longitude, maxradiuskm,
starttime, orderby, limit, minmagnitude; >20,000 matching events -> HTTP 400, so time, radius and limit are always bound).
GeoJSON fields: https://earthquake.usgs.gov/data/comcat/data-eventterms.php (time and updated are epoch milliseconds;
status automatic|reviewed|deleted). Feeds (recommended for whole-world polling, not used here):
https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
"""
from .. import http
from . import _fdsn
from .base import Adapter, Results, observation, prepared_call

BASE = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DOCS = "https://earthquake.usgs.gov/fdsnws/event/1/"


class USGSEarthquakes(Adapter):
    card_id = "usgs-earthquakes"
    can_live = True

    def _params(self, lat, lon, radius_km, limit, days, now=None, minmag=None):
        return _fdsn.query_params(lat, lon, radius_km, limit, days, "geojson", radius_in_km=True, now=now, minmag=minmag)

    def prepare(self, req, route, radius_km=300, limit=10, days=7, now=None):
        """The query live() sends: events within radius_km of the point in the last `days`, newest first."""
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        return prepared_call(self.card_id, "read", "GET", _fdsn.url_with(BASE, self._params(req["lat"], req["lon"], radius_km, limit, days, now)),
                             docs_url=DOCS,
                             notes="GeoJSON FeatureCollection; properties.time and .updated are epoch milliseconds (UTC), "
                                   "geometry is [lon, lat, depth_km], properties.status is automatic or reviewed. "
                                   "An empty features list means no event matched. Keep time and radius bounded "
                                   "(over 20,000 matches returns HTTP 400).")

    def live(self, lat, lon, radius_km=300, limit=10, days=7, minmag=None, **kw):
        def run():
            doc = http.get(BASE, params=self._params(lat, lon, radius_km, limit, days, minmag=minmag))
            return Results([self._obs(lat, lon, f) for f in _fdsn.features(doc)][:limit])
        return _fdsn.guarded(self.card_id, run)

    def _obs(self, lat, lon, f):
        p = f.get("properties") or {}
        c = f["geometry"]["coordinates"]
        mag = p.get("mag")
        return observation(self.card_id, "sensor", _fdsn.iso_ms(p.get("time")), c[1], c[0], value=mag, unit=p.get("magType"),
                           label=p.get("title") or (f"M {mag} - {p.get('place')}" if mag is not None else p.get("place")),
                           result_time=_fdsn.iso_ms(p.get("updated")), url=p.get("url"), event_id=f.get("id"),
                           place=p.get("place"), depth_km=c[2] if len(c) > 2 else None, review_status=p.get("status"),
                           event_type=p.get("type"), tsunami_flag=p.get("tsunami"), pager_alert=p.get("alert"),
                           felt_reports=p.get("felt"), network=p.get("net"), distance_km=_fdsn.distance(lat, lon, f))
