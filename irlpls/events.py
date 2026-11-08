"""Append-only, hash-chained event log (registry.ndjson). Stdlib only.

One JSON object per line: {ts, action, domain, card_id, version_sha256, actor, evidence, prev_hash, hash}.
`prev_hash` is the `hash` of the previous line ("0"*64 for the first); `hash` is the sha256 of the canonical JSON of
the line without its own `hash` field. Editing, deleting or reordering any line breaks the chain at that point and
`verify_chain` reports where. Appends take an exclusive file lock and fsync, so concurrent writers cannot interleave.
Actions: submitted, crawled, gated, accepted, rejected.
"""
import hashlib
import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ACTIONS = ("submitted", "crawled", "gated", "accepted", "rejected")
GENESIS = "0" * 64


class EventError(Exception):
    pass


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def line_hash(event):
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


def _last_hash(f):
    f.seek(0)
    last = None
    for raw in f:
        if raw.strip():
            last = raw
    if last is None:
        return GENESIS
    try:
        return json.loads(last)["hash"]
    except (ValueError, KeyError, TypeError):
        raise EventError("log tail is not a valid event; run verify_chain")


def append(path, action, domain=None, card_id=None, version_sha256=None, actor="crawler", evidence=None, ts=None):
    """Append one event atomically; returns the event dict as written."""
    if action not in ACTIONS:
        raise EventError(f"unknown action {action!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as raw:
        if fcntl:
            fcntl.flock(raw, fcntl.LOCK_EX)
        try:
            with open(path, "r", encoding="utf-8") as rd:
                prev = _last_hash(rd)
            ev = {"ts": ts if ts is not None else int(time.time()), "action": action, "domain": domain,
                  "card_id": card_id, "version_sha256": version_sha256, "actor": actor,
                  "evidence": evidence or [], "prev_hash": prev}
            ev["hash"] = line_hash(ev)
            raw.write((_canon(ev) + "\n").encode("utf-8"))
            raw.flush()
            os.fsync(raw.fileno())
        finally:
            if fcntl:
                fcntl.flock(raw, fcntl.LOCK_UN)
    return ev


def read(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def verify_chain(path):
    """Return (ok, problems). problems: [{line, error}] with 1-based line numbers."""
    p = Path(path)
    if not p.exists():
        return True, []
    prev, problems = GENESIS, []
    for n, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not l.strip():
            problems.append({"line": n, "error": "blank line"})
            continue
        try:
            ev = json.loads(l)
        except ValueError:
            problems.append({"line": n, "error": "not JSON"})
            continue
        if not isinstance(ev, dict) or "hash" not in ev:
            problems.append({"line": n, "error": "missing hash"})
            continue
        if ev.get("prev_hash") != prev:
            problems.append({"line": n, "error": "prev_hash does not match previous line"})
        if line_hash(ev) != ev["hash"]:
            problems.append({"line": n, "error": "hash does not match content"})
        prev = ev["hash"]
    return not problems, problems
