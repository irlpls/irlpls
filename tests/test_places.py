"""The browser world gazetteer (src/places.js + src/places.json, GeoNames CC BY 4.0): a typed place is put on the map in
the browser, never sent anywhere. Runs places.js in a real browser against the real places.json."""
import json
import pathlib

import pytest

pw = pytest.importorskip("playwright.sync_api")
SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto("about:blank")
        pg.add_script_tag(content=(SRC / "places.js").read_text())
        pg.evaluate("d => IRLPlaces.use(d)", json.loads((SRC / "places.json").read_text()))
        yield pg
        b.close()


def find(pg, text):
    return pg.evaluate("t => IRLPlaces.find(t)", text)


@pytest.mark.parametrize("text,cc,name", [
    ("Munich", "de", "Munich"), ("Lyon", "fr", "Lyon"), ("Osaka", "jp", "Osaka"), ("Nairobi", "ke", "Nairobi"),
    ("São Paulo", "br", "São Paulo"), ("Sao Paulo", "br", "São Paulo"), ("Munich, Germany", "de", "Munich"),
    ("Springfield, Illinois", "us", "Springfield, IL"), ("air quality in munich", "de", "Munich"),
    ("Is the water nice in Paris", "fr", "Paris"), ("Portland, Maine", "us", "Portland, ME"), ("Cambridge, MA", "us", "Cambridge, MA"),
    ("Rhine level at Cologne?", "de", "Cologne"), ("Mobile, Alabama", "us", "Mobile, AL"), ("river level in Reading", "gb", "Reading"),
])
def test_finds(page, text, cc, name):
    r = find(page, text)
    assert r and r["cc"] == cc and r["name"] == name, (text, r)


def test_munich_coordinates(page):
    r = find(page, "Air quality in Munich?")
    assert abs(r["lat"] - 48.14) < 0.05 and abs(r["lon"] - 11.58) < 0.05


@pytest.mark.parametrize("text", ["reading list", "Reading list", "is it nice out", "air quality"])
def test_common_words_are_not_places(page, text):
    assert find(page, text) is None


def test_country(page):
    assert page.evaluate("IRLPlaces.country('Munich, Germany')") == "de"
    assert page.evaluate("IRLPlaces.country('what is in it')") is None


def test_small_and_credited():
    assert (SRC / "places.json").stat().st_size < 600 * 1024
    d = json.loads((SRC / "places.json").read_text())
    assert "GeoNames" in d["source"] and "CC BY 4.0" in d["source"] and d["date"]
    assert "GeoNames" in (SRC / "media" / "CREDITS.md").read_text()
