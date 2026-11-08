"""published.run in parallel: bounded pool, budget, SSRF-blocked hosts, deterministic output (offline, injected fetchers)."""
import datetime
import json
import threading
import time

from irlpls import published, safefetch

NOW = datetime.datetime(2026, 11, 8, 6, 10, tzinfo=datetime.timezone.utc)


def hosts(n):
    return [f"d{i:03d}.example.org" for i in range(n)]


def go(fetch, extra, d, **kw):
    return published.run([], None, fetch=fetch, now=NOW, extra=extra, d=d, **kw)


def test_slow_domains_finish_in_about_max_not_sum(tmp_path):
    def slow(url, headers):
        time.sleep(0.2)
        return 404, {}, b""
    t = time.monotonic()
    state, _, _ = go(slow, hosts(16), tmp_path)
    assert time.monotonic() - t < 1.0          # serial would be 3.2 s
    assert len(state["domains"]) == 16


def test_pool_is_bounded_and_one_request_per_host(tmp_path):
    live, peak, seen, lock = [0], [0], [], threading.Lock()

    def f(url, headers):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            seen.append(url)
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return 404, {}, b""
    go(f, hosts(40), tmp_path, workers=4)
    assert peak[0] <= 4 and len(seen) == len(set(seen)) == 40


def test_budget_exhausted_reports_skipped_and_finishes(tmp_path):
    def slow(url, headers):
        time.sleep(0.1)
        return 404, {}, b""
    t = time.monotonic()
    state, _, _ = go(slow, hosts(20), tmp_path, workers=2, budget=0.25)
    assert time.monotonic() - t < 2
    skipped = published.run.last_skipped
    assert skipped and skipped == sorted(skipped)
    assert all(state["domains"][h]["status"] == published.SKIPPED for h in skipped)
    assert len(skipped) < 20     # the ones started in time were read


def test_ssrf_blocked_domain_is_reported_not_a_crash(tmp_path):
    def f(url, headers):
        if "d001" in url:
            raise safefetch.BlockedAddress("private address")
        return 404, {}, b""
    state, _, _ = go(f, hosts(3), tmp_path)
    assert state["domains"]["d001.example.org"]["status"] == "BlockedAddress"
    assert state["domains"]["d000.example.org"]["status"] == 404


def test_default_fetch_blocks_private_and_plain_http_without_sending():
    for u in ("http://x.example.org/.well-known/irlpls", "https://localhost/.well-known/irlpls"):
        try:
            published.default_fetch(u, {})
        except safefetch.SafeFetchError:
            continue
        raise AssertionError(u)


def test_output_is_deterministic_whatever_the_timing(tmp_path):
    import random

    def jittery(url, headers):
        time.sleep(random.random() * 0.03)
        return 404, {}, b""
    hs = hosts(30)
    a, ra, _ = go(jittery, hs, tmp_path / "a", workers=16)
    b, rb, _ = go(jittery, list(reversed(hs)), tmp_path / "b", workers=3)
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)
    assert list(a["domains"]) == sorted(a["domains"]) and ra == rb
