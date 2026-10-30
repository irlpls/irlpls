"""Provider-published cards end to end, with a fake fetch (no network): an improved ProxyPics card goes live and
replaces the compiled one; hostile and broken publishes never do (subdomain, lookalike, suffix trick, invalid JSON,
oversized, redirect, expired), and the real owner's entry is never touched by someone else's domain.

    uv run --with pytest --with jsonschema python -m pytest -q tests/test_published.py
"""
import datetime
import hashlib
import json
import pathlib

import pytest

from irlpls import published, validate

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPILED = [json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))]
BY_ID = {c["id"]: c for c in COMPILED}
NOW = datetime.datetime(2026, 10, 29, 6, 10, tzinfo=datetime.timezone.utc)
TODAY = NOW.date()
OWNER = "www.proxypics.com"   # the host in the compiled card's provider_url
URL = f"https://{OWNER}/.well-known/irlpls"
JSON_HEADERS = {"Content-Type": "application/json", "Cache-Control": "public, max-age=3600", "ETag": '"v1"'}


def improved(host=OWNER, **over):
    """ProxyPics' compiled card as ProxyPics might publish it: claimed, dated today, with a price and rights stated."""
    c = json.loads(json.dumps(BY_ID["proxypics"]))
    c.update(status="provider-maintained", provider_url=f"https://{host}", updated=TODAY.isoformat(),
             expires=(TODAY + datetime.timedelta(days=300)).isoformat())
    c["pricing"].update(from_usd=12, unit="photo request")
    c["licensing"]["ai_training"] = "forbidden"
    c["licensing"]["resale"] = "forbidden"
    c.update(over)
    return c


class Fake:
    """fetch(url, headers) -> (status, headers, body) from a table; records every call."""

    def __init__(self, table=None):
        self.table, self.calls = dict(table or {}), []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        r = self.table.get(url, (404, {}, b""))
        if isinstance(r, Exception):
            raise r
        return r(headers) if callable(r) else r


def body(card):
    return json.dumps(card).encode()


def run(fake, state=None, d=None, now=NOW, extra=()):
    return published.run(COMPILED, state, fetch=fake, now=now, extra=extra, d=d)


@pytest.fixture
def d(tmp_path):
    return tmp_path / "published"


def test_improved_proxypics_goes_live_and_replaces_the_compiled_card(d):
    b = body(improved())
    fake = Fake({URL: (200, JSON_HEADERS, b)})
    state, rejects, board = run(fake, d=d)
    assert not rejects and not board
    e = state["cards"]["proxypics"]
    assert e["state"] == "live" and e["domain"] == OWNER and e["url"] == URL
    assert e["sha256"] == hashlib.sha256(b).hexdigest()
    assert e["first_seen"] == e["last_ok"] == e["last_material_change"] == e["listed_at"] == published.iso(NOW)
    fields = {r["field"]: r for r in e["changed"]}
    assert fields["Price"]["before"] == "not stated" and fields["Price"]["after"].startswith("$12")
    assert fields["AI training"]["after"] == "not allowed" and fields["Resale"]["before"] == "not stated"
    cards, eff = published.overrides(COMPILED, state, d=d, today=TODAY)
    pp = next(c for c in cards if c["id"] == "proxypics")
    assert eff["proxypics"] == "live" and pp["status"] == "provider-maintained" and pp["pricing"]["from_usd"] == 12
    assert next(c for c in cards if c["id"] == "skyfi") is BY_ID["skyfi"]   # nobody else changes
    pub = published.public(state)
    assert set(pub) == {"irlpls_published", "checked_at", "next_read", "schedule", "cards"}
    assert not any(k.startswith("_") for k in pub["cards"]["proxypics"])
    assert pub["next_read"] == published.iso(NOW + datetime.timedelta(days=1))
    # one GET per domain, polite: every compiled card host once, nothing else
    urls = [u for u, _ in fake.calls]
    assert len(urls) == len(set(urls)) and URL in urls
    assert all(u.startswith("https://") and u.endswith("/.well-known/irlpls") for u in urls)


def test_the_next_read_is_conditional_and_honours_max_age(d):
    fake = Fake({URL: (200, JSON_HEADERS, body(improved()))})
    state, *_ = run(fake, d=d)
    # two hours later: max-age 3600 has passed, so it reads again, conditionally, and 304 keeps it live
    fake2 = Fake({URL: lambda h: (304, {}, b"") if h.get("If-None-Match") == '"v1"' else (500, {}, b"")})
    state, *_ = run(fake2, state, d=d, now=NOW + datetime.timedelta(hours=2))
    sent = dict(fake2.calls)[URL]
    assert sent.get("If-None-Match") == '"v1"'
    assert state["cards"]["proxypics"]["state"] == "live"
    assert state["cards"]["proxypics"]["last_read"] == published.iso(NOW + datetime.timedelta(hours=2))
    # a provider asking for a day: a run an hour later does not fetch at all
    fake3 = Fake({URL: (200, {**JSON_HEADERS, "Cache-Control": "public, max-age=86400"}, body(improved()))})
    state, *_ = run(fake3, state, d=d, now=NOW + datetime.timedelta(hours=3))
    fake4 = Fake()
    run(fake4, state, d=d, now=NOW + datetime.timedelta(hours=4))
    assert URL not in [u for u, _ in fake4.calls]


