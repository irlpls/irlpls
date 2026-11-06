import pytest


@pytest.fixture(autouse=True)
def _isolated_geocode_disk_cache(tmp_path_factory, monkeypatch):
    """Mocked geocoder answers must never land in the real ~/.irlpls/geocode-cache.json."""
    monkeypatch.setenv("IRLPLS_HOME", str(tmp_path_factory.mktemp("irlpls_home")))
