"""Client-side rate limits (irlpls/ratelimit.py): pacing, Retry-After, cache, fan-out. No network; a fake clock."""
import email.message
import io
import json
import random
import urllib.error
import urllib.parse

import pytest

import irlpls
from irlpls import http, ratelimit
from irlpls.adapters import usgs_water


class FakeClock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        if s > 0:
            self.slept.append(s)
            self.t += s


@pytest.fixture
def clock(monkeypatch):
    ratelimit.reset()
    c = FakeClock()
    monkeypatch.setattr(ratelimit, "CLOCK", c)
    monkeypatch.delenv("USGS_WATER_API_KEY", raising=False)
    yield c
    ratelimit.reset()


def _ok(value="x"):
    return lambda: {"v": value}


def test_bucket_allows_burst_then_paces(clock):
    ratelimit.configure("p", requests=1, per_seconds=1, burst=5)
    sent = []
    for i in range(10):
        ratelimit.call("p", "POST", f"https://p.example/{i}", lambda: sent.append(clock.t))
    assert len(sent) == 10
    assert sent[:5] == [1000.0] * 5                       # the burst goes at once
    assert sent[5:] == pytest.approx([1001, 1002, 1003, 1004, 1005])  # then one per second, queued in order


def test_default_for_a_card_with_no_stated_limit(clock):
    lim = ratelimit.limits("no-such-card")
    assert (lim["requests"], lim["per_seconds"], lim["burst"], lim["stated"]) == (1, 1, 5, False)
    assert "publishes no number" in ratelimit.describe("no-such-card")


def test_card_rate_limit_and_with_key(clock, monkeypatch):
    card = {"id": "k", "access": {"rate_limit": {"requests": 60, "per_seconds": 60, "scope": "per-ip",
                                                 "source_url": "https://k.example/limits",
                                                 "with_key": {"requests": 1000, "per_seconds": 3600, "scope": "per-key"}}}}
    monkeypatch.setattr(irlpls, "card", lambda cid: card if cid == "k" else None)
    lim = ratelimit.limits("k")
    assert (lim["requests"], lim["per_seconds"], lim["burst"], lim["scope"], lim["stated"]) == (60, 60, 5, "per-ip", True)
    assert ratelimit.describe("k") == "k allows 60 requests per minute per IP"

    class A:
        env = "K_KEY"

        def configured(self):
            return True
    monkeypatch.setattr(irlpls.adapters, "get", lambda cid: A())
    lim = ratelimit.limits("k")
    assert (lim["requests"], lim["per_seconds"], lim["scope"], lim["keyed"]) == (1000, 3600, "per-key", True)


def test_retry_after_is_honoured_and_pauses_the_provider(clock):
    calls = []

    def send():
        calls.append(clock.t)
        if len(calls) == 1:
            raise http.HTTPError(429, "https://p.example/a", "slow down", headers={"retry-after": "7"})
        return {"ok": True}
    assert ratelimit.call("p", "GET", "https://p.example/a", send) == {"ok": True}
    assert calls[1] - calls[0] >= 7
    assert ratelimit.STATS["retried"] == 1
    # everyone else in the process waits too
    t0 = clock.t
    ratelimit.bucket("p").block(5)
    ratelimit.call("p", "POST", "https://p.example/b", lambda: None)
    assert clock.t - t0 >= 5


def test_retry_after_http_date(clock):
    import email.utils
    import time
    d = email.utils.formatdate(time.time() + 30, usegmt=True)
    assert 25 <= ratelimit.retry_after({"Retry-After": d}) <= 31
    assert ratelimit.retry_after({"Retry-After": "12"}) == 12
    assert ratelimit.retry_after({}) is None


