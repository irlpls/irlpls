"""Render docs/STANDARD.md into src/standard.html, keeping the page's head, header and footer.

Run: uv run --with markdown python tools/build_standard.py
"""
import html
import pathlib
import re

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "docs" / "STANDARD.md", ROOT / "src" / "standard.html"


def tables(h):
    """Wrap each table for the mobile layout and label every cell with its column, as the stylesheet expects."""
    def one(m):
        t = m.group(0)
        heads = [re.sub("<[^>]+>", "", x) for x in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
        rows = re.split(r"(<tr>)", t)
        def label(row):
            i = iter(heads)
            return re.sub(r"<td([^>]*)>", lambda c: f'<td data-l="{html.escape(next(i, ""))}"{c.group(1)}>', row)
        t = "".join(label(r) if "<td" in r else r for r in rows)
        return f'<div class="tbl c{min(len(heads), 3)}">{t}</div>'
    return re.sub(r"<table>.*?</table>", one, h, flags=re.S)


def main():
    md = SRC.read_text()
    title, body = md.split("\n", 1)
    version = title.removeprefix("# irlpls ").strip()
    conv = markdown.Markdown(extensions=["tables", "fenced_code", "toc"])
    main_html = tables(conv.convert(body))
    toc = "".join(f'<li><a href="#{t["id"]}">{html.escape(t["name"])}</a></li>' for t in conv.toc_tokens[0]["children"]) \
        if conv.toc_tokens and conv.toc_tokens[0]["level"] == 1 else "".join(f'<li><a href="#{t["id"]}">{html.escape(t["name"])}</a></li>' for t in conv.toc_tokens)
    page = OUT.read_text()
    head, rest = page.split('<div class="spec-wrap">', 1)
    tail = rest[rest.index('<footer class="spec-foot">'):]
    head = re.sub(r"<title>.*?</title>", f"<title>irlpls standard {version}</title>", head)
    head = re.sub(r'(<meta name="description" content=")[^"]*', rf"\1The irlpls {version} specification: the Capture Card, discovery, the Request, Policy and Job messages, matching and conformance.", head)
    OUT.write_text(head + '<div class="spec-wrap">\n'
                   f'<div class="spec-head"><h1>irlpls standard</h1><p>{version} · draft · private preview</p></div>\n'
                   f'<details class="toc" id="toc"><summary>Contents</summary><nav aria-label="Contents"><ol>{toc}</ol></nav></details>\n'
                   f'<main id="main" class="std">\n{main_html}\n</main>\n</div>\n' + tail)
    print(f"standard {version}: {len(main_html)} chars")


if __name__ == "__main__":
    main()
