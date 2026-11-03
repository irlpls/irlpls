"""PyPI readiness: what the irlpls and irlpls-mcp wheels and sdists contain, their metadata, server.json against the
MCP Registry schema, and the hosted server's rate limiter. Builds with `uv build` into a temp dir (skipped without uv)."""
import asyncio
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import sync_package_data  # noqa: E402

try:  # the public-release leak scanner (private repo only; the public export leaves it out)
    import stage_public  # noqa: E402
    scan, excluded = stage_public.scan, stage_public.excluded
except ImportError:  # public checkout: generic patterns only
    _GENERIC = [("local path", re.compile(r"/Users/[A-Za-z]|/home/[a-z]+/|/private/tmp/")),
                ("tailscale ip", re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
                ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
                ("personal email", re.compile(r"[\w.+-]+@(gmail|yahoo|hotmail|outlook|icloud)\.com\b", re.I))]

    def scan(path, data):
        text = data.decode("utf-8", "replace")
        return [(n, i, line[:80]) for i, line in enumerate(text.splitlines(), 1) for n, rx in _GENERIC
                if rx.search(line) and not (path.endswith("public_suffix_list.dat") and line.startswith("//"))]

    def excluded(path):
        return None

VERSION = "0.1.0"
DATA = {"irlpls/data/cards.json", "irlpls/data/places.json", "irlpls/data/public_suffix_list.dat",
        "irlpls/data/reviewers.json"} | {f"irlpls/data/schema/{p.relative_to(ROOT / 'schema')}"
                                         for p in (ROOT / "schema").rglob("*.schema.json")}
PRIVATE = re.compile(r"(^|/)(feedback|docs|tools|tests|site|src|qa-pages|qa-shots|scratchpad|deploy|cards)/|HANDOFF|"
                     r"\.preview-url|health\.json|published-domains|/published/|\.plist$|\.pem$|\.env")


def _load_serve():
    spec = importlib.util.spec_from_file_location("irlpls_deploy_serve", ROOT / "deploy" / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not installed")
    out = tmp_path_factory.mktemp("dist")
    for src in (ROOT, ROOT / "packages" / "irlpls-mcp"):
        r = subprocess.run([uv, "build", "-q", "--out-dir", str(out), str(src)], capture_output=True, text=True,
                           timeout=300)
        if r.returncode:
            pytest.skip(f"uv build failed (offline?): {r.stderr[-400:]}")
    return out


def _wheel(dist, name):
    (w,) = dist.glob(f"{name}-{VERSION}-py3-none-any.whl")
    return zipfile.ZipFile(w)


def _meta(z):
    (m,) = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
    return Parser().parsestr(z.read(m).decode())


def test_package_data_in_sync():
    assert sync_package_data.main(["--check"]) == 0


def test_irlpls_wheel_holds_the_package_and_only_public_data(dist):
    z = _wheel(dist, "irlpls")
    names = [n for n in z.namelist() if not n.endswith("/")]
    tops = {n.split("/")[0] for n in names}
    assert tops == {"irlpls", f"irlpls-{VERSION}.dist-info"}, tops
    data = {n for n in names if n.startswith("irlpls/data/")}
    assert data == DATA, data ^ DATA
    for want in ("irlpls/__init__.py", "irlpls/mcp_server.py", "irlpls/places.py", "irlpls/paths.py",
                 "irlpls/adapters/usgs_water.py", "irlpls/protocol/client.py"):
        assert want in names
    assert not [n for n in names if n.endswith((".pyc", ".pyo")) or "__pycache__" in n]
    assert not [n for n in names if PRIVATE.search(n)], [n for n in names if PRIVATE.search(n)]


@pytest.mark.parametrize("name", ["irlpls", "irlpls_mcp"])
def test_wheels_and_sdists_carry_no_personal_data(dist, name):
    blobs = {n: z.read(n) for z in [_wheel(dist, name)] for n in z.namelist() if not n.endswith("/")}
    (sd,) = dist.glob(f"{name}-{VERSION}.tar.gz")
    with tarfile.open(sd) as t:
        members = [m for m in t.getmembers() if m.isfile()]
        blobs.update({"sdist:" + m.name.split("/", 1)[1]: t.extractfile(m).read() for m in members})
    hits = []
    for path, data in blobs.items():
        rel = path.removeprefix("sdist:")
        hits += [(path, *h) for h in scan(rel, data)]
        if rel.startswith("irlpls/") and excluded(rel):
            hits.append((path, "excluded from public release", 0, excluded(rel)))
    assert not hits, hits
    sdist_paths = [p.removeprefix("sdist:") for p in blobs if p.startswith("sdist:")]
    assert not [p for p in sdist_paths if PRIVATE.search(p)], [p for p in sdist_paths if PRIVATE.search(p)]


def test_metadata(dist):
    for name, script, target in (("irlpls", "irlpls", "irlpls.cli:main"), ("irlpls_mcp", "irlpls-mcp", "irlpls_mcp:main")):
        z = _wheel(dist, name)
        m = _meta(z)
        assert m["Version"] == VERSION and m["License-Expression"] == "Apache-2.0"
        assert m["Requires-Python"] == ">=3.10"
        assert m["Author-email"] == "irlpls maintainers <hello@irlpls.com>"
        urls = dict(u.split(", ", 1) for u in m.get_all("Project-URL"))
        assert urls["Homepage"] == "https://irlpls.com" and urls["Repository"] == "https://github.com/irlpls/irlpls"
        assert any(c.startswith("Programming Language :: Python :: 3.10") for c in m.get_all("Classifier"))
        ep = next(z.read(n).decode() for n in z.namelist() if n.endswith("entry_points.txt"))
        assert f"{script} = {target}" in ep
    irl = _meta(_wheel(dist, "irlpls"))
    assert not [r for r in (irl.get_all("Requires-Dist") or []) if "extra ==" not in r], "irlpls must stay stdlib only"
    assert any("cryptography" in r and 'extra == "protocol"' in r for r in irl.get_all("Requires-Dist"))
    assert "irlpls-mcp" not in next(z.read(n).decode() for z in [_wheel(dist, "irlpls")]
                                    for n in z.namelist() if n.endswith("entry_points.txt"))
    mcp = _meta(_wheel(dist, "irlpls_mcp"))
    assert set(mcp.get_all("Requires-Dist")) == {f"irlpls=={VERSION}", "mcp<3,>=2"}
    # MCP Registry ownership check: the PyPI description must name the server.json server
    server = json.loads((ROOT / "server.json").read_text())
    assert f"mcp-name: {server['name']}" in mcp.get_payload()


def test_server_json_matches_the_registry_schema_and_the_packages():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "tests" / "fixtures" / "mcp-registry" / "server.schema.2025-12-11.json").read_text())
    server = json.loads((ROOT / "server.json").read_text())
    assert server["$schema"] == schema["$id"]
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(server)
    assert len(server["description"]) <= 100 and server["version"] == VERSION
    (pkg,) = server["packages"]
    assert (pkg["registryType"], pkg["identifier"], pkg["version"], pkg["runtimeHint"], pkg["transport"]["type"]) == \
        ("pypi", "irlpls-mcp", VERSION, "uvx", "stdio")
    (remote,) = server["remotes"]
    assert remote == {"type": "streamable-http", "url": "https://mcp.irlpls.com/mcp"}
    for p in (ROOT / "pyproject.toml", ROOT / "packages" / "irlpls-mcp" / "pyproject.toml"):
        assert f'version = "{VERSION}"' in p.read_text()
    npm = json.loads((ROOT / "packages" / "npm-irlpls-mcp" / "package.json").read_text())
    assert npm["version"] == VERSION and npm["mcpName"] == server["name"]
    import irlpls
    assert irlpls.__version__ == VERSION


