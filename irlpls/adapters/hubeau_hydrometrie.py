"""Hub'Eau hydrométrie (France): the latest real-time water height and flow at the gauges nearest a point. Free, no key;
Licence Ouverte / Etalab.

Sources (public docs, read 2026-10-28):
- API page: https://hubeau.eaufrance.fr/page/api-hydrometrie ("mm pour les hauteurs d'eau ... l/s pour les débits";
  dates in UTC; updated from PHyC every 5 minutes; result depth capped at 20,000 records).
- Swagger: https://hubeau.eaufrance.fr/api/v2/hydrometrie/api-docs. /observations_tr takes latitude, longitude and
  distance (km), date_debut_obs (at most one month back), grandeur_hydro (H, Q), sort (desc by default), size, cursor;
  /referentiel/stations takes code_station (comma list), size. Partial pages answer HTTP 206 with a `next` link.
Use /api/v2: the card saw a 403 on /api/v1 with a custom User-Agent.
"""
from datetime import datetime, timedelta, timezone
import urllib.parse

from .. import geo, http, ratelimit
from .base import Adapter, Results, observation, prepared_call, warning

BASE = "https://hubeau.eaufrance.fr/api/v2/hydrometrie"
DOCS = "https://hubeau.eaufrance.fr/page/api-hydrometrie"
# grandeur_hydro -> (label, unit after conversion, divisor from the documented raw unit)
GRANDEUR = {"H": ("water level", "m", 1000.0, "mm"), "Q": ("flow", "m3/s", 1000.0, "l/s")}
LOOKBACK_MIN = 120
PAGE = 1000


def _since(now=None, minutes=LOOKBACK_MIN):
    return ((now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _obs_params(lat, lon, radius_km, since):
    return {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "distance": radius_km, "date_debut_obs": since,
            "sort": "desc", "size": PAGE, "format": "json"}


def series_code(r):
    """A series belongs to a station (code_station) or, for flows computed for a whole site, to the site (code_station
    is null and code_site is set, as in live responses)."""
    return r.get("code_station") or r.get("code_site")


def latest_per_series(rows):
    """Newest observation per (station or site code, grandeur_hydro) from observations_tr `data` rows."""
    best = {}
    for r in rows or []:
        if r.get("resultat_obs") is None or r.get("grandeur_hydro") not in GRANDEUR or not series_code(r):
            continue
        k = (series_code(r), r["grandeur_hydro"])
        if k not in best or (r.get("date_obs") or "") > (best[k].get("date_obs") or ""):
            best[k] = r
    return best


def _warn(card_id, what, err):
    ra = getattr(err, "retry_after", None)
    if ra is None:
        ra = ratelimit.retry_after(getattr(err, "headers", None))
    code = {401: "refused", 403: "refused", 404: "not_found", 429: "rate_limited"}.get(err.status, "provider_error")
    return warning(card_id, code, f"{what}: HTTP {err.status}", status=err.status,
                   **({"retry_after_s": int(ra)} if ra is not None else {}))


STATUS_EN = {"Brute": "raw (not yet checked)", "Pré-validée": "pre-validated", "Validée": "validated",
             "Non qualifiée": "not qualified"}


class HubEauHydrometrie(Adapter):
    card_id = "hubeau-hydrometrie"
    can_live = True

    def prepare(self, req, route, radius_km=10):
        if route != "live" or req.get("lat") is None or req.get("lon") is None:
            return None
        q = urllib.parse.urlencode(_obs_params(req["lat"], req["lon"], radius_km, _since()))
        return prepared_call(self.card_id, "read", "GET", f"{BASE}/observations_tr?{q}", docs_url=DOCS,
                             notes="JSON {count, next, data: [...]}, newest first. Keep the newest row per code_station and "
                                   "grandeur_hydro (H = height in mm, Q = flow in l/s; divide by 1000 for m and m3/s). HTTP 206 "
                                   "means more pages (follow `next`). Station names: GET /referentiel/stations?code_station=<codes>. "
                                   "date_debut_obs may be at most one month back.")

    def live(self, lat, lon, radius_km=10, limit=3, names=True, **kw):
        resp = http.get(f"{BASE}/observations_tr", params=_obs_params(lat, lon, radius_km, _since())) or {}
        best = latest_per_series(resp.get("data"))
        dist = {}
        for (code, _), r in best.items():
            if r.get("latitude") is not None and r.get("longitude") is not None:
                dist[code] = geo.distance_km(lat, lon, r["latitude"], r["longitude"])
        keep = sorted(dist, key=dist.get)[:limit]
        warns, info = [], {}
        is_site = {code: not best.get((code, "H"), best.get((code, "Q")) or {}).get("code_station") for code in keep}
        if names and keep:
            for path, field, label_field, codes in (
                    ("stations", "code_station", "libelle_station", [c for c in keep if not is_site[c]]),
                    ("sites", "code_site", "libelle_site", [c for c in keep if is_site[c]])):
                if not codes:
                    continue
                try:
                    got = http.get(f"{BASE}/referentiel/{path}", params={field: ",".join(codes), "size": len(codes),
                                                                         "format": "json"}) or {}
                    info.update({x.get(field): {"name": x.get(label_field), "river": x.get("libelle_cours_eau")}
                                 for x in got.get("data") or []})
                except http.HTTPError as err:
                    warns.append(_warn(self.card_id, f"{path[:-1]} names unavailable", err))
        out = []
        for code in keep:
            s = info.get(code, {})
            for g in ("H", "Q"):
                r = best.get((code, g))
                if not r:
                    continue
                label, unit, div, raw_unit = GRANDEUR[g]
                out.append(observation(self.card_id, "sensor", r.get("date_obs"), r["latitude"], r["longitude"],
                                       value=round(r["resultat_obs"] / div, 4), unit=unit, label=label,
                                       url=f"{BASE}/observations_tr?code_entite={code}&grandeur_hydro={g}&size=20",
                                       station=s.get("name") or code, **{"site_code" if is_site[code] else "station_code": code},
                                       river=s.get("river"), raw_value=r["resultat_obs"], raw_unit=raw_unit,
                                       data_status=r.get("libelle_statut"), qualification=r.get("libelle_qualification_obs"),
                                       status=STATUS_EN.get(r.get("libelle_statut"), r.get("libelle_statut")),
                                       distance_km=round(dist[code], 1)))
        # one gauge is often listed under two codes (a station and its backup, or a station and its site) with the
        # same reading: keep the first of each
        seen, kept = set(), []
        for o in out:   # nearest first, so the nearest code keeps the reading
            k = (o.get("label"), o.get("value"), o.get("phenomenon_time"))
            if k not in seen:
                seen.add(k)
                kept.append(o)
        return Results(kept, warns)
