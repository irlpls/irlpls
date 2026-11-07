import pytest


@pytest.fixture(autouse=True)
def _isolated_geocode_disk_cache(tmp_path_factory, monkeypatch):
    """Mocked geocoder answers must never land in the real ~/.irlpls/geocode-cache.json,
    and jobs/orders must never land in the real ~/.irlpls (broker.HOME reads IRLPLS_HOME lazily)."""
    monkeypatch.setenv("IRLPLS_HOME", str(tmp_path_factory.mktemp("irlpls_home")))


@pytest.fixture(autouse=True)
def _no_real_geocoder(request, monkeypatch):
    """Tests never call the public Nominatim/Photon services; those hosts fail as if offline, so geo falls back to
    the bundled gazetteer. A test that patches http.get itself (test_geocoder_chain) overrides this;
    mark a test @pytest.mark.network to opt out."""
    if request.node.get_closest_marker("network"):
        return
    from irlpls import http
    real = http.get

    def get(url, *a, **kw):
        if "nominatim" in str(url) or "photon" in str(url):
            raise OSError("real geocoder blocked in tests")
        return real(url, *a, **kw)
    monkeypatch.setattr(http, "get", get)


def pytest_configure(config):
    config.addinivalue_line("markers", "network: test may use the real network")
