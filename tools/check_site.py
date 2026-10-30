"""Check a public irlpls build, local or live: every internal link resolves, and nothing internal ships.

    python3 tools/check_site.py site                    # a local `python3 build.py --public` output
    python3 tools/check_site.py https://irlpls.com      # the live site (GET only, ~100 requests)

Crawls every page reachable from / and sitemap.xml, following href/src on the same site, plus asset paths written as
strings in scripts and styles (an <img src> built in JS, a url() in CSS). Fails (exit 1) on: a broken internal link or
asset, a reference into a private-only folder (media/live/: the private refresh job's gitignored snapshots), a
prototype or preview-only file (pitch/, headlines.html, fb.js), or a preview host or local path in any text file.
Stdlib only.
"""
import html.parser
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

FORBIDDEN_RX = re.compile(r"(^|/)(pitch/|headlines\.html$|_*fb\.js$|(portal|decisions|board)(\.html|/)?$)")
LEAK_RX = re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b|\.ts\.net\b|//(localhost|127\.0\.0\.1)\b"
                     r"|(?<![\w.-])(?:/Users/[A-Za-z]|/home/[a-z]|/private/tmp/)")
# a site-local asset named in a script or style: quoted or in url(), relative or root-relative, never with a scheme
ASSET_RX = re.compile(r"""(?:["'`]|url\()((?:\.{0,2}/)?(?:[\w.-]+/)*[\w-][\w.-]*\.(?:jpe?g|png|gif|webp|avif|svg|mp4|webm|mov|json|js|css|woff2?))(?=["'`)?#])""")
PRIVATE_RX = re.compile(r"(^|/)media/live/")  # gitignored, private refresh job output
TEXT = (".html", ".js", ".css", ".json", ".txt", ".md", ".xml", ".svg", "")


def asset_tail(text, at):
    """A quoted path that is only the tail of another URL ('…'+B+'/x.json') or a suffix test (endsWith('x.json')), not an asset."""
    before = text[max(0, at - 12):at].rstrip()
    return before.endswith("+") or re.search(r"(endsWith|includes|indexOf|test)\($", before) is not None


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src") and v:
                self.found.append(v)


class Local:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()

    def get(self, path):  # -> (status, bytes)
        f = self.root / path.lstrip("/")
        if f.is_dir() or path.endswith("/"):
            f = f / "index.html"
        if not f.is_file() and not f.suffix and (f.parent / (f.name + ".html")).is_file():
            f = f.parent / (f.name + ".html")  # Cloudflare Pages serves /x for x.html
        return (200, f.read_bytes()) if f.is_file() else (404, b"")

    def all_files(self):
        return ["/" + str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()]


class Live:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": "irlpls-check-site/1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as x:
            return x.code, b""
        except Exception as x:  # DNS, TLS, timeout
            return f"error {type(x).__name__}", b""

    def all_files(self):
        return []


def main(argv):
    if not argv:
        sys.exit(__doc__)
    target = argv[0]
    live = target.startswith("http")
    site = Live(target) if live else Local(target)
    host = urllib.parse.urlparse(target).netloc if live else None
    problems, seen = [], {}

    status, body = site.get("/sitemap.xml")
    queue = ["/"] + [urllib.parse.urlparse(u).path for u in re.findall(r"<loc>([^<]+)</loc>", body.decode(errors="replace"))]
    refs = {}
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        status, body = site.get(path)
        seen[path] = status
        if status != 200:
            continue
        text = body.decode("utf-8", errors="replace")
        if pathlib.PurePosixPath(path).suffix in TEXT:
            problems += [f"{path}: leak {m.group(0)}" for m in LEAK_RX.finditer(text)]
        page = path.endswith((".html", "/")) or "." not in path.rsplit("/", 1)[-1]
        found = []
        if page or path.endswith((".js", ".css")):
            found = [m.group(1) for m in ASSET_RX.finditer(text) if not asset_tail(text, m.start())]
            problems += [f"{path}: references private-only {v}" for v in found if PRIVATE_RX.search(v)]
        if page:
            p = Links()
            p.feed(text)
            found += p.found
        for v in found:
            u = urllib.parse.urlparse(urllib.parse.urljoin("https://x" + path, v.strip()))
            if u.scheme not in ("http", "https") or (u.netloc not in ("x", host) and not (host and u.netloc == "www." + host)):
                continue
            t = urllib.parse.unquote(u.path) or "/"
            refs.setdefault(t, path)
            queue.append(t)

    for t, st in sorted(seen.items()):
        if st != 200:
            problems.append(f"broken: {t} ({st}), linked from {refs.get(t, 'sitemap')}")
    files = site.all_files() or list(seen)
    for f in files:
        if FORBIDDEN_RX.search(f):
            problems.append(f"internal file shipped: {f}")
    if not live:  # every text file, not only the reachable ones
        for f in files:
            if f not in seen and pathlib.PurePosixPath(f).suffix in TEXT[:-1]:
                problems += [f"{f}: leak {m.group(0)}" for m in LEAK_RX.finditer(site.get(f)[1].decode(errors="replace"))]
    else:
        for f in ("/pitch/", "/pitch/provider-flow.html", "/headlines.html", "/__fb.js", "/fb.js"):
            st, _ = site.get(f)
            if st == 200:  # site/404.html exists, so Pages answers 404 here, never the home page
                problems.append(f"internal file served: {f}")

    print(f"checked {len(seen)} URLs on {target}: {len(problems)} problem(s)")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