def test_rate_limit_buckets():
    serve = _load_serve()
    now = [0.0]
    rl = serve.RateLimit(per_min=60, burst=3, global_per_min=600, clock=lambda: now[0])
    assert [rl.check("a") for _ in range(3)] == [0, 0, 0]
    assert rl.check("a") > 0          # burst spent
    assert rl.check("b") == 0         # another client is not affected
    now[0] += 1.0                     # one token per second
    assert rl.check("a") == 0 and rl.check("a") > 0
    g = serve.RateLimit(per_min=60, burst=100, global_per_min=6, clock=lambda: now[0])   # global burst = max(1, 100)
    assert sum(g.check(f"c{i}") == 0 for i in range(150)) == 100


def test_guard_answers_healthz_and_429_and_passes_through():
    serve = _load_serve()
    calls, sent = [], []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    async def send(msg):
        sent.append(msg)

    g = serve.Guard(app, limit=serve.RateLimit(per_min=60, burst=2, global_per_min=600), header="fly-client-ip")
    scope = lambda path, ip="1.2.3.4", method="POST": {"type": "http", "path": path, "method": method,  # noqa: E731
                                                       "headers": [(b"fly-client-ip", ip.encode())], "client": ("10.0.0.1", 1)}
    asyncio.run(g(scope("/healthz", method="GET"), None, send))
    assert sent[0]["status"] == 200 and not calls
    sent.clear()
    for _ in range(2):
        asyncio.run(g(scope("/mcp"), None, send))
    assert calls == ["/mcp", "/mcp"] and not sent
    asyncio.run(g(scope("/mcp"), None, send))
    assert sent[0]["status"] == 429 and dict(sent[0]["headers"])[b"retry-after"]
    sent.clear()
    asyncio.run(g(scope("/mcp", ip="5.6.7.8"), None, send))   # keyed on the proxy's client header
    assert calls[-1] == "/mcp" and not sent
    assert serve.client_key({"headers": [], "client": ("9.9.9.9", 1)}, header="") == "9.9.9.9"


def test_offline_gazetteer():
    from irlpls import places
    assert places.lookup("Munich")["country_code"] == "de"
    p = places.lookup("Portland, Maine")
    assert (p["name"], p["subdivision"], p["state"]) == ("Portland, ME", "US-ME", "maine")
    assert places.lookup("Paris, Texas") is None      # a same-named city elsewhere is not a guess
    assert places.lookup("Comfort, TX") is None       # too small for the table
    assert places.lookup("Washington")["name"] == "Washington, DC"
    assert places.lookup("") is None