def test_503_without_retry_after_backs_off_then_gives_up(clock):
    calls = []

    def send():
        calls.append(clock.t)
        raise http.HTTPError(503, "https://p.example/a", "busy")
    with pytest.raises(http.HTTPError) as e:
        ratelimit.call("p", "GET", "https://p.example/a", send)
    assert e.value.status == 503 and len(calls) == ratelimit.MAX_RETRIES + 1
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert gaps[0] >= 1 and gaps[1] >= 2   # exponential


def test_long_retry_after_is_not_slept_but_reported(clock):
    def send():
        raise http.HTTPError(429, "https://p.example/a", "", headers={"Retry-After": "3600"})
    with pytest.raises(http.HTTPError) as e:
        ratelimit.call("p", "GET", "https://p.example/a", send)
    assert e.value.retry_after == 3600
    assert sum(clock.slept) < ratelimit.MAX_BACKOFF_S


def test_budget_refuses_before_sending(clock):
    ratelimit.configure("p", requests=1, per_seconds=10, burst=1)
    sent = []
    with ratelimit.using("p", budget_s=5):
        ratelimit.call("p", "POST", "https://p.example/1", lambda: sent.append(1))
        with pytest.raises(ratelimit.RateLimited) as e:
            ratelimit.call("p", "POST", "https://p.example/2", lambda: sent.append(2))
    assert sent == [1]
    assert e.value.status == 429 and e.value.client_side and e.value.retry_after == pytest.approx(10)


def test_cache_reuses_identical_asks_within_the_interval(clock):
    ratelimit.configure("p", requests=1, per_seconds=1, cache_seconds=900)
    n = []

    def send():
        n.append(1)
        return {"reading": len(n)}
    a = ratelimit.call("p", "GET", "https://P.example/x?b=2&a=1", send, headers={"X-Api-Key": "one"})
    b = ratelimit.call("p", "GET", "https://p.example/x?a=1&b=2", send, headers={"X-Api-Key": "two"})
    assert a == b == {"reading": 1} and len(n) == 1     # same request, query reordered, key ignored
    a["reading"] = "mutated"
    assert ratelimit.call("p", "GET", "https://p.example/x?a=1&b=2", send) == {"reading": 1}  # callers get copies
    ratelimit.call("p", "GET", "https://p.example/x?a=1&b=2", send, headers={"Accept": "text/csv"})
    assert len(n) == 2                                   # a different Accept is a different answer
    clock.t += 901
    assert ratelimit.call("p", "GET", "https://p.example/x?a=1&b=2", send) == {"reading": 3}
    ratelimit.call("p", "POST", "https://p.example/x?a=1&b=2", send)
    ratelimit.call("p", "POST", "https://p.example/x?a=1&b=2", send)
    assert len(n) == 5                                   # never cache POST


# -- through http.request ------------------------------------------------------------------------------------------
class _Resp:
    def __init__(self, body, headers=None):
        self._b, self.headers = io.BytesIO(body), dict(headers or {})

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_request_user_agent_and_retry_after(clock, monkeypatch):
    seen = []
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])

    def opener(req, timeout, allow_private=False):
        seen.append((clock.t, req.get_header("User-agent")))
        if len(seen) == 1:
            m = email.message.Message()
            m["Retry-After"] = "4"
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", m, io.BytesIO(b"slow"))
        return _Resp(b'{"ok": 1}')
    monkeypatch.setattr(http, "_open", opener)
    assert http.get("https://feed.example/x", provider="feed") == {"ok": 1}
    assert seen[1][0] - seen[0][0] >= 4
    assert "hello@irlpls.com" in seen[0][1] and "irlpls" in seen[0][1]
    # outside any provider context nothing is paced or cached
    with pytest.raises(http.HTTPError):
        seen.clear()
        http.get("https://feed.example/y")
    assert len(seen) == 1


# -- fan-out: the 1,000-ask USGS scenario ----------------------------------------------------------------------------
def _gauges(lat0, lat1, lon0, lon1, step=0.25):
    out, i = [], 0
    lat = lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            out.append((f"USGS-{8000000 + i}", round(lat, 3), round(lon, 3)))
            i += 1
            lon += step
        lat += step
    return out


