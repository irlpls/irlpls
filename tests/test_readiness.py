"""One-yes readiness, the account / agent-rail / CIMD card fields, and the CIMD correction (MCP spec 2026-07-28).
docs/research/strategy/ACCOUNT-QUESTION.md, "Where I land", items 1 and 2."""
import copy
import importlib.util
import json
import pathlib

import pytest

import irlpls
from irlpls import catalog
from irlpls.broker import READY_MARGIN, Job
from irlpls.readiness import LEVELS, readiness, readiness_for
from irlpls.validate import problems

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS = {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "cards").glob("*.json"))}
SPEC = "https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration"


def base(**over):
    c = {"access": {"key_required": True, "gate": "none", "automation_allowed": True}, "api": {"auth": []}, "payment": {}}
    for k, v in over.items():
        c[k] = v
    return c


MAY = {"allowed": True, "source_url": "https://example.com/terms"}
OAUTH_CIMD = {"type": "oauth2", "cimd": True, "transport": "mcp"}
OAUTH_DCR = {"type": "oauth2", "dynamic_registration": True, "transport": "mcp"}
OAUTH_PLAIN = {"type": "oauth2", "transport": "rest"}


# ------------------------------------------------------------------ the rules
@pytest.mark.parametrize("card,level,via", [
    (base(access={"key_required": False, "gate": "none"}), "ready_now", "keyless"),
    (base(access={"key_required": False, "gate": "none"}, payment={"agent_rails": ["x402"]}), "ready_now", "wallet"),
    (base(payment={"agent_rails": ["x402"]}, account={"headless_signup": True, "agent_may_act": MAY}), "ready_now", "wallet_signup"),
    (base(api={"auth": [OAUTH_CIMD]}, account={"agent_may_act": MAY}), "one_consent", "oauth"),
    (base(api={"auth": [OAUTH_DCR]}, account={"agent_may_act": MAY}), "one_consent", "oauth"),
    (base(account={"headless_signup": True, "agent_may_act": MAY}), "one_consent", "headless_signup"),
    (base(api={"auth": [OAUTH_PLAIN]}, account={"agent_may_act": MAY}), "account_first", "manual"),
    (base(), "account_first", "manual"),
    (base(access={"key_required": False, "gate": "contract"}), "contract", "gate"),
    (base(access={"key_required": True, "gate": "none", "automation_allowed": False}), "contract", "terms"),
    (base(api={"auth": [OAUTH_CIMD]}, account={"agent_may_act": {**MAY, "allowed": False}}), "contract", "terms"),
])
def test_rules(card, level, via):
    r = readiness(card)
    assert (r["level"], r["via"]) == (level, via)
    assert r["line"] and len(r["line"].split()) <= 10  # one short plain line (card pages keep a word budget)


def test_unstated_never_moves_a_provider_up():
    # self-registering OAuth or a wallet rail without a sourced agent_may_act stays account_first
    assert readiness(base(api={"auth": [OAUTH_CIMD]}))["level"] == "account_first"
    assert readiness(base(payment={"agent_rails": ["x402", "mpp"]}, account={"headless_signup": True}))["level"] == "account_first"
    assert readiness(base(account={"headless_signup": True}))["level"] == "account_first"
    # key_required unstated is not keyless
    assert readiness(base(access={"gate": "none"}))["level"] == "account_first"
    # a card rail only on payment.methods (not agent_rails) is not a wallet rail
    assert readiness(base(payment={"methods": ["x402"]}, account={"headless_signup": True, "agent_may_act": MAY}))["level"] == "one_consent"


def test_gate_beats_everything():
    c = base(access={"key_required": False, "gate": "approval"}, payment={"agent_rails": ["x402"]})
    assert readiness(c)["level"] == "contract"


def test_kyc_is_said_in_the_line_but_keeps_the_level():
    r = readiness(base(api={"auth": [OAUTH_DCR]}, account={"agent_may_act": MAY, "kyc": "id"}))
    assert r["level"] == "one_consent" and r["line"] == "One sign-in and an ID check the first time."
    r = readiness(base(account={"headless_signup": True, "agent_may_act": MAY, "kyc": "business"}))
    assert r["line"] == "One sign-up and a business check the first time."
    assert readiness(base(api={"auth": [OAUTH_CIMD]}, account={"agent_may_act": MAY, "kyc": "email"}))["line"] == "One sign-in the first time."


