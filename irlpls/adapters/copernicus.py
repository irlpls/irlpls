"""Copernicus Data Space Ecosystem: the newest Sentinel-2 (optical) and Sentinel-1 (radar) products over a place.
Search is free and keyless (STAC); downloading full products needs a free account.

Docs: https://documentation.dataspace.copernicus.eu/APIs.html (STAC at https://stac.opensearch.dataspace.copernicus.eu/v1;
the old stac.dataspace.copernicus.eu/v1 is replaced on 2027-01-12, and keyless search is 60 requests/min per IP:
https://dataspace.copernicus.eu/news/2026-10-16-new-sfeos-stac-catalogue-endpoint). Always send sortby: the new default is relevance."""

from .. import geo, http
from .base import Adapter, observation, pick_operation, prepared_call, query

STAC = "https://stac.opensearch.dataspace.copernicus.eu/v1/search"
COLLECTIONS = [("sentinel-2-l2a", "Sentinel-2 optical scene"), ("sentinel-1-grd", "Sentinel-1 radar scene")]


class Copernicus(Adapter):
    card_id = "copernicus-sentinel"
    can_live = True
    readiness_note = "No key to search; downloading full products needs a free Copernicus account."

    def prepare(self, req, route, operation=None, radius_km=5, limit=1):
        """The newest Sentinel-2 scene over the point (STAC search). Free and keyless; safe without approval."""
        if not pick_operation(route, operation, "live", ("read",), "read") or req.get("lat") is None or req.get("lon") is None:
            return None
        w, s, e, n = geo.bbox(req["lat"], req["lon"], radius_km)
        q = query({"collections": COLLECTIONS[0][0], "bbox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "sortby": "-datetime", "limit": limit})
        return prepared_call(
            self.card_id, "read", "GET", f"{STAC}?{q}", docs_url="https://documentation.dataspace.copernicus.eu/APIs.html",
            notes="No key for search. Repeat with collections=sentinel-1-grd for radar. features[] carry properties.datetime, "
                  "eo:cloud_cover and a self link; downloading full products needs a free CDSE account.")

    def live(self, lat, lon, radius_km=5, limit=1, **kw):
        w, s, e, n = geo.bbox(lat, lon, radius_km)
        out = []
        for coll, label in COLLECTIONS:
            r = http.get(STAC, params={"collections": coll, "bbox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "sortby": "-datetime", "limit": limit})
            for f in (r or {}).get("features", []):
                p = f.get("properties") or {}
                self_url = next((l["href"] for l in f.get("links", []) if l.get("rel") == "self"), None)
                extra = {"cloud_cover_pct": p["eo:cloud_cover"]} if p.get("eo:cloud_cover") is not None else {}
                out.append(observation("copernicus-sentinel", "image", p.get("datetime"), lat, lon, label=label, url=self_url,
                                       result_time=p.get("published"), product=f.get("id"), **extra))
        return out
