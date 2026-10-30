import json
from datetime import date, timedelta

import pytest

from irlpls import conformance, validate

CARDS = __import__("pathlib").Path(__file__).resolve().parent.parent / "cards"
TODAY = date.today()


def make_card(**kw):
    card = json.loads((CARDS / "skyfi.json").read_text())
    card.update(status="provider-maintained", provider_url="https://skyfi.com",
                updated=TODAY.isoformat(), expires=(TODAY + timedelta(days=300)).isoformat())
    card.update(kw)
    return card


GOOD = {"content-type": "application/json", "cache-control": "public, max-age=300", "etag": '"a"'}


def fetcher(card=None, status=200, headers=None, ucp=None, calls=None):
    h = dict(GOOD if headers is None else headers)
    def f(url):
        if calls is not None:
            calls.append(url)
        if url.endswith("/ucp"):
            return 200, {}, json.dumps(ucp).encode()
        return status, h, json.dumps(card if card is not None else make_card()).encode()
    return f


def run(card=None, **kw):
    f = fetcher(card, **{k: kw.pop(k) for k in ("status", "headers", "ucp", "calls") if k in kw})
    return conformance.check("skyfi.com", fetcher=f, **kw)


def fails(res):
    return {n for s, n, _ in res if s == "fail"}


def test_good_card_passes():
    res = run()
    assert not fails(res), res
    assert any(n == "etag" and s == "pass" for s, n, _ in res)


def test_redirect_fails():
    res = run(status=301, headers={"location": "https://www.skyfi.com/x"})
    assert "no redirects" in fails(res)


def test_missing_cache_control_fails():
    assert "cache-control" in fails(run(headers={"content-type": "application/json"}))


def test_short_max_age_fails():
    assert "cache-control" in fails(run(headers={**GOOD, "cache-control": "public, max-age=30"}))


def test_wrong_content_type_fails():
    assert "content type" in fails(run(headers={**GOOD, "content-type": "text/html"}))


def test_expired_card_fails():
    old = make_card(updated=(TODAY - timedelta(days=100)).isoformat(), expires=(TODAY - timedelta(days=1)).isoformat())
    assert "expires" in fails(run(old))


def test_expiry_over_a_year_fails():
    assert "expires" in fails(run(make_card(expires=(TODAY + timedelta(days=500)).isoformat())))


def test_domain_mismatch_fails_for_provider_maintained():
    res = run(make_card(provider_url="https://other.example"))
    assert "provider_url" in fails(res)


def test_unclaimed_mismatch_only_warns():
    res = run(make_card(status="unclaimed", provider_url="https://other.example"))
    assert "provider_url" not in fails(res)
    assert any(n == "provider_url" and s == "warn" for s, n, _ in res)


def test_against_reports_overclaims():
    audited = make_card()
    audited["verification"]["time"] = {"level": "none", "unknown": "x"}
    card = make_card()
    card["verification"]["time"] = {"level": "claimed", "unknown": "x"}
    res = run(card, against=audited)
    assert any(n == "overclaim verification.time" and s == "warn" for s, n, _ in res)
    assert validate.overclaims(card, audited)


def test_only_one_get_without_ucp_and_api_never_called():
    calls = []
    card = make_card(api={"available": True, "docs_url": "https://skyfi.com/docs", "base_url": "https://api.skyfi.com"})
    run(card, calls=calls)
    assert calls == ["https://skyfi.com/.well-known/irlpls"]


def test_ucp_checks_capability_and_card_url():
    url = "https://skyfi.com/.well-known/irlpls"
    good = {"capabilities": [{"name": "com.irlpls.capture.card", "config": {"card": url}}]}
    bad = {"capabilities": [{"name": "com.irlpls.capture.card", "config": {"card": "https://x/y"}}]}
    assert not fails(conformance.check("skyfi.com", fetcher=fetcher(ucp=good), ucp=True))
    assert "ucp" in fails(conformance.check("skyfi.com", fetcher=fetcher(ucp=bad), ucp=True))


def test_main_exit_codes(capsys):
    assert conformance.main(["skyfi.com"], fetcher=fetcher()) == 0
    assert conformance.main(["skyfi.com"], fetcher=fetcher(headers={})) == 1
    assert "FAIL" in capsys.readouterr().out


def test_fetch_error_fails():
    def boom(url):
        raise OSError("no route")
    assert "fetch" in fails(conformance.check("skyfi.com", fetcher=boom))


def test_provider_url_must_be_the_serving_host_or_its_www_twin():
    card = make_card()
    card["status"] = "provider-maintained"
    f = fetcher(card)
    for target, ok in (("skyfi.com", True), ("www.skyfi.com", True), ("cards.skyfi.com", False),
                       ("skyfi.com.evil.net", False), ("evil-skyfi.com", False)):
        res = conformance.check(target, fetcher=f)
        assert ("provider_url" not in fails(res)) is ok, target
