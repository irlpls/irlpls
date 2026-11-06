"""Security review H3/H4 and CI hardening: hostile card text must not become a live link, a forged llms.txt section,
a script break-out or a terminal escape; workflows are least-privilege and SHA-pinned."""
import copy
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402
from irlpls import validate  # noqa: E402
from irlpls.safe_text import is_safe_url, safe_href, sanitize_card, strip_controls  # noqa: E402

jsonschema = pytest.importorskip("jsonschema")
SCHEMAS = [ROOT / "schema" / "capture-card.schema.json", ROOT / "irlpls" / "data" / "schema" / "capture-card.schema.json"]


def base(tmp_path):
    card = json.loads((ROOT / "cards" / "usgs-water.json").read_text())
    (tmp_path / "card.json").write_text(json.dumps(card))
    return card


def schema_errors(card, schema_path):
    return list(jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).iter_errors(card))


def test_schema_copies_identical():
    assert SCHEMAS[0].read_text() == SCHEMAS[1].read_text()


@pytest.mark.parametrize("schema", SCHEMAS)
def test_real_card_still_valid(tmp_path, schema):
    assert not schema_errors(base(tmp_path), schema)


@pytest.mark.parametrize("path,value", [
    (("provider_url",), "javascript:alert(1)"),
    (("api", "docs_url"), "javascript:alert(1)"),
    (("api", "docs_url"), "http://example.com/"),
    (("api", "docs_url"), "https://exa mple.com/"),
    (("api", "docs_url"), "https://example.com/\nx"),
    (("sources", 0, "url"), "data:text/html,<script>1</script>"),
    (("name",), "Evil\n## Injected section"),
    (("name",), "Evil\x1b[2J"),
    (("summary",), "x" * 5000),
    (("name",), "x" * 500),
])
def test_hostile_values_rejected_everywhere(tmp_path, path, value):
    card = base(tmp_path)
    o = card
    for k in path[:-1]:
        o = o.setdefault(k, {}) if isinstance(k, str) else o[k]
    if isinstance(path[-1], str):
        o[path[-1]] = value
    for s in SCHEMAS:
        assert schema_errors(card, s), (s, path)
    errs = [p for p in validate.problems(card) if p[0] == "error"]
    assert errs, path
    assert validate.text_problems(card), path  # and the rule holds without jsonschema installed


def test_safe_text_helpers():
    assert strip_controls("a\x1b[31mred\x1b[0m\nb\x1b]0;t\x07c") == "ared bc"
    assert strip_controls("x y\x00z") == "x yz".replace("yz", "y z")
    assert strip_controls("l1\r\nl2", multiline=True) == "l1\nl2"
    assert is_safe_url("https://a.example/p?q=1") and not is_safe_url("http://a.example/")
    assert safe_href("http://a.example/") and not safe_href("javascript:alert(1)")
    assert not safe_href("JaVaScRiPt:alert(1)") and not safe_href("https://a.example/ onclick=x")


def hostile(tmp_path):
    c = copy.deepcopy(base(tmp_path))
    c["name"] = 'Evil\n## Injected\n- [x](/y) "><script>alert(1)</script>'
    c["short"] = "short </script><!--   \x1b[2J"
    c["provider_url"] = "javascript:alert(1)"
    c.setdefault("api", {})["docs_url"] = " javascript:alert(2)"
    c["sources"] = [{"claim": "c", "url": "javascript:alert(3)"}]
    return c


def test_card_page_never_links_javascript(tmp_path):
    c = hostile(tmp_path)
    html_ = build.card_page(c)  # without sanitising: the renderer alone must refuse
    assert not re.search(r'href="\s*javascript:', html_, re.I)
    assert "<script>alert(1)" not in html_


def test_sanitize_card_drops_unsafe_urls_and_controls(tmp_path):
    c = sanitize_card(hostile(tmp_path))
    assert "provider_url" not in c and "url" not in c["sources"][0] and "docs_url" not in c["api"]
    assert "\n" not in c["name"] and "\x1b" not in c["short"]
    assert not re.search(r'href="\s*javascript:', build.card_page(c), re.I)


def test_llms_txt_cannot_forge_a_section(tmp_path):
    c = hostile(tmp_path)
    txt = build.LLMS.format(ids=build.llms_lines([c]))
    assert "\n## Injected" not in txt and "\x1b" not in txt
    assert txt.count("\n- [") == build.LLMS.format(ids="").count("\n- [") + 1


def test_json_in_script_cannot_break_out():
    out = build._dump({"a": "</script><!-- <script>    "})
    for bad in ("</script", "<!--", "<script", " ", " "):
        assert bad not in out.lower()
    assert json.loads(out)["a"] == "</script><!-- <script>    "


def test_badge_is_valid_xml_with_control_chars():
    import xml.dom.minidom
    svg = build.badge_svg("Bad\x00\x1bName\n", {"last_read": "2026-11-01"}, "live")
    xml.dom.minidom.parseString(svg)


def test_cli_strips_escape_sequences(capsys):
    from irlpls import cli
    cli.print("\x1b[2J\x1b]0;pwn\x07hello\x00")
    assert capsys.readouterr().out == "hello\n"


def test_workflows_least_privilege_and_pinned():
    wfs = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert wfs
    for f in wfs:
        txt = f.read_text()
        assert re.search(r"^permissions:", txt, re.M), f"{f.name}: no top-level permissions"
        for m in re.finditer(r"uses:\s*(\S+)", txt):
            ref = m.group(1)
            if ref.startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{f.name}: {ref} is not pinned to a commit SHA"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^permissions:\n  contents: read", ci, re.M)
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    top = rel.split("\njobs:")[0]
    assert "id-token" not in top  # OIDC only on the publish jobs
