"""irlpls_list_capabilities, the irlpls://catalog resources and the find_providers 'verdict none' answer, through the real SDK."""
import asyncio
import json

import pytest

import irlpls
from irlpls import catalog, mcp_server


@pytest.fixture(scope="module")
def srv():
    pytest.importorskip("mcp")
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        pytest.skip("needs mcp >= 2")
    return mcp_server.build(read_only=True)


def call(srv, name, args=None):
    r = asyncio.run(srv.call_tool(name, args or {}))
    return json.loads(r.content[0].text)


def test_tool_in_both_modes_and_read_only(srv):
    for ro in (True, False):
        tools = {t.name: t for t in asyncio.run(mcp_server.build(read_only=ro).list_tools())}
        t = tools["irlpls_list_capabilities"]
        assert t.annotations.read_only_hint and t.annotations.idempotent_hint and not t.annotations.open_world_hint


def test_unfiltered_index_is_compact_and_complete(srv):
    out = call(srv, "irlpls_list_capabilities")
    assert {t["id"] for t in out["question_types"]} == {"read", "watch", "send", "overhead", "collect"}
    assert len(json.dumps(out)) < 40_000 and "providers" not in out and out["unsupported"]
    cards = [c for c in irlpls.registry().all if c.get("status") != "defunct" and (c.get("delivery") or {}).get("modes")]
    assert out["stats"]["providers"] == len(cards)


def test_filters_type_place_route_free(srv):
    out = call(srv, "irlpls_list_capabilities", {"type": "watch", "place": "FI"})
    rows = [r for f in out["families"] for r in f["provider_rows"]]
    assert any(r.startswith("fintraffic-weathercams") for r in rows) and not any(r.startswith("caltrans") for r in rows)
    assert {f["type"] for f in out["families"]} == {"watch"} and len(json.dumps(out)) < 10_000
    assert all("commission" in f["routes"] for f in call(srv, "irlpls_list_capabilities", {"route": "commission"})["families"])
    free = call(srv, "irlpls_list_capabilities", {"type": "read", "free_only": True})
    assert all(" | free | " in r for f in free["families"] for r in f["provider_rows"])
    assert call(srv, "irlpls_list_capabilities", {"place": "Finland", "type": "watch"})["families"]


def test_place_matches_area_prefix(srv):
    out = call(srv, "irlpls_list_capabilities", {"family": "river-water-level", "place": "US-CA"})
    assert any(r.startswith("usgs-water") for r in out["families"][0]["provider_rows"])
    assert not any(r.startswith("pegelonline") for r in out["families"][0]["provider_rows"])


def test_empty_answer_is_a_result_with_unsupported_and_nearest(srv):
    out = call(srv, "irlpls_list_capabilities", {"family": "ship-positions", "place": "SG"})
    assert out["families"] == [] and "No listed provider covers this" in out["message"]
    assert any("Ship or vessel positions" in u["ask"] for u in out["unsupported"])
    assert out["nearest"] and out["nearest"][0]["family"] == "ship-positions"
    none_free = call(srv, "irlpls_list_capabilities", {"type": "collect", "free_only": True})
    assert none_free["families"] == [] and none_free["nearest"] and none_free["unsupported"]


def test_bad_values_return_error_with_valid_list(srv):
    for args in ({"type": "bogus"}, {"family": "nope"}, {"route": "x"}, {"detail": "all"}):
        out = call(srv, "irlpls_list_capabilities", args)
        assert out["code"] == "invalid_arguments" and out["message"] and out["hint"] and out["valid"]


def test_providers_detail_and_cap(srv):
    out = call(srv, "irlpls_list_capabilities", {"family": "satellite-radar", "detail": "providers"})
    assert set(out["providers"]) == {c for c in catalog.load()["families"][[f["id"] for f in catalog.load()["families"]].index("satellite-radar")]["providers"]}
    assert "adapter" in next(iter(out["providers"].values()))
    big = call(srv, "irlpls_list_capabilities", {"detail": "providers"})
    assert big["truncated"] is True and len(big["providers"]) == catalog.MAX_PROVIDERS


def test_resources(srv):
    async def go():
        res = {str(r.uri) for r in await srv.list_resources()}
        tpl = {t.uri_template for t in await srv.list_resource_templates()}
        idx = json.loads((await srv.read_resource("irlpls://catalog"))[0].content)
        fam = json.loads((await srv.read_resource("irlpls://catalog/family/satellite-radar"))[0].content)
        return res, tpl, idx, fam
    res, tpl, idx, fam = asyncio.run(go())
    assert {"irlpls://catalog", "irlpls://catalog/full"} <= res and "irlpls://catalog/family/{id}" in tpl
    assert idx["families"] and "providers" not in idx and fam["providers"]["umbra"]["id"] == "umbra"


def test_find_providers_verdict_none_is_plain(srv):
    out = call(srv, "irlpls_find_providers", {"need": "current positions of commercial aircraft over Chicago"})
    if isinstance(out, dict):
        assert out["results"] == [] and out["verdict"] == "none" and "No provider fits (verdict none)" in out["message"]
        assert all("fit" in n for n in out["nearest"])
    good = irlpls.find("river level at Comfort, TX now", geocode=False, lat=29.97, lon=-98.9, country_code="us", state="texas")
    assert good[0]["verdict"] in ("strong", "weak")


def test_instructions_mention_tool_and_person_refusal():
    for kw in ("irlpls_list_capabilities", "specific person", "refuses person-targeting"):
        assert kw in mcp_server.INSTRUCTIONS
