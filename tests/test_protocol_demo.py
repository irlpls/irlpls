"""Smoke test for tools/protocol_demo.py in fast mode: recorded USGS data, a 1-second simulator, and no network beyond
the demo's own two servers on 127.0.0.1 (any other connection fails the test)."""
import importlib.util
import pathlib
import socket

import pytest

pytest.importorskip("cryptography")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("protocol_demo", ROOT / "tools" / "protocol_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def loopback_only(monkeypatch):
    real = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"the fast demo tried to reach {address!r}")
        return real(self, address)
    real_gai = socket.getaddrinfo

    def getaddrinfo(host, *a, **k):
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"the fast demo tried to resolve {host!r}")
        return real_gai(host, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_fast_demo_runs_every_step_offline(loopback_only, tmp_path):
    demo = _load()
    out = tmp_path / "DEMO.md"
    r = demo.run(fast=True, out=out)
    failed = [s for s in r["steps"] if not s["ok"]]
    assert r["ok"] and not failed, failed
    assert r["usgs_source"] == "recorded"
    titles = " | ".join(s["title"] for s in r["steps"])
    for want in ("Quote the river", "Order the river", "Verify the river receipts", "Idempotency-Key", "Replay",
                 "Poll status", "Verify the photo receipt", "Tamper", "person-targeting"):
        assert want in titles, want
    t = r["text"]
    assert "USGS data source:   RECORDED" in t
    assert "same order id; the provider holds 1 order" in t
    assert "idempotency_key_reused" in t and t.count("person_targeting_refused") == 2
    assert "verified=False" in t and "RESULT: all" in t
    md = out.read_text()
    assert md.startswith("# irlpls protocol: end-to-end demo") and "**USGS data: RECORDED.**" in md
    assert "| 12 | Replay the same order | pass |" in md and "```text" in md


def test_demo_leaves_global_state_as_it_found_it(loopback_only):
    import irlpls
    from irlpls import broker
    before = (irlpls._registry, broker.HOME, broker.PROTOCOL.get("client"))
    _load().run(fast=True, out=None)
    assert (irlpls._registry, broker.HOME, broker.PROTOCOL.get("client")) == before
