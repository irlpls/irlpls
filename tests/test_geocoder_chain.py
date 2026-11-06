"""Geocoder fallback chain: caches, Nominatim, Photon, bundled gazetteer. No real network."""
import json
import urllib.error

import pytest

from irlpls import geo, http

NOM = [{"lat": "48.85", "lon": "2.35", "display_name": "Paris, France",
        "address": {"country_code": "fr", "state": "Ile-de-France", "ISO3166-2-lvl4": "FR-IDF"}}]
PHOTON = {"features": [{"geometry": {"coordinates": [2.35, 48.85]},
                        "properties": {"name": "Paris", "country": "France", "countrycode": "FR", "state": "Ile-de-France"}}]}
KEYS = {"lat", "lon", "name", "country_code", "state", "subdivision", "subdivisions"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("IRLPLS_HOME", str(tmp_path))
    geo._cache.clear()
    yield tmp_path
    geo._cache.clear()


def fake(monkeypatch, nominatim, photon):
    calls = []

    def get(url, **kw):
        calls.append(url)
        v = photon if "photon" in url else nominatim
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(http, "get", get)
    return calls


def e429():
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)


def test_429_then_photon(monkeypatch):
    calls = fake(monkeypatch, e429(), PHOTON)
    g = geo.geocode("Zzzville Nowhere")
    assert g["country_code"] == "fr" and "approximate" not in g and set(g) == KEYS
    assert sum("nominatim" in c for c in calls) == 1   # no hammering: one request


def test_offline_hit_is_marked_approximate_and_not_cached(monkeypatch, env):
    fake(monkeypatch, e429(), OSError("down"))
    g = geo.geocode("Munich")
    assert g["approximate"] is True and g["country_code"] == "de" and KEYS <= set(g)
    assert "munich" not in geo._cache and not (env / "geocode-cache.json").exists()


def test_no_hit_and_outage_not_cached(monkeypatch):
    fake(monkeypatch, e429(), OSError("down"))
    assert geo.geocode("Qzxv Plugh") is None
    assert "qzxv plugh" not in geo._cache


def test_real_not_found_is_cached(monkeypatch):
    fake(monkeypatch, [], {"features": []})
    assert geo.geocode("Qzxv Plugh") is None
    assert "qzxv plugh" in geo._cache


def test_disk_cache_write_and_read(monkeypatch, env):
    fake(monkeypatch, NOM, OSError("x"))
    assert geo.geocode("Paris")["name"] == "Paris, France"
    assert json.loads((env / "geocode-cache.json").read_text())["paris"]["lat"] == 48.85
    geo._cache.clear()
    calls = fake(monkeypatch, e429(), e429())   # next process: services down, disk answers
    g = geo.geocode("Paris")
    assert g["subdivision"] == "FR-IDF" and not calls and "approximate" not in g
