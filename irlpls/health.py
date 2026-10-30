"""The daily read check, read side: did one minimal real read through each provider's adapter work last time?

tools/healthcheck.py writes irlpls/data/health.json once a day (LaunchAgent com.mo.irlpls-healthcheck); build.py
shows one line per card page (merged with the landing's 30-minute check, src/media/live/health.json: see latest), catalog.json carries providers[id].health, and the broker warns when the provider a plan
picks failed its last check. The file is generated and gitignored (it changes every day); without it nothing is shown
and nothing is warned. IRLPLS_HEALTH_FILE points somewhere else (tests).

health.json:
  {"irlpls_health": 1, "checked_at", "duration_s", "summary": {checked, ok, failed, skipped},
   "providers": {card_id: {"result": "ok"|"failed"|"skipped", "ok", "status" (HTTP status, or null), "error" (error
                 class, or null), "latency_ms", "checked_at", "reason", "read" (what was called), "items",
                 "history": [{"date": "YYYY-MM-DD", "ok": bool}, ...] (one per day, newest last, at most HISTORY_DAYS)}}}
"""
import datetime
import json
import os
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
    return r if isinstance(r, dict) and r.get("result") in ("ok", "failed") else None


def summary(card_id, data=None):
    """What catalog.json and plans carry: {ok, checked_at, status, error, reason} or None."""
    r = last(card_id, data)
    if not r:
        return None
    return {"ok": bool(r.get("ok")), "checked_at": r.get("checked_at"), "status": r.get("status"),
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
    when = f"{d.astimezone().day} {d.astimezone().strftime('%b')}" if d else "recently"  # the builder's day
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