def test_a_material_change_moves_the_strip_date_at_most_once_in_30_days(d):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    t1 = NOW + datetime.timedelta(days=2)
    state, *_ = run(Fake({URL: (200, {**JSON_HEADERS, "ETag": '"v2"'}, body(improved(updated=TODAY.isoformat(), short="Reworded only.")))}), state, d=d, now=t1)
    e = state["cards"]["proxypics"]
    assert e["last_material_change"] == published.iso(NOW)        # wording is not material
    c = improved()
    c["pricing"]["from_usd"] = 9
    state, *_ = run(Fake({URL: (200, {**JSON_HEADERS, "ETag": '"v3"'}, body(c))}), state, d=d, now=t1 + datetime.timedelta(days=1))
    e = state["cards"]["proxypics"]
    assert e["last_material_change"] == published.iso(t1 + datetime.timedelta(days=1))
    assert e["listed_at"] == published.iso(NOW)                    # cooldown: the strip date waits 30 days


@pytest.mark.parametrize("host", ["cards.proxypics.com", "proxypics-photos.com", "www.proxypics.com.evil.net", "proxypics.github.io"])
def test_hostile_domains_never_bind_and_never_touch_the_owner(d, host):
    good = Fake({URL: (200, JSON_HEADERS, body(improved()))})
    state, *_ = run(good, d=d)
    before = json.loads(json.dumps(state["cards"]["proxypics"]))
    evil = improved(host=host)
    evil["pricing"]["from_usd"] = 1
    hostile = Fake({URL: (304, {}, b""), f"https://{host}/.well-known/irlpls": (200, JSON_HEADERS, body(evil))})
    state, rejects, board = run(hostile, state, d=d, now=NOW + datetime.timedelta(days=1), extra=[host])
    assert [r["reason"] for r in rejects] == ["not_owner"] and rejects[0]["host"] == host
    after = state["cards"]["proxypics"]
    assert after["domain"] == OWNER and after["sha256"] == before["sha256"] and after["state"] == "live"
    assert published.load_copy("proxypics", d)["pricing"]["from_usd"] == 12
    same_site = host.endswith(".proxypics.com")
    assert bool(board) is not same_site                            # another registrable domain: the owner hears about it
    if board:
        assert board[0]["kind"] == "info" and "impersonation" in board[0]["text"]


def test_a_lookalike_that_also_claims_its_own_provider_url_still_cannot_take_the_id(d):
    host = "proxypics-photos.com"
    evil = improved(host=host)
    assert not [p for p in validate.problems(evil, fetched_from=f"https://{host}/.well-known/irlpls") if p[0] == "error"]
    state, rejects, board = run(Fake({f"https://{host}/.well-known/irlpls": (200, JSON_HEADERS, body(evil))}), d=d, extra=[host])
    assert "proxypics" not in state["cards"] and rejects[0]["reason"] == "not_owner" and board


@pytest.mark.parametrize("bad,reason", [
    ((200, JSON_HEADERS, b"{not json"), "invalid_json"),
    ((200, JSON_HEADERS, b"{" + b" " * (published.MAX_BYTES + 10) + b"}"), "oversized"),
    (published._http.TooLarge("response is larger than 262144 bytes"), "oversized"),
    ((301, {"Location": "https://evil.example/irlpls"}, b""), "redirect"),
    ((200, {"Content-Type": "text/plain"}, None), "content_type"),
    ((200, JSON_HEADERS, None), "invalid_card"),
])
def test_a_broken_publish_falls_back_to_the_compiled_card_with_a_reason(d, bad, reason):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    if isinstance(bad, tuple) and bad[2] is None:   # a body built here: a valid card served as text, or a broken card
        c = improved()
        if reason == "invalid_card":
            c["verification"]["time"] = {"level": "evidenced", "unknown": "x"}   # evidenced with no method or evidence_url
        bad = (bad[0], bad[1], body(c))
    state, rejects, _ = run(Fake({URL: bad}), state, d=d, now=NOW + datetime.timedelta(days=1))
    assert reason in [r["reason"] for r in rejects]
    e = state["cards"]["proxypics"]
    assert e["state"] == "rejected" and e["reason"].startswith("didn't validate")
    cards, eff = published.overrides(COMPILED, state, d=d, today=TODAY)
    assert eff["proxypics"] == "rejected"
    assert next(c for c in cards if c["id"] == "proxypics") is BY_ID["proxypics"]


