"""Shared plumbing that lets each new adapter live in its own module (docs/research/strategy/WRAPPER-TRIAGE.md, "File
ownership"): registry discovery, adapter-owned shim hooks, form-encoded POSTs and the OAuth token cache."""
import sys
from decimal import Decimal

import pytest

import irlpls.adapters
from irlpls import catalog, http
from irlpls.adapters import _oauth
from irlpls.adapters.base import Adapter, MissingCredentials
from irlpls.protocol.shim import AdapterShim


def _pkg(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir()
    (d / "__init__.py").write_text("")
    for fn, src in files.items():
        (d / fn).write_text(src)
    sys.path.insert(0, str(tmp_path))
    return d


ONE = "from irlpls.adapters.base import Adapter\nclass A(Adapter):\n    card_id = '{cid}'\n    can_live = True\n"


def test_discover_finds_one_adapter_per_module_in_name_order(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    d = _pkg(tmp_path, "fakeadapters_a", {"zeta.py": ONE.format(cid="zeta-card"), "alpha.py": ONE.format(cid="alpha-card"),
                                          "_helper.py": "X = 1\n", "skipme.py": ONE.format(cid="skipped")})
    got = irlpls.adapters.discover("fakeadapters_a", [str(d)], skip={"skipme"})
    assert [a.card_id for a in got] == ["alpha-card", "zeta-card"]


def test_discover_rejects_a_module_with_two_adapters(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    two = ONE.format(cid="one") + "class B(Adapter):\n    card_id = 'two'\n"
    d = _pkg(tmp_path, "fakeadapters_b", {"two.py": two})
    with pytest.raises(ImportError, match="exactly one"):
        irlpls.adapters.discover("fakeadapters_b", [str(d)])


def test_registry_rejects_duplicate_card_ids():
    class X(Adapter):
        card_id = "dup"
    with pytest.raises(ImportError, match="two adapters"):
        irlpls.adapters._registry([X(), X()])


def test_registry_keeps_listed_adapters_first_and_matches_catalog():
    ids = list(irlpls.adapters.ADAPTERS)
    assert ids[:3] == ["nws", "usgs-water", "noaa-tides"]
    # catalog.adapters() reads the sources without importing; both views must agree on which cards have adapters
    assert set(catalog.adapters()) == set(ids)


class _Hooked(Adapter):
    card_id = "proxypics"   # a real card, so the shim can load it
    can_commission = True

    def spec_from_request(self, req, ext_id, deliver_by):
        return {"what": req.get("need"), "ext": ext_id, "by": deliver_by}

    def order_total(self, resp):
        return Decimal("12.5")

    def commission(self, spec, confirm=False):
        self.seen = spec
        return self._dry("POST", "https://example.invalid/orders", spec) if not confirm else {"id": "p1", "status": "unassigned"}


def test_shim_uses_adapter_hooks_before_its_own_tables():
    a = _Hooked()
    s = AdapterShim("proxypics", adapter=a)
    assert s._spec({"need": "photo of the gate"}, "ext-1", "2026-11-01T00:00:00Z") == {
        "what": "photo of the gate", "ext": "ext-1", "by": "2026-11-01T00:00:00Z"}
    assert s._order_total({"cost": 999}) == Decimal("12.5")


def test_shim_falls_back_to_its_tables_without_hooks():
    s = AdapterShim("proxypics")
    assert s._order_total({"cost": 1250}) == Decimal("12.5")
    assert s._spec({"place": "1 Main St", "need": "x"}, "e")["address"] == "1 Main St"


def test_base_cancel_is_unsupported_by_default():
    with pytest.raises(irlpls.adapters.NotSupported):
        Adapter().cancel({"id": "x"})
    assert Adapter.can_cancel is False


def test_http_form_body(monkeypatch):
    sent = {}

    class R:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"ok": true}'

    def fake_open(req, timeout, allow_private=False):
        sent["data"], sent["ct"] = req.data, req.get_header("Content-type")
        return R()

    monkeypatch.setattr(http, "_open", fake_open)
    monkeypatch.setattr(http, "check_url", lambda url, allow_private=False: None)
    assert http.post("https://auth.example/token", form={"grant_type": "client_credentials", "scope": None, "a": "b c"}) == {"ok": True}
    assert sent["data"] == b"grant_type=client_credentials&a=b+c" and sent["ct"] == "application/x-www-form-urlencoded"
    with pytest.raises(ValueError):
        http.post("https://auth.example/token", body={"x": 1}, form={"y": 2})


def test_http_head_returns_lowercased_headers_and_checks_the_url(monkeypatch):
    class R:
        headers = {"Last-Modified": "Wed, 28 Oct 2026 10:00:00 GMT", "Content-Type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def fake_open(req, timeout, allow_private=False):
        seen["method"] = req.get_method()
        return R()

    monkeypatch.setattr(http, "_open", fake_open)
    monkeypatch.setattr(http, "check_url", lambda url, allow_private=False: None)
    assert http.head("https://img.example/cam.jpg")["last-modified"] == "Wed, 28 Oct 2026 10:00:00 GMT"
    assert seen["method"] == "HEAD"
    monkeypatch.undo()
    with pytest.raises(http.UnsafeURL):
        http.head("http://127.0.0.1/cam.jpg")


def test_token_cache_reuses_until_expiry(monkeypatch):
    calls, now = [], [0.0]

    def post(url, body=None, form=None, headers=None, **kw):
        calls.append((url, dict(form), dict(headers or {})))
        return {"access_token": f"t{len(calls)}", "expires_in": 120}

    monkeypatch.setattr(http, "post", post)
    tc = _oauth.TokenCache(clock=lambda: now[0])
    assert tc.client_credentials("https://a/token", "id", "sec", audience="https://api/") == "t1"
    assert tc.client_credentials("https://a/token", "id", "sec", audience="https://api/") == "t1"
    assert calls[0][1] == {"grant_type": "client_credentials", "audience": "https://api/", "client_id": "id", "client_secret": "sec"}
    now[0] = 61.0   # 120 s token, refreshed 60 s early
    assert tc.client_credentials("https://a/token", "id", "sec", audience="https://api/") == "t2"
    tc.client_credentials("https://b/token", "id", "sec", basic=True)
    assert calls[-1][2]["Authorization"].startswith("Basic ") and "client_secret" not in calls[-1][1]
    assert tc.password("https://c/token", "up42-api", "me@x", "pw") == "t4"
    assert calls[-1][1]["grant_type"] == "password" and "client_secret" not in calls[-1][1]


def test_token_cache_refusals_become_missing_credentials(monkeypatch):
    def post(url, **kw):
        raise http.HTTPError(401, url, "invalid_client")

    monkeypatch.setattr(http, "post", post)
    with pytest.raises(MissingCredentials):
        _oauth.TokenCache().client_credentials("https://a/token", "id", "bad")
    with pytest.raises(MissingCredentials):
        _oauth.TokenCache().client_credentials("https://a/token", "", "")
    monkeypatch.setattr(http, "post", lambda url, **kw: {"token_type": "bearer"})
    with pytest.raises(MissingCredentials, match="no access_token"):
        _oauth.TokenCache().client_credentials("https://a/token", "id", "sec")


# ---- prepared calls: the filled-in request an agent (or the SDK, with the user's key) runs against the provider

from irlpls.adapters.base import prepared_call


def test_prepared_call_shape_and_defaults():
    pc = prepared_call("x", "read", "GET", "https://api.x/obs?lat=1", headers={"Authorization": "Bearer {X_KEY}"},
                       auth_kind="bearer", key_name="X_KEY", key_in="header Authorization", get_key_url="https://x/keys")
    assert pc["auth"] == {"kind": "bearer", "key_name": "X_KEY", "placeholder": "{X_KEY}", "in": "header Authorization",
                          "get_key_url": "https://x/keys"}
    assert pc["safe_to_run_without_approval"] is True and "body" not in pc
    assert prepared_call("x", "order", "POST", "https://api.x/orders", body={"a": 1})["safe_to_run_without_approval"] is False
    assert prepared_call("x", "quote", "POST", "https://api.x/q")["safe_to_run_without_approval"] is False
    assert prepared_call("x", "read", "GET", "https://api.x/")["auth"] == {"kind": "none"}
    ph = [{"name": "X_CONTRACT_ID", "what": "your contract id", "where": "GET /contracts"}]
    assert prepared_call("x", "quote", "GET", "https://api.x/{X_CONTRACT_ID}/price", placeholders=ph)["placeholders"] == ph
    with pytest.raises(ValueError):
        prepared_call("x", "delete", "GET", "https://api.x/")
    with pytest.raises(ValueError):
        prepared_call("x", "read", "GET", "https://api.x/", auth_kind="magic")


def test_openaq_prepares_a_ready_call_without_the_real_key(monkeypatch):
    monkeypatch.setenv("OPENAQ_API_KEY", "real-secret-value")
    a = irlpls.adapters.get("openaq")
    pc = a.prepare({"lat": 48.8566, "lon": 2.3522, "need": "pm2.5 now"}, "live")
    assert pc["method"] == "GET" and pc["url"].startswith("https://api.openaq.org/v3/locations?coordinates=48.8566%2C2.3522")
    assert pc["headers"] == {"X-API-Key": "{OPENAQ_API_KEY}"} and pc["auth"]["key_name"] == "OPENAQ_API_KEY"
    assert "real-secret-value" not in repr(pc)
    assert a.prepare({"need": "pm2.5"}, "live") is None            # no coordinates, no call
    assert a.prepare({"lat": 1, "lon": 2}, "commission") is None
    assert Adapter().prepare({"lat": 1, "lon": 2}, "live") is None   # adapters without the hook prepare nothing


def test_plan_carries_the_prepared_call_offline(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("planning must not call a provider")
    monkeypatch.setattr(http, "request", no_network)
    monkeypatch.setenv("OPENAQ_API_KEY", "real-secret-value")
    job = irlpls.request("PM2.5 air quality right now in Paris", geocode=False, lat=48.8566, lon=2.3522, country_code="fr")
    opt = next(o for o in job.plan if o["card_id"] == "openaq")
    assert opt["prepared_call"]["operation"] == "read" and opt["prepared_call"]["safe_to_run_without_approval"] is True
    assert "real-secret-value" not in repr(job.to_dict())
    # a hook that raises never breaks the plan
    from irlpls import broker

    class Boom(Adapter):
        card_id = "openaq"

        def prepare(self, req, route):
            raise RuntimeError("bad hook")
    assert broker._prepared(Boom(), job.request, "live") is None