class FakeUSGS:
    """Serves latest-continuous like the OGC API: bbox filter, limit, next links. Records when each request was sent."""

    def __init__(self, clock, sites, page=None):
        self.clock, self.sites, self.page, self.sent = clock, sites, page, []

    def __call__(self, req, timeout, allow_private=False):
        self.sent.append(self.clock.t)
        u = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query))
        assert u.path.endswith("/collections/latest-continuous/items"), req.full_url
        w, s, e, n = map(float, q["bbox"].split(","))
        hits = [x for x in self.sites if s <= x[1] <= n and w <= x[2] <= e]
        limit, off = min(int(q["limit"]), self.page or 10 ** 9), int(q.get("offset", 0))
        chunk = hits[off: off + limit]
        feats = [{"geometry": {"coordinates": [lon, lat]}, "properties": {
            "monitoring_location_id": site, "time": "2026-10-28T12:00:00+00:00", "value": "1.5",
            "unit_of_measure": "ft" if q["parameter_code"] == "00065" else "ft^3/s", "approval_status": "Provisional"}}
            for site, lat, lon in chunk]
        links = []
        if off + limit < len(hits):
            nq = dict(q, offset=str(off + limit))
            links.append({"rel": "next", "href": urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(nq), ""))})
        return _Resp(json.dumps({"type": "FeatureCollection", "features": feats, "links": links}).encode())


def _under_limit(times, rate, burst):
    """True if every run of consecutive sends fits a token bucket of this rate and burst."""
    times = sorted(times)
    for i in range(len(times)):
        for j in range(i, len(times)):
            if (j - i + 1) > burst + rate * (times[j] - times[i]) + 1e-6:
                return False
    return True


def test_1000_usgs_asks_batch_and_stay_under_the_limit(clock, monkeypatch):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    fake = FakeUSGS(clock, _gauges(28.0, 32.0, -100.0, -96.0, step=0.1), page=500)
    monkeypatch.setattr(http, "_open", fake)
    rnd = random.Random(7)
    asks = [(round(rnd.uniform(28.2, 31.8), 4), round(rnd.uniform(-99.8, -96.2), 4)) for _ in range(900)]
    asks += asks[:100]                                    # agents repeat themselves; repeats cost nothing
    res = irlpls.live_many("usgs-water", asks, budget_s=3600)
    assert len(res) == 1000 and res.pending == []
    assert all(r for r in res), "every ask has gauge readings"
    assert all(o["distance_km"] <= 15 for r in res for o in r)
    assert not [w for w in res.warnings if w["code"] == "rate_limited"]
    lim = ratelimit.limits("usgs-water")
    assert len(fake.sent) <= 40, f"batched: {len(fake.sent)} requests for 1000 asks"
    assert _under_limit(fake.sent, lim["requests"] / lim["per_seconds"], lim["burst"])
    # the same 1000 asks again inside the update interval are answered from the cache
    n = len(fake.sent)
    again = irlpls.live_many("usgs-water", asks, budget_s=3600)
    assert len(fake.sent) == n and [len(r) for r in again] == [len(r) for r in res]


def test_fan_out_over_budget_returns_partial_results_and_says_so(clock, monkeypatch):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    fake = FakeUSGS(clock, _gauges(25.0, 49.0, -124.0, -67.0, step=1.0))
    monkeypatch.setattr(http, "_open", fake)
    rnd = random.Random(3)
    asks = [{"lat": rnd.uniform(25, 49), "lon": rnd.uniform(-124, -67)} for _ in range(1000)]
    res = irlpls.live_many("usgs-water", asks, budget_s=20)
    lim = ratelimit.limits("usgs-water")
    assert len(res) == 1000
    pending = [i for i, r in enumerate(res) if r is None]
    assert pending and pending == res.pending                       # never a silent drop
    w = next(w for w in res.warnings if w["code"] == "rate_limited")
    assert w["pending"] == pending and w["asked"] == 1000 and w["answered"] == 1000 - len(pending)
    assert w["msg"].startswith(f"rate limited: {len(pending)} of 1000 asks still queued")
    assert "USGS_WATER_API_KEY" in w["msg"] and "do not loop" in w["msg"]
    assert _under_limit(fake.sent, lim["requests"] / lim["per_seconds"], lim["burst"])
    assert clock.t - 1000 <= 20 + 1e-6                               # stayed inside the budget


