"""A local convenience: compare the metadata on what came back with what you asked for.

This is not verification and irlpls does not verify anything. Proof comes from providers (see the guarantees on
their Capture Card) and from independent verifiers (the card's `attestations`). This helper just runs simple
comparisons on your side, so your own pipeline can screen deliveries:

  time     captured after you asked (and not in the future); for live requests, within the window you asked for
  place    within radius_km of the requested point
  content  shows what you asked for; needs a checker you supply (your own vision/LLM call), since we run no inference

Each check is pass / fail / unknown, and carries how much to trust the metadata it used: if the provider only
*claims* its timestamps or GPS (see the Capture Card), a pass is a metadata pass, not proof.
"""
from datetime import datetime, timedelta, timezone

from . import geo


def _ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e12 else v, timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _trust(card, key):
    if not card:
        return "unknown"
    return {"evidenced": "evidenced by provider", "claimed": "metadata only (provider claims it)", "none": "no provider guarantee"}[
        card["verification"][key]["level"]]


def compare(items, req, since=None, radius_km=1.0, check_content=None, card=None, now=None):
    """items: observations/deliverables (dicts with phenomenon_time, lat, lon, url...). req: a Request.
    since: when you asked (defaults to now minus the live window, or None). check_content(item, need) -> (True/False/None, note)."""
    now = now or datetime.now(timezone.utc)
    if since is None and req.mode == "live":
        since = now - timedelta(minutes=req.within_minutes or 60)
    since = _ts(since) if not isinstance(since, datetime) else since
    out = []
    for i, it in enumerate(items):
        checks = {}
        t = _ts(it.get("phenomenon_time"))
        if t is None:
            checks["time"] = ("unknown", "no capture time on the item")
        elif t > now + timedelta(minutes=5):
            checks["time"] = ("fail", f"captured in the future ({t.isoformat()})")
        elif since and t < since:
            checks["time"] = ("fail", f"captured {t.isoformat()}, before you asked ({since.isoformat()})")
        else:
            checks["time"] = ("pass", f"captured {t.isoformat()}")
        if req.lat is None or it.get("lat") is None:
            checks["place"] = ("unknown", "no requested point or no item location")
        else:
            d = geo.distance_km(req.lat, req.lon, it["lat"], it["lon"])
            checks["place"] = ("pass" if d <= radius_km else "fail", f"{d:.2f} km from the requested point (limit {radius_km} km)")
        if check_content is None:
            checks["content"] = ("unknown", "no content checker given; pass check_content=your vision/LLM function")
        else:
            ok, note = check_content(it, req.need)
            checks["content"] = ({True: "pass", False: "fail"}.get(ok, "unknown"), note)
        res = {k: {"status": s, "detail": d, "trust": _trust(card, {"place": "location"}.get(k, k))} for k, (s, d) in checks.items()}
        st = [v["status"] for v in res.values()]
        verdict = "fail" if "fail" in st else "pass" if all(s == "pass" for s in st) else "unverified"
        out.append({"index": i, "verdict": verdict, "checks": res})
    summary = {v: sum(1 for o in out if o["verdict"] == v) for v in ("pass", "unverified", "fail")}
    return {"summary": summary, "items": out}
