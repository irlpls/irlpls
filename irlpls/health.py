"""The daily read check, read side: did one minimal real read through each provider's adapter work last time?

tools/healthcheck.py writes irlpls/data/health.json once a day (LaunchAgent com.mo.irlpls-healthcheck); build.py
shows one line per card page (merged with the landing's 30-minute check, src/media/live/"degraded" = the provider answered but the reply is empty or lacks the fields the adapter's own parse always emits where
a reading is normally there (tools/healthcheck.py EXPECT). We cannot tell a provider that renamed its keys from one that
simply has nothing now, so the wording never says "down" or "changed": "answered, but not as expected". ok is False.

health.json: see latest), catalog.json carries providers[id].health, and the broker warns when the provider a plan
picks failed its last check. The file is generated and gitignored (it changes every day); without it nothing is shown
and nothing is warned. IRLPLS_HEALTH_FILE points somewhere else (tests).

health.json:
  {"irlpls_health": 1, "checked_at", "duration_s", "summary": {checked, ok, failed, skipped},
   "providers": {card_id: {"result": "ok"|"degraded"|"failed"|"skipped", "ok", "status" (HTTP status, or null), "error" (error
                 class, or null), "latency_ms", "checked_at", "reason", "read" (what was called), "items",
                 "shape" ("data" | "empty" | "unexpected": what an ok-or-degraded read returned),
                 "history": [{"date": "YYYY-MM-DD", "ok": bool}, ...] (one per day, newest last, at most HISTORY_DAYS)}}}
"""
import datetime
import json
import os
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "health.json"
HISTORY_DAYS = 14


def path():
    return Path(os.environ.get("IRLPLS_HEALTH_FILE") or PATH)


