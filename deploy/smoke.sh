#!/usr/bin/env bash
# Local smoke test for the hosted read-only MCP server. Localhost only; makes no network calls beyond 127.0.0.1.
# Starts deploy/serve.py (the Docker entrypoint) with --read-only on 127.0.0.1, then checks over streamable HTTP:
# initialize, tools/list is exactly the 7 planning tools, no irlpls_submit, no protocol tools, find_providers answers,
# and the server log holds no access lines, no client IP and none of the request text. Also checks the two guards:
# a non-loopback bind without --allow-remote is refused, and serve.py refuses to start without --read-only.
# Then starts the full (not read-only) server on 127.0.0.1 only to count its tools (14); it calls no tool there.
# Usage: deploy/smoke.sh [python]   (python needs irlpls[mcp] installed; default: python3). Run from anywhere.
set -u
PY=${1:-python3}; PORT=${PORT:-8791}; URL=http://127.0.0.1:$PORT/mcp
ROOT=$(cd "$(dirname "$0")/.." && pwd); cd "$ROOT"
MARK="smokemarker$$"   # unique text sent in the request; it must not appear in the server log
LOG=$(mktemp); BODY=$(mktemp); PID=
trap '[ -n "$PID" ] && kill $PID 2>/dev/null; rm -f "$BODY"' EXIT
fail=0; ok(){ echo "PASS $1"; }; no(){ echo "FAIL $1"; fail=1; }
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
post(){ curl -s "${H[@]}" ${SID:+-H "Mcp-Session-Id: $SID"} -d "$1" "$URL"; }
names(){ "$PY" -c 'import sys,json
t=sys.stdin.read(); t=next((l[5:] for l in t.splitlines() if l.startswith("data:")), t)
print(" ".join(sorted(x["name"] for x in json.loads(t)["result"]["tools"])))'; }
start(){ "$PY" "$@" --transport streamable-http --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 & PID=$!
  for i in $(seq 60); do curl -s -o /dev/null "http://127.0.0.1:$PORT/" && return; sleep 0.25; done; }
init(){ HDR=$(curl -s -D - -o "$BODY" "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' "$URL")
  SID=$(echo "$HDR" | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
  post '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null; }

echo "== read-only server (deploy/serve.py --read-only, 127.0.0.1:$PORT)"
start deploy/serve.py --read-only; init
grep -q serverInfo "$BODY" && ok initialize || no "initialize: $(head -c 300 "$BODY")"
RO=$(post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | names)
echo "tools: $RO"
WANT="irlpls_answer irlpls_choose_provider irlpls_find_providers irlpls_get_card irlpls_get_job irlpls_list_capabilities irlpls_request"
[ "$RO" = "$WANT" ] && ok "tools/list: exactly the 7 planning tools" || no "tools/list: got [$RO]"
case " $RO " in *" irlpls_submit "*) no "irlpls_submit exposed";; *) ok "no irlpls_submit";; esac
P=""; for t in discover quote order status deliver cancel; do case " $RO " in *" irlpls_$t "*) P="$P irlpls_$t";; esac; done
[ -z "$P" ] && ok "no protocol tools (discover/quote/order/status/deliver/cancel)" || no "protocol tools exposed:$P"
F=$(post '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"irlpls_find_providers","arguments":{"need":"river level at Comfort, TX now '"$MARK"'","limit":3}}}')
if echo "$F" | grep -q '"isError":true' || ! echo "$F" | grep -q '"result"'; then no "find_providers: $(echo "$F" | head -c 300)"
else ok "find_providers: $(echo "$F" | grep -o '\\"id\\": *\\"[a-z0-9-]*\\"' | head -3 | tr -d '\\"' | tr '\n' ' ')"; fi
R=$(post '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"irlpls_order","arguments":{"order_request":{}}}}')
echo "$R" | grep -qi 'unknown tool\|not found\|"isError":true\|"error"' && ok "calling irlpls_order is rejected" || no "irlpls_order call: $(echo "$R" | head -c 200)"
"$PY" -c 'print("{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"ping\",\"pad\":\"" + "x" * 100000 + "\"}")' >"$BODY"
c=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" ${SID:+-H "Mcp-Session-Id: $SID"} --data-binary @"$BODY" "$URL")
[ "$c" = 413 ] && ok "100 KB body rejected (413)" || no "100 KB body got $c"
kill $PID; wait $PID 2>/dev/null; PID=
grep -q "$MARK" "$LOG" && no "request text found in server log" || ok "request text not in server log"
grep -Eq '127\.0\.0\.1:[0-9]+ - |"POST /mcp' "$LOG" && no "access log lines present" || ok "no access-log lines (no client IP, path or status)"
echo "server log, complete ($(wc -l <"$LOG" | tr -d ' ') lines):"; sed 's/^/  | /' "$LOG"

echo "== guards"
"$PY" -m irlpls.mcp_server --read-only --transport streamable-http --host 0.0.0.0 --port "$PORT" >"$LOG" 2>&1 & G=$!
sleep 2; if kill -0 $G 2>/dev/null; then kill $G; no "0.0.0.0 without --allow-remote started"
else grep -q "refusing to bind" "$LOG" && ok "0.0.0.0 without --allow-remote refused" || no "guard: $(tail -2 "$LOG")"; fi
"$PY" deploy/serve.py --transport streamable-http --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1
grep -q "refusing to start without --read-only" "$LOG" && ok "serve.py refuses without --read-only" || no "serve.py guard: $(tail -2 "$LOG")"

echo "== full server, for comparison (127.0.0.1, tools/list only, no tool called)"
start -m irlpls.mcp_server; init
FULL=$(post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | names)
n=$(echo "$FULL" | wc -w | tr -d ' '); echo "tools ($n): $FULL"
[ "$n" = 14 ] && ok "full server registers 14 tools" || no "full server registers $n tools"
kill $PID; wait $PID 2>/dev/null; PID=; rm -f "$LOG"

[ $fail = 0 ] && echo "SMOKE OK" || { echo "SMOKE FAILED"; exit 1; }