def test_unreachable_keeps_the_copy_seven_days_then_falls_back(d):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    state, *_ = run(Fake({URL: TimeoutError("timed out")}), state, d=d, now=NOW + datetime.timedelta(days=1))
    assert state["cards"]["proxypics"]["state"] == "stale"
    cards, eff = published.overrides(COMPILED, state, d=d, today=TODAY + datetime.timedelta(days=1))
    assert eff["proxypics"] == "stale" and next(c for c in cards if c["id"] == "proxypics")["pricing"]["from_usd"] == 12
    cards, eff = published.overrides(COMPILED, state, d=d, today=TODAY + datetime.timedelta(days=published.KEEP_DAYS + 1))
    assert eff["proxypics"] == "gone" and next(c for c in cards if c["id"] == "proxypics") is BY_ID["proxypics"]


def test_404_two_days_in_a_row_is_withdrawn(d):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    state, *_ = run(Fake(), state, d=d, now=NOW + datetime.timedelta(days=1))
    assert state["cards"]["proxypics"]["state"] == "stale"
    state, *_ = run(Fake(), state, d=d, now=NOW + datetime.timedelta(days=2))
    assert state["cards"]["proxypics"]["state"] == "gone"
    assert published.overrides(COMPILED, state, d=d, today=TODAY)[1]["proxypics"] == "gone"


def test_an_expired_card_falls_back_at_once(d):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    old = improved(updated=(TODAY - datetime.timedelta(days=400)).isoformat(), expires=(TODAY - datetime.timedelta(days=40)).isoformat())
    state, *_ = run(Fake({URL: (200, {**JSON_HEADERS, "ETag": '"v9"'}, body(old))}), state, d=d, now=NOW + datetime.timedelta(days=1))
    assert state["cards"]["proxypics"]["state"] in ("expired", "rejected")
    assert published.overrides(COMPILED, state, d=d, today=TODAY)[0][[c["id"] for c in COMPILED].index("proxypics")] is BY_ID["proxypics"]
    # a live copy whose expires passes later turns expired at build time too
    e = {"state": "live", "expires": (TODAY - datetime.timedelta(days=1)).isoformat(), "last_ok": published.iso(NOW)}
    assert published.effective(e, TODAY) == "expired"


def test_a_new_provider_is_never_added_only_listed_for_review(d, tmp_path):
    host = "newco.example"
    c = improved(host=host, id="newco", name="NewCo Field Photos")
    state, rejects, board = run(Fake({f"https://{host}/.well-known/irlpls": (200, JSON_HEADERS, body(c))}), d=d, extra=[host])
    assert "newco" not in state["cards"] and f"{host} newco" in state["new"]
    assert "newco" not in published.public(state)["cards"] and "new" not in published.public(state)
    assert board and board[0]["id"] == "u-published-new-newco.example-newco" and board[0]["kind"] == "info"
    bp = tmp_path / "BOARD.json"
    bp.write_text(json.dumps({"updates": [{"id": "old", "kind": "done", "text": "x"}]}))
    assert published.board_add(board, bp) == [board[0]["id"]]
    assert published.board_add(board + board, bp) == []            # deduped across runs
    assert [u["id"] for u in json.loads(bp.read_text())["updates"]] == [board[0]["id"], "old"]


def test_an_html_catch_all_is_not_a_card_and_is_not_logged(d):
    page = (200, {"Content-Type": "text/html"}, b"<!doctype html><title>Home</title>")
    state, rejects, board = run(Fake({URL: page}), d=d)
    assert not rejects and not board and "proxypics" not in state["cards"]


def test_the_fetcher_refuses_plain_http_and_private_hosts_without_sending():
    with pytest.raises(published._http.UnsafeURL):
        published.default_fetch("http://www.proxypics.com/.well-known/irlpls", {})
    with pytest.raises(published._http.UnsafeURL):
        published.default_fetch("https://localhost/.well-known/irlpls", {})


def test_the_built_in_schema_check_catches_shapes_without_jsonschema():
    schema = json.loads(published.SCHEMA.read_text())
    assert published.mini_schema(schema, improved()) == []
    bad = improved()
    bad["verification"]["time"] = "claimed"
    bad["id"] = "Not An Id!"
    errs = dict(published.mini_schema(schema, bad))
    assert "verification.time" in errs and "id" in errs


def test_save_writes_state_and_the_public_file(d, tmp_path):
    state, *_ = run(Fake({URL: (200, JSON_HEADERS, body(improved()))}), d=d)
    site = tmp_path / "site"
    site.mkdir()
    published.save(state, d=d, site=site)
    pub = json.loads((site / "published.json").read_text())
    assert pub["cards"]["proxypics"]["state"] == "live" and "domains" not in pub
    assert published.load_state(d)["domains"][OWNER]["etag"] == '"v1"'
    published.append_rejects([{"reason": "x"}], d)
    assert (d / "rejects.log").read_text().strip() == '{"reason": "x"}'
