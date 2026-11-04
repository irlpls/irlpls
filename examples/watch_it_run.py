"""Watch irlpls run, step by step, in your browser. Nothing is published, ordered or paid.

    python3 examples/watch_it_run.py            # then open http://127.0.0.1:8431

A local page (stdlib only). Each step shows the exact command, a Run button, and the real output with its timing; the
MCP step shows the actual JSON-RPC messages an assistant would exchange with `irlpls-mcp`. Step 1 installs the wheels
from dist/ into a throwaway virtualenv (build them first: `uv build`, and in packages/irlpls-mcp `uv build --out-dir ../../dist`).
Live steps use public keyless services, so they need internet. Ctrl-C stops the page and deletes the virtualenv.
"""
import atexit
import http.server
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PORT = 8431
TMP = pathlib.Path(tempfile.mkdtemp(prefix="irlpls-watch-"))
VENV = TMP / "v"
CLI = str(VENV / "bin" / "irlpls")
MCP = str(VENV / "bin" / "irlpls-mcp")
VPY = str(VENV / "bin" / "python")
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))
LOCK = threading.Lock()

# each step: id, title, why (one line), argv (shown and run; {CLI} etc. are the venv's), or "mcp" for the protocol demo
STEPS = [
    {"title": "Install the built packages into a clean virtualenv",
     "why": "What a stranger would get from PyPI, built from this checkout instead. Your own Python is untouched.",
     "kind": "install"},
    {"title": "Check the version", "why": "The CLI is installed and runs.", "argv": [CLI, "--version"]},
    {"title": "Rank providers for 'river level in Bristol'",
     "why": "Planning only: no network, no order. Fit is for this request; the proof columns say what each provider claims.",
     "argv": [CLI, "find", "river level in Bristol"]},
    {"title": "A live answer: what is reporting now near Comfort, TX",
     "why": "A real call to USGS water services. Photos link to the real image.",
     "argv": [CLI, "live", "usgs-water", "--place", "Comfort, TX"]},
    {"title": "Plan 'traffic camera in London'",
     "why": "Should pick the free TfL cameras and show the exact call it would make; it makes no call and places no order.",
     "argv": [CLI, "request", "traffic camera in London"]},
    {"title": "Plan something irlpls can't do: 'weather on Mars'",
     "why": "It should say so and ask for an Earth place, not guess one.",
     "argv": [CLI, "request", "weather on Mars"]},
    {"title": "An empty ask", "why": "A clear one-line message, no traceback.", "argv": [CLI, "request", ""]},
    {"title": "The MCP server, as an assistant sees it",
     "why": "irlpls-mcp over stdio, read-only like the hosted server (plan and quote, never order). The messages below are the real JSON-RPC exchange.",
     "kind": "mcp"},
]


def shell_quote(argv):
    return " ".join(a if re.fullmatch(r"[\w./:=@%+,-]+", a) else "'" + a.replace("'", "'\\''") + "'" for a in argv)


def shown(argv):
    return shell_quote(argv).replace(str(VENV) + "/bin/", "")


def run_argv(argv, timeout=120):
    t = time.time()
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {"cmd": shown(argv), "out": (r.stdout + r.stderr).rstrip(), "code": r.returncode,
            "ms": int((time.time() - t) * 1000)}


def do_install():
    wheels = sorted(DIST.glob("*.whl"))
    if len(wheels) < 2:
        return {"cmd": "(no wheels in dist/)", "out": "Build them first: `uv build` in the repo root and "
                "`uv build --out-dir ../../dist` in packages/irlpls-mcp.", "code": 1, "ms": 0}
    t = time.time()
    if VENV.exists():
        shutil.rmtree(VENV)
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    argv = [VPY, "-m", "pip", "install", "-q", "--disable-pip-version-check", *map(str, wheels)]
    r = subprocess.run(argv, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip() or "installed: " + ", ".join(w.name for w in wheels)
    return {"cmd": f"python -m venv v && pip install {' '.join(w.name for w in wheels)}", "out": out,
            "code": r.returncode, "ms": int((time.time() - t) * 1000)}


def do_mcp():
    proc = subprocess.Popen([MCP, "--read-only"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "watch_it_run", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "irlpls_request", "arguments": {"need": "river level at Comfort, TX now"}}},
    ]
    ex = []
    try:
        for m in msgs:
            t = time.time()
            proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            recv = None
            if "id" in m:
                line = proc.stdout.readline()
                recv = json.loads(line) if line else None
            ex.append({"send": m, "recv": summarize(m, recv), "ms": int((time.time() - t) * 1000)})
    finally:
        proc.terminate()
    return {"cmd": "irlpls-mcp --read-only   (speaking JSON-RPC on stdin/stdout)", "exchanges": ex, "code": 0,
            "ms": sum(e["ms"] for e in ex)}


