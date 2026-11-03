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
    # replace only the contents and the body; the page's head, title block and footer are kept as they are
    a, b = '<main id="main" class="std">\n', "\n</main>"
    i = page.index(a) + len(a)
    page = page[:i] + main_html + page[page.index(b, i):]
    t0 = page.index('<nav aria-label="Contents"><ol>') + len('<nav aria-label="Contents"><ol>')
    page = page[:t0] + toc + page[page.index("</ol></nav>", t0):]
    page = re.sub(r"<title>.*?</title>", f"<title>irlpls standard {version}</title>", page, count=1)
    OUT.write_text(page)
    print(f"standard {version}: {len(main_html)} chars")


if __name__ == "__main__":
    main()