def test_plain_lines():
    assert readiness(base(api={"auth": [OAUTH_CIMD]}, account={"agent_may_act": MAY}))["line"] == "One sign-in the first time."
    assert readiness(base())["line"] == "Needs an account first."


# ------------------------------------------------------------------ the real cards (filled only from cited sources)
EXPECTED = {
    "vangrid": "ready_now", "rentahuman": "ready_now", "execution-market": "ready_now", "usgs-water": "ready_now",
    "skyfi": "one_consent", "getterdone": "one_consent", "airtasker": "one_consent", "errand": "one_consent", "mundane": "one_consent",
    "proxypics": "account_first", "quest": "account_first", "hover": "account_first",
    "globhe": "contract", "hire-a-human": "contract", "freelancer": "contract", "umbra": "contract",
}


@pytest.mark.parametrize("cid,level", sorted(EXPECTED.items()))
def test_real_cards(cid, level):
    assert readiness(CARDS[cid])["level"] == level


def test_card_fields_validate_and_cite_sources():
    for cid, c in CARDS.items():
        errs = [p for p in problems(c) if p[0] == "error"]
        assert not errs, (cid, errs)
        may = (c.get("account") or {}).get("agent_may_act")
        if may:
            assert may["source_url"] in {s["url"] for s in c.get("sources") or []}, cid  # the terms are a listed source
        if (c.get("payment") or {}).get("agent_rails") or c.get("account") or any("cimd" in m for m in (c.get("api") or {}).get("auth") or []):
            assert c.get("sources"), cid


def test_filled_facts():
    assert CARDS["rentahuman"]["payment"]["agent_rails"] == ["x402", "mpp"]
    assert CARDS["vangrid"]["payment"]["agent_rails"] == ["x402"]
    assert set(CARDS["execution-market"]["payment"]["agent_rails"]) == {"x402", "mpp"}
    assert CARDS["mundane"]["account"]["headless_signup"] is True
    assert any(m.get("cimd") for m in CARDS["mundane"]["api"]["auth"])
    assert any(m.get("cimd") for m in CARDS["execution-market"]["api"]["auth"])
    assert CARDS["rentahuman"]["account"]["agent_may_act"]["allowed"] is True
    assert CARDS["freelancer"]["account"]["agent_may_act"]["allowed"] is False
    # no card claims a rail nobody documented
    assert not any(r in ("spt", "link_card") for c in CARDS.values() for r in (c.get("payment") or {}).get("agent_rails") or [])
    # CIMD only where a source says so
    assert {cid for cid, c in CARDS.items() if any(m.get("cimd") for m in (c.get("api") or {}).get("auth") or [])} == {"mundane", "execution-market"}


def test_schema_rejects_bad_values():
    c = copy.deepcopy(CARDS["rentahuman"])
    c["payment"]["agent_rails"] = ["paypal"]
    assert any(p[0] == "error" and p[1].startswith("payment.agent_rails") for p in problems(c))
    c = copy.deepcopy(CARDS["rentahuman"])
    c["account"]["agent_may_act"] = {"allowed": True}
    assert any(p[0] == "error" and p[1].startswith("account.agent_may_act") for p in problems(c))
    c["account"]["agent_may_act"] = {"allowed": True, "source_url": "http://rentahuman.ai/terms"}
    assert any(p[0] == "error" and p[1].startswith("account") for p in problems(c))
    c = copy.deepcopy(CARDS["rentahuman"])
    c["account"]["kyc"] = "passport"
    assert any(p[0] == "error" and p[1].startswith("account.kyc") for p in problems(c))
    c = copy.deepcopy(CARDS["rentahuman"])
    c["api"]["auth"][0]["cimd"] = True  # an api_key_header method
    assert any(p[0] == "error" and p[1] == "api.auth.0" for p in problems(c))


# ------------------------------------------------------------------ CIMD correction
def test_dynamic_registration_marked_deprecated():
    std = (ROOT / "docs" / "STANDARD.md").read_text()
    agents = (ROOT / "docs" / "AGENTS.md").read_text()
    assert "RFC 7591 dynamic client registration is RECOMMENDED" not in std
    for doc in (std, agents):
        assert SPEC in doc and "deprecated" in doc.lower() and "Client ID Metadata Document" in doc
    assert "with `dynamic_registration`, you can register yourself and your person links" not in agents
    schema = json.loads((ROOT / "schema" / "capture-card.schema.json").read_text())
    auth = schema["properties"]["api"]["properties"]["auth"]["items"]["properties"]
    assert "Deprecated" in auth["dynamic_registration"]["description"] and auth["cimd"]["type"] == "boolean"
    for cid, c in CARDS.items():
        for m in (c.get("api") or {}).get("auth") or []:
            if m.get("dynamic_registration") and not m.get("cimd"):
                assert "deprecated" in m["how_to_get"], cid
                assert SPEC in {s["url"] for s in c["sources"]}, cid