def load(p=None):
    """The last health.json, or {} when there is none (or it cannot be read)."""
    try:
        d = json.loads(Path(p or path()).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def last(card_id, data=None):
    """The provider's last check ({result, ok, status, error, checked_at, reason, ...}) or None. Skipped checks
    (a key not set) count as no check."""
    r = ((data if data is not None else load()).get("providers") or {}).get(card_id)
    return r if isinstance(r, dict) and r.get("result") in ("ok", "degraded", "failed") else None


def summary(card_id, data=None):
    """What catalog.json and plans carry: {ok, checked_at, status, error, reason} or None."""
    r = last(card_id, data)
    if not r:
        return None
    return {"ok": bool(r.get("ok")), "result": r.get("result"), "checked_at": r.get("checked_at"), "status": r.get("status"),
            "error": r.get("error"), "reason": r.get("reason")}


def _when(r):
    try:
        return datetime.datetime.fromisoformat(str(r.get("checked_at")).replace("Z", "+00:00"))
    except ValueError:
        return None


def latest(card_id, data=None, live=None):
    """The newest check of either source: the daily read check (data) or the landing's 30-minute check (live,
    {"checked", "results": {card_id: {"ok", ...}}}). A tie goes to the one that worked; None when neither checked."""
    out = [r for r in [last(card_id, data)] if r]
    lr = ((live or {}).get("results") or {}).get(card_id)
    if isinstance(lr, dict) and "ok" in lr:
        out.append({"result": "ok" if lr["ok"] else "failed", "ok": bool(lr["ok"]), "status": lr.get("status"),
                    "error": lr.get("error"), "checked_at": live.get("checked")})
    if not out:
        return None
    floor = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return max(out, key=lambda r: ((_when(r) or floor), bool(r.get("ok"))))


def why(r):
    """The short cause in brackets: the HTTP status, else the error class."""
    if r.get("result") == "degraded":
        return "answered, but not as expected"
    if r.get("status"):
        return str(r["status"])
    return r.get("error") or "error"


def line(card_id, data=None, live=None):
    """'Read check: worked 28 Oct' / 'Read check: failed 28 Oct (404)' from the newest check of either source
    (see latest), or None when never checked."""
    r = latest(card_id, data, live)
    if not r:
        return None
    d = _when(r)
    when = f"{d.astimezone(datetime.timezone.utc).day} {d.astimezone(datetime.timezone.utc).strftime('%b')}" if d else "recently"  # the UTC day, like the public line
    if r.get("result") == "degraded":   # honest: we saw an answer, we cannot tell whether the provider changed or is just quiet
        return f"Read check: {when}, answered but not as expected"
    return f"Read check: worked {when}" if r.get("ok") else f"Read check: failed {when} ({why(r)})"


def failed_streak(history):
    """Consecutive calendar days, ending with the newest entry, on which the check failed."""
    n, prev = 0, None
    for h in reversed(history or []):
        try:
            day = datetime.date.fromisoformat(h["date"])
        except (KeyError, TypeError, ValueError):
            break
        if h.get("ok") or (prev is not None and (prev - day).days != 1):
            break
        n, prev = n + 1, day
    return n


def add_history(history, date, ok):
    """history with today's result (a later run the same day replaces the earlier one), at most HISTORY_DAYS."""
    out = [h for h in (history or []) if isinstance(h, dict) and h.get("date") != date]
    out.append({"date": date, "ok": bool(ok)})
    return sorted(out, key=lambda h: h["date"])[-HISTORY_DAYS:]


# ---- public snapshot (docs/research/ops/HEALTH-PUBLIC-DESIGN.md) ----
PUBLIC_PATH = Path(__file__).resolve().parent.parent / "data" / "health-public.json"
PUBLIC_STATUS = ("ok", "degraded", "failing", "unknown")
_ID_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def public_status(r):
    """ok / degraded / failing / unknown from one provider record (no record, or a skipped check, is unknown)."""
    return {"ok": "ok", "degraded": "degraded", "failed": "failing"}.get((r or {}).get("result"), "unknown")


def sanitize_public(data, known=None, today=None):
    """The public-safe summary of a health.json (or of an already-public file, re-checked): only card_id, status and
    last_checked (a date). Nothing else is ever copied: no paths, URLs, reasons, errors, latencies or responses. Ids
    that are not plain card ids (or not in `known`, when given) are dropped; so are unknown dates."""
    out = {}
    src = data.get("cards") if isinstance(data, dict) and isinstance(data.get("cards"), dict) else (data or {}).get("providers") or {}
    for cid, r in src.items():
        if not isinstance(r, dict) or not isinstance(cid, str) or not _ID_RX.match(cid) or (known is not None and cid not in known):
            continue
        st = r["status"] if r.get("status") in PUBLIC_STATUS else public_status(r)
        day = str(r.get("last_checked") or r.get("checked_at") or "")[:10]
        if not _DATE_RX.match(day):
            day, st = None, "unknown"
        out[cid] = {"status": st, "last_checked": day}
    gen = str((data or {}).get("generated") or (data or {}).get("checked_at") or today or "")[:10]
    return {"irlpls_public_health": 1, "generated": gen if _DATE_RX.match(gen) else None, "cards": dict(sorted(out.items()))}


def load_public(p=None, known=None):
    """data/health-public.json, re-sanitized on read, or {} when there is no usable snapshot."""
    try:
        d = json.loads(Path(p or PUBLIC_PATH).read_text())
    except (OSError, ValueError):
        return {}
    return sanitize_public(d, known) if isinstance(d, dict) and d.get("irlpls_public_health") == 1 else {}


def public_line(entry):
    """The small neutral line for a card, only when its status is degraded or failing; else None. Never a ranking."""
    if not isinstance(entry, dict) or entry.get("status") not in ("degraded", "failing"):
        return None
    try:
        d = datetime.date.fromisoformat(str(entry.get("last_checked")))
    except ValueError:
        return None
    when = f"{d.day} {d.strftime('%b')}"
    return f"Read check failed on {when}" if entry["status"] == "failing" else f"Read check on {when}: answered but not as expected"