def summarize(sent, recv):
    """The reply as sent, except tools/list is cut to names (the full schemas are long)."""
    if recv is None:
        return None
    if sent.get("method") == "tools/list" and "result" in recv:
        return {"id": recv["id"], "result": {"tools": [t["name"] for t in recv["result"]["tools"]],
                                              "(note)": "full input schemas omitted here"}}
    return recv


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json"):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self.send(PAGE.replace("__STEPS__", json.dumps([
            {"title": s["title"], "why": s["why"], "cmd": shown(s["argv"]) if "argv" in s else
             ("python -m venv v && pip install dist/*.whl" if s["kind"] == "install" else "irlpls-mcp --read-only (stdio)")}
            for s in STEPS])), "text/html")

    def do_POST(self):
        i = int(self.path.rsplit("=", 1)[-1])
        s = STEPS[i]
        with LOCK:
            try:
                if s.get("kind") == "install":
                    res = do_install()
                elif not VENV.exists():
                    res = {"cmd": "", "out": "Run step 1 first (it installs the packages).", "code": 1, "ms": 0}
                elif s.get("kind") == "mcp":
                    res = do_mcp()
                else:
                    res = run_argv(s["argv"])
            except Exception as e:  # noqa: BLE001
                res = {"cmd": "", "out": f"{type(e).__name__}: {e}", "code": 1, "ms": 0}
        self.send(json.dumps(res))


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watch irlpls run</title>
<style>
:root{--bg:#fbfcfe;--fg:#101418;--mut:#5b6574;--line:#e3e8ef;--ok:#059669;--bad:#dc2626;--blue:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1318;--fg:#eef1f5;--mut:#9aa5b4;--line:#242c37}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Inter,system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:28px 16px 80px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--mut);margin:0 0 20px}.bar{display:flex;gap:10px;margin-bottom:18px}
button{font:600 14px Inter,system-ui;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:999px;padding:9px 18px;cursor:pointer}
button.p{background:var(--blue);color:#fff;border-color:var(--blue)}button:disabled{opacity:.5;cursor:default}
.step{border:1px solid var(--line);border-radius:16px;padding:16px;margin:0 0 12px}
.step h2{font-size:17px;margin:0 0 2px}.n{color:var(--mut);font-weight:500;margin-right:6px}.why{color:var(--mut);margin:0 0 10px;font-size:14.5px}
pre{margin:8px 0 0;padding:12px;border-radius:10px;background:rgba(127,140,160,.1);overflow:auto;font:13px/1.45 ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
.cmd{display:flex;gap:8px;align-items:center}.cmd pre{flex:1;margin:0}.meta{font-size:13px;color:var(--mut);margin-top:8px}
.ok{color:var(--ok)}.bad{color:var(--bad)}img{max-width:100%;border-radius:10px;margin-top:8px;display:block}
.ex{margin-top:8px}.ex b{font-size:12.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
</style><main><h1>Watch irlpls run</h1>
<p class="sub">Each step shows the exact command, then what it really printed. Runs on this computer. Nothing is published, ordered or paid.</p>
<div class="bar"><button class="p" id="all">Run all steps</button></div><div id="steps"></div></main>
<script>
var S=__STEPS__,box=document.getElementById('steps');
function esc(t){return String(t).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
S.forEach(function(s,i){var d=document.createElement('section');d.className='step';
d.innerHTML='<h2><span class="n">'+(i+1)+'.</span>'+esc(s.title)+'</h2><p class="why">'+esc(s.why)+'</p>'+
'<div class="cmd"><pre>$ '+esc(s.cmd)+'</pre><button data-i="'+i+'">Run</button></div><div class="res"></div>';box.appendChild(d)});
function render(i,r){var el=box.children[i].querySelector('.res'),h='';
if(r.exchanges){r.exchanges.forEach(function(e){h+='<div class="ex"><b>assistant sends</b><pre>'+esc(JSON.stringify(e.send,null,1))+'</pre>'+
(e.recv?'<b>irlpls-mcp replies ('+e.ms+' ms)</b><pre>'+esc(JSON.stringify(e.recv,null,1).slice(0,3500))+'</pre>':'<b>(no reply expected)</b>')+'</div>'})}
else{h='<pre>'+esc(r.out||'(no output)')+'</pre>';(r.out.match(/https?:\/\/\S+\.(?:jpg|jpeg|png)/g)||[]).slice(0,1).forEach(function(u){h+='<img src="'+u+'" alt="photo from the reading">'})}
h+='<div class="meta '+(r.code?'bad':'ok')+'">'+(r.code?'exit '+r.code:'ok')+' · '+r.ms+' ms</div>';el.innerHTML=h}
function run(i){var b=box.children[i].querySelector('button');b.disabled=true;b.textContent='Running…';
return fetch('/run?step='+i,{method:'POST'}).then(function(r){return r.json()}).then(function(r){render(i,r);b.disabled=false;b.textContent='Run again';return r})}
box.addEventListener('click',function(e){var i=e.target.getAttribute&&e.target.getAttribute('data-i');if(i!==null&&i!==undefined)run(+i)});
document.getElementById('all').onclick=function(){var b=this;b.disabled=true;var p=Promise.resolve();
S.forEach(function(s,i){p=p.then(function(){return run(i)})});p.then(function(){b.disabled=false})};
</script></html>"""

if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Watch irlpls run: http://127.0.0.1:{PORT}   (Ctrl-C to stop; the throwaway virtualenv is deleted on exit)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