def test_live_many_without_batch_queues_and_dedups(clock, monkeypatch):
    calls = []

    class Feed:
        card_id, env = "sensor-community", None

        def configured(self):
            return True

        def live(self, lat, lon, **kw):
            calls.append((lat, lon))
            http.request("GET", f"https://feed.example/{lat},{lon}", provider=None)
            return [{"lat": lat, "lon": lon}]
    monkeypatch.setattr(irlpls.adapters, "get", lambda cid: Feed())
    ratelimit.configure("sensor-community", requests=1, per_seconds=1, burst=5)
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    sent = []
    monkeypatch.setattr(http, "_open", lambda req, timeout, allow_private=False: (sent.append(clock.t), _Resp(b"{}"))[1])
    asks = [(52.5, 13.4)] * 3 + [(48.1, 11.6 + i / 100) for i in range(12)]
    res = irlpls.live_many("sensor-community", asks, budget_s=4)
    assert len(calls) <= 1 + 12 and calls.count((52.5, 13.4)) == 1  # identical asks answered once
    assert res[0] == res[1] == res[2]
    assert res.pending and all(res[i] is None for i in res.pending)  # 13 unique asks, 1/s burst 5, 4 s budget
    assert len(sent) == 5 + 4
    assert any(w["code"] == "rate_limited" for w in res.warnings)


def test_job_fan_out_puts_the_warning_on_the_job(clock, monkeypatch):
    monkeypatch.setattr(http, "_resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(http, "_open", FakeUSGS(clock, _gauges(25.0, 49.0, -124.0, -67.0, step=1.0)))
    job = irlpls.request("river flow at Comfort, TX right now", geocode=False, lat=29.97, lon=-98.9, country_code="us", state="texas")
    job.choose("usgs-water")
    rnd = random.Random(5)
    job.submit(asks=[(rnd.uniform(25, 49), rnd.uniform(-124, -67)) for _ in range(300)], budget_s=10)
    assert job.state == "delivered"
    w = next(w for w in job.warnings if w["code"] == "rate_limited")
    assert job.pending == w["pending"] and "of 300 asks still queued" in w["msg"]
    assert all("ask" in o for o in job.results)


# -- the card field ----------------------------------------------------------------------------------------------------
def test_validate_rate_limit_needs_a_source():
    from irlpls.validate import problems
    c = json.loads((irlpls.registry().get("usgs-water") and open(pathlib_root() / "cards" / "usgs-water.json").read()))
    c["access"]["rate_limit"] = {"requests": 10, "per_seconds": 60}
    assert any(w == "access.rate_limit" and "source_url" in m for s, w, m in problems(c))
    c["access"]["rate_limit"] = {"requests": 10, "per_seconds": 60, "source_url": "https://example.gov/limits"}
    assert not [p for p in problems(c) if p[1].startswith("access.rate_limit") and p[0] == "error"]
    c["access"]["rate_limit"] = {"requests": 10, "source_url": "https://example.gov/limits"}
    assert any(w.startswith("access.rate_limit") and s == "error" for s, w, m in problems(c))


def test_every_stated_card_limit_cites_a_source():
    root = pathlib_root()
    for f in sorted((root / "cards").glob("*.json")):
        rl = (json.loads(f.read_text()).get("access") or {}).get("rate_limit")
        if rl:
            assert rl.get("source_url", "").startswith("https://"), f.name


def pathlib_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent
