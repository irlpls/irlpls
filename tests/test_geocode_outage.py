"""A geocoder outage (Nominatim answers 429, or the network is down) must read as "unknown place", never a traceback,
and must not be cached as "no such place"."""
import urllib.error

from irlpls import geo, http


def test_rate_limited_geocoder_returns_none_and_is_not_cached(monkeypatch):
    geo._cache.pop("atlantis-outage-test", None)

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://nominatim.openstreetmap.org/search", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(http, "get", boom)
    assert geo.geocode("Atlantis-outage-test") is None
    assert "atlantis-outage-test" not in geo._cache
    monkeypatch.setattr(http, "get", lambda *a, **k: [])
    assert geo.geocode("Atlantis-outage-test") is None
    assert "atlantis-outage-test" in geo._cache   # a real "not found" is remembered
    geo._cache.pop("atlantis-outage-test", None)
