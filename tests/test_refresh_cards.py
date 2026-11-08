"""tools/refresh_cards.py: ordering, month counts, probe pacing, bump refusal. No network."""
import importlib.util
import json
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("refresh_cards", Path(__file__).resolve().parent.parent / "tools" / "refresh_cards.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def make(tmp, id, expires, url="https://a.example/docs"):
    card = {"id": id, "provider_url": url, "api": {"docs_url": url}, "updated": "2026-10-27", "expires": expires}
    (tmp / f"{id}.json").write_text(json.dumps(card, indent=2) + "\n")


def test_order_and_months(tmp_path, capsys):
    make(tmp_path, "b", "2027-11-02")
    make(tmp_path, "a", "2027-10-27")
    make(tmp_path, "c", "2027-10-30")
    assert rc.main(["--cards-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.index(" a\n") < out.index(" c\n") < out.index(" b\n")
    assert ("2027-10", 2) in rc.month_counts(rc.by_expiry(rc.load_cards(tmp_path)))


def test_probe_rate_limits_per_host(tmp_path):
    make(tmp_path, "a", "2027-10-27", "https://h.example/x")
    make(tmp_path, "b", "2027-10-27", "https://h.example/y")
    sleeps, now = [], [0.0]
    res = rc.probe(rc.load_cards(tmp_path), fetch=lambda u: 200, sleep=lambda s: sleeps.append(s), clock=lambda: now[0])
    assert sleeps and sleeps[0] == 1.0 and res["a"][0][1] == 200


def test_bump_is_proposal_only(tmp_path, capsys):
    make(tmp_path, "a", "2027-10-27")
    before = (tmp_path / "a.json").read_text()
    assert rc.main(["--cards-dir", str(tmp_path), "--bump", "a"], fetch=lambda u: 200, today=date(2027, 8, 1)) == 0
    assert (tmp_path / "a.json").read_text() == before
    assert '+  "expires": "2028-07-31"' in capsys.readouterr().out


def test_bump_write(tmp_path):
    make(tmp_path, "a", "2027-10-27")
    rc.main(["--cards-dir", str(tmp_path), "--bump", "a", "--write"], fetch=lambda u: 301, today=date(2027, 8, 1))
    c = json.loads((tmp_path / "a.json").read_text())
    assert c["updated"] == "2027-08-01" and c["expires"] == "2028-07-31"


def test_bump_refused_on_failed_probe(tmp_path, capsys):
    make(tmp_path, "a", "2027-10-27")
    before = (tmp_path / "a.json").read_text()
    assert rc.main(["--cards-dir", str(tmp_path), "--bump", "a", "--write"], fetch=lambda u: 404, today=date(2027, 8, 1)) == 1
    assert "REFUSED a" in capsys.readouterr().out
    assert (tmp_path / "a.json").read_text() == before
    assert rc.main(["--cards-dir", str(tmp_path), "--bump", "a", "--write"], fetch=lambda u: "error: Timeout") == 1


def test_verdicts_and_probe_output(tmp_path, capsys):
    assert rc.verdict("base", 404) == "root-info" and rc.verdict("base", 401) == "root-info"
    assert rc.verdict("base", 502) == "FAIL" and rc.verdict("base", "error: URLError") == "FAIL"
    assert rc.verdict("docs", 403) == "blocked" and rc.verdict("docs", 404) == "FAIL" and rc.verdict("docs", 502) == "FAIL"
    card = {"id": "z", "provider_url": "https://p.example/", "api": {"docs_url": "https://p.example/d", "base_url": "https://api.example/"}, "expires": "2027-10-27"}
    (tmp_path / "z.json").write_text(json.dumps(card))
    codes = {"https://p.example/": 403, "https://p.example/d": 200, "https://api.example/": 404}
    assert rc.main(["--cards-dir", str(tmp_path), "--probe"], fetch=lambda u: codes[u]) == 0
    out = capsys.readouterr().out
    assert "root-info z 404 base" in out and "blocked (bot?) z 403 docs" in out and "FAIL" not in out
    assert rc.main(["--cards-dir", str(tmp_path), "--probe"], fetch=lambda u: 500) == 1


def test_http_fetch_uses_safefetch_and_maps_errors():
    from irlpls import safefetch as sf
    calls = []

    class R:
        def __init__(self, status):
            self.status = status

    def fake(url, **kw):
        calls.append(kw["method"])
        if kw["method"] == "HEAD":
            return R(405)
        return R(200)

    assert rc.http_fetch("https://x.example/", fetcher=fake) == 200 and calls == ["HEAD", "GET"]

    def blocked(url, **kw):
        raise sf.BlockedAddress("10.0.0.1")

    assert rc.http_fetch("https://x.example/", fetcher=blocked) == "error: BlockedAddress"