# ------------------------------------------------------------------ catalog
def test_catalog_shows_readiness():
    cat, idx = catalog.build([c for c in CARDS.values()])
    sdk = catalog.adapters()
    for pid, p in cat["providers"].items():
        assert p["readiness"] == readiness_for(CARDS[pid], sdk.get(pid))
    assert sum(cat["stats"]["readiness"].values()) == cat["stats"]["providers"]
    assert set(cat["stats"]["readiness"]) == set(LEVELS)
    assert idx["row_format"].endswith("| readiness")
    for f in idx["families"]:
        for r in f["provider_rows"]:
            pid, level = r.split(" | ")[0], r.split(" | ")[-1]
            assert level == cat["providers"][pid]["readiness"]["level"]
    assert cat["providers"]["mundane"]["api"]["oauth_cimd"] is True
    assert cat["providers"]["rentahuman"]["agent_rails"] == ["x402", "mpp"]
    assert cat["providers"]["freelancer"]["account"]["agent_may_act"] is False


# ------------------------------------------------------------------ broker: plan items and the near-tie
def test_plan_items_carry_readiness():
    job = irlpls.request("street video within 3 days", geocode=False, lat=30.27, lon=-97.74, country_code="us")
    assert job.plan
    for o in job.plan:
        a = irlpls.adapters.get(o["card_id"])
        sdk = {"env": a.env, "wallet": a.needs_wallet, "note": a.readiness_note} if a else None
        assert o["readiness"] == readiness_for(irlpls.card(o["card_id"]), sdk)
        assert o["readiness"]["line"].endswith(".")


def opt(cid, fit, level, callable_=True, needs_key=False, route="commission"):
    return {"card_id": cid, "fit": fit, "policy": [], "route": route,
            "sdk": {"callable": callable_, "needs_key": needs_key},
            "readiness": {"level": level, "line": ""}}


def best(*opts):
    job = Job("x", None, None)
    job.plan = list(opts)
    return job.best()["card_id"]


def test_readiness_breaks_a_near_tie():
    assert best(opt("acct", 80, "account_first"), opt("yes", 80 - READY_MARGIN, "one_consent")) == "yes"
    assert best(opt("acct", 80, "account_first"), opt("now", 78, "ready_now"), opt("one", 79, "one_consent")) == "now"


def test_readiness_never_beats_a_much_better_fit():
    assert best(opt("acct", 80, "account_first"), opt("yes", 80 - READY_MARGIN - 1, "ready_now")) == "acct"
    assert best(opt("acct", 90, "account_first"), opt("yes", 60, "ready_now")) == "acct"


def test_equal_readiness_keeps_the_better_fit():
    assert best(opt("a", 80, "one_consent"), opt("b", 79, "one_consent")) == "a"
    assert best(opt("a", 80, "contract"), opt("b", 80, "contract")) == "a"


def test_readiness_does_not_override_callable_or_key_rules():
    # a no-key callable option still wins over a closer-to-yes option that needs a key, as before
    assert best(opt("nokey", 80, "account_first"), opt("key", 80, "ready_now", needs_key=True)) == "nokey"
    # directory-only near-ties among themselves: readiness picks
    assert best(opt("a", 70, "contract", callable_=False), opt("b", 68, "one_consent", callable_=False)) == "b"


# ------------------------------------------------------------------ card page
def test_card_page_line_under_api():
    spec = importlib.util.spec_from_file_location("build", ROOT / "build.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    for cid in ("skyfi", "proxypics", "vangrid", "globhe", "usgs-water"):
        h = b.card_article(CARDS[cid])
        after = h.split('<h2 class="sec">API</h2>', 1)[1]
        r = b.readiness_for(CARDS[cid], b.ADAPTERS.get(cid))  # the page shows what the reference library needs, like catalog.json
        assert after.startswith(f'<p class="rdy" data-level="{r["level"]}">{r["line"]}</p>'), cid
    assert "or link an account" in b.key_text(CARDS["mundane"])  # CIMD counts as linking an account
