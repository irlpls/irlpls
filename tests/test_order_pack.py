"""Order pack (irlpls/orderpack.py): how_to_pay chosen only from the card, no invented URL, nothing sent or charged."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

import irlpls
from irlpls import mcp_server, orderpack
from irlpls.adapters import _a7

NEED = "photo of the storefront at 12 Main St, Austin, TX within 2 days, AI training rights"
URL = re.compile(r"https?://[^\s,;)\]'\"<>]+")


@pytest.fixture(scope="module")
def job():
    return irlpls.request(NEED, geocode=False, max_price_usd=500)


def pack(job, cid):
    return orderpack.how_to_pay(job, irlpls.card(cid))


def commission_cards():
    return [c for c in irlpls.registry()
            if "commission" in (c.get("delivery") or {}).get("modes", [])]


def check_shape(p):
    assert p["kind"] in orderpack.KINDS
    assert 1 <= len(p["steps"]) <= 4 and all(len(s.split()) <= 14 for s in p["steps"])
    assert p["needs_from_user"] and p["irlpls_does_not"] and p["evidence"]
    assert {"charge you", "take a cut"} <= set(p["irlpls_does_not"])


def test_x402(job):
    p = pack(job, "rentahuman")
    check_shape(p)
    assert p["kind"] == "x402" and "payment.agent_rails" in p["evidence"]
    assert "your own agent wallet" in " ".join(p["steps"]).lower()
    assert _a7.WALLET_SIGNING is False


def test_connect_once(job):
    for cid in ("getterdone", "proxypics"):
        p = pack(job, cid)
        check_shape(p)
        assert p["kind"] == "connect_once" and p["links"]


def test_provider_checkout(job):
    p = pack(job, "wegolook")
    check_shape(p)
    assert p["kind"] == "provider_checkout" and p["links"][0]["url"] == irlpls.card("wegolook")["provider_url"]


def test_request_quote_has_message(job):
    for cid in ("capella", "appen"):
        p = pack(job, cid)
        check_shape(p)
        assert p["kind"] == "request_quote" and "send this for you" in p["irlpls_does_not"]
        m = p["message"]
        body = m["body"]
        assert m["subject"].startswith("Quote request") and "Austin" in m["subject"]
        for want in ("storefront", "12 Main St", "2 days", "Quantity: 1", "train AI", "$500", "Proof wanted"):
            assert want in body
        assert "irlpls" not in (m["subject"] + body).lower()


def test_by_hand(job):
    p = pack(job, "freelancer")
    check_shape(p)
    assert p["kind"] == "by_hand" and any("approval" in s for s in p["steps"])


def test_no_invented_url_over_all_commission_cards(job):
    cards = commission_cards()
    assert len(cards) >= 60
    for c in cards:
        p = orderpack.how_to_pay(job, c)
        check_shape(p)
        blob = json.dumps(c)
        for u in URL.findall(json.dumps(p)):
            assert u in blob, (c["id"], u)
        for ln in p["links"]:
            assert ln["url"] in blob


def test_not_for_live_only_cards(job):
    live = next(c for c in irlpls.registry() if "commission" not in (c.get("delivery") or {}).get("modes", []))
    assert orderpack.how_to_pay(job, live) is None


def test_deterministic(job):
    for cid in ("capella", "rentahuman", "getterdone"):
        assert json.dumps(pack(job, cid), sort_keys=True) == json.dumps(pack(job, cid), sort_keys=True)


def test_job_json_carries_how_to_pay_on_commission_options(job):
    d = job.to_dict()
    com = [o for o in d["plan"] if o["route"] == "commission"]
    assert com and all(o["how_to_pay"]["kind"] in orderpack.KINDS for o in com)
    assert any(o.get("default") and "how_to_pay" in o for o in d["plan"])
    assert "how_to_pay" not in job.plan[0]   # the saved plan itself is untouched


def test_cli_to_order_and_rfq(capsys, job):
    from irlpls import cli
    cli.show(job)
    assert "To order:" in capsys.readouterr().out
    cap = next(o["card_id"] for o in job.to_dict()["plan"] if o.get("how_to_pay", {}).get("kind") == "request_quote")
    cli.main(["rfq", job.id, cap])
    out = capsys.readouterr().out
    assert out.startswith("Subject: Quote request") and "storefront" in out


def test_mcp_read_only_tools_unchanged_and_no_order_tool():
    pytest.importorskip("mcp")
    names = {t.name for t in asyncio.run(mcp_server.build(read_only=True).list_tools())}
    assert names == {"irlpls_answer", "irlpls_choose_provider", "irlpls_find_providers", "irlpls_get_card",
                     "irlpls_get_job", "irlpls_list_capabilities", "irlpls_request"}
