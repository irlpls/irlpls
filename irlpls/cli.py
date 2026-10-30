"""irlpls command line: ask the broker for NEW real-world data.

  irlpls request "is the Guadalupe at Comfort, TX good for tubing right now" --submit
  irlpls request "photo of the storefront at 123 Main St, Springfield, IL today" --policy policy.json
  irlpls answer JOB --place "123 Main St, Springfield, IL"      # or --lat/--lon, --within-minutes, --quantity, --max-order-usd
  irlpls choose JOB proxypics
  irlpls submit JOB                                             # live reads run; made-to-order stops at a quote
  irlpls submit JOB --confirm --idempotency-key order-1         # places the order (may cost money)
  irlpls refresh JOB                                            # ask the provider how a placed order is going
  irlpls approve JOB --approval-id Q1 --total 1000 --confirm --idempotency-key appr-1
  irlpls job JOB                                                # show a job again (--json for all of it)

  irlpls find "is Shoal Creek in Austin, TX flooded right now"
  irlpls live usgs-water --at "Austin, TX"
  irlpls commission skyfi --lat 30.27 --lon -97.74          # dry run; --confirm needs --idempotency-key
  irlpls show skyfi
  irlpls adapters

Jobs are saved in ~/.irlpls/jobs/, so each command can pick one up by its id.
"""
import argparse
import json
import sys

import irlpls
from .registry import ASSURANCES

MARK = {"evidenced": "●", "claimed": "◐", "none": "·"}


def _price(c):
    p = c["pricing"]
    if p.get("from_usd"):
        return f'${p["from_usd"]:g} / {p["unit"]}'
    return "free" if p.get("model", "").lower().startswith("free") else "quote"


def cmd_find(a):
    pol = _policy_file(a.policy)
    res = irlpls.find(a.need, limit=a.limit, geocode=not a.no_geo, policy=pol, strict=a.strict)
    if a.json:
        return print(json.dumps(res, indent=2))
    req = irlpls.make_request(a.need, geocode=False)
    when = f' within {req.within_minutes} min' if req.within_minutes else ""
    proof = ", ".join(req.require) + (" (default)" if req.default_proof else "")
    print(f"{req.mode}{when} · qty {req.quantity} · proof: {proof}\n")
    print(f'{"FIT":>4}  {"PROVIDER":<28} {"DELIVERY":<20} {"FROM":<22} PROOF   SDK')
    for m in res:
        c = irlpls.card(m["id"])
        d = c.get("delivery", {})
        proof = "".join(MARK[c["verification"][k]["level"]] for k, _ in ASSURANCES)
        ad = m["adapter"]
        a_ = irlpls.adapters.get(m["id"])
        ad = "-" if not ad else "+".join(x for x in ("live", "commission") if ad[x]) + (
            " (wallet)" if getattr(a_, "needs_wallet", False) else "" if ad["ready"] else " (key)")
        print(f'{m["fit"] if m["fit"] is not None else "-":>4}  {c["name"][:28]:<28} {("/".join(d.get("modes", [])) + " · " + d.get("speed", "?"))[:20]:<20} {_price(c)[:22]:<22} {proof} {ad}')
        if m["gaps"]:
            print(f'      ! {"; ".join(m["gaps"])}')
        if m.get("policy"):
            print(f'      ✕ policy: {"; ".join(m["policy"])}')
    print("\nproof columns: human app camera time place content cross-check (● evidenced ◐ claimed · none). Fit is for this request only.")


def _usd(text):
    """A dollar amount from the command line, as Decimal (never a float): 0 or more."""
    from decimal import Decimal, InvalidOperation
    try:
        v = Decimal(str(text).strip().lstrip("$"))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not an amount in USD: {text!r} (e.g. 40 or 12.50)")
    if not v.is_finite() or v < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more (got {text})")
    return v


def _policy_file(path):
    if not path:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except OSError as e:
        raise SystemExit(f"irlpls: can't read the policy file {path!r} ({e.strerror}); pass --policy with a JSON file")
    except ValueError as e:
        raise SystemExit(f"irlpls: the policy file {path!r} is not valid JSON ({e})")


def _spec_arg(text):
    if not text:
        return None
    try:
        v = json.loads(text)
    except ValueError as e:
        raise SystemExit(f"irlpls: --spec must be a JSON object ({e})")
    if not isinstance(v, dict):
        raise SystemExit("irlpls: --spec must be a JSON object, e.g. '{\"price\": 25}'")
    return v


def _job(job_id):
    j = irlpls.broker.load_job(job_id)
    if j is None:
        raise SystemExit(f"irlpls: no job {job_id!r} (jobs are saved in ~/.irlpls/jobs/; start one with irlpls request)")
    return j


def _money(p):
    if not p:
        return None
    return f'{p["amount"]} {p["currency"]}' + ("" if p.get("firm") else " (indicative" + (", from the card" if p.get("source") == "card" else "") + ")")


def _sdk(o):
    sdk = o.get("sdk") or {}
    if not sdk.get("callable"):
        return "no adapter (by hand)"
    if sdk.get("needs_wallet"):
        return "your wallet signs"
    if sdk.get("env"):
        return ("needs " if sdk.get("needs_key") else "key set: ") + sdk["env"]
    return "no key"


def show(job, d=None):
    """A job for people: state, the default option, questions, the plan with readiness, price and warnings, the quote,
    the ready call and results. The same facts the SDK and MCP return (irlpls job JOB --json for all of them)."""
    d = d or job.to_dict()
    print(f'job {d["id"]} · {d["state"]}' + (f' · default {d["best_card_id"]}' if d.get("best_card_id") else ""))
    if d.get("refusal"):
        r = d["refusal"]
        print(f'  ✕ {r.get("msg")}' + (f' Instead: {r["instead"]}' if r.get("instead") else ""))
    for q in d["questions"]:
        hint = q.get("hint") or {"place": "irlpls answer JOB --place '...'", "within_minutes": "irlpls answer JOB --within-minutes N",
                                 "max_order_usd": "irlpls answer JOB --max-order-usd N", "quantity": "irlpls answer JOB --quantity N",
                                 "lat": "irlpls answer JOB --lat N --lon N", "lon": "irlpls answer JOB --lat N --lon N"}.get(q["field"], "")
        print(f'  ? {q["ask"]}{" (blocking)" if q["blocking"] else ""}' + (f' {q["why"]}' if q.get("why") else "")
              + (f'\n      → {hint.replace("JOB", d["id"])}' if hint else ""))
    for o in d["plan"][:6]:
        mark = "→" if o.get("default") and d["state"] != "clarifying" else " "
        price = "free" if o.get("price") == 0 else (f'from ${o["price"]:g}' if o.get("price") else "price not published")
        print(f' {mark} {o["fit"] if o["fit"] is not None else "-":>3} {o["name"][:30]:<30} {o["route"]:<10} {price:<20} {_sdk(o)}')
        if o.get("readiness"):
            print(f'        {o["readiness"]["line"]}')
        for v in o["policy"]:
            print(f'        ✕ policy: {v}')
        if o.get("warning"):
            print(f'        ! {o["warning"]}')
        if o.get("by_hand"):
            print(f'        order by hand: {o["by_hand"]["msg"]}')
    best = next((o for o in d["plan"] if o.get("default")), None)
    pc = (best or {}).get("prepared_call")
    if pc and d["state"] in ("proposed", "quoted"):
        print(f'\nready call ({pc.get("operation")}): {pc.get("method")} {pc.get("url")}'
              + ("" if pc.get("safe_to_run_without_approval") else "  · needs your person's yes"))
    if d["state"] == "failed":
        print(f'\nfailed: {d["log"][-1]["msg"]}')
    q = d.get("quote")
    if q:
        print("\nquote: " + (_money(q.get("price")) or q.get("price_note") or "no price"))
        for m in q.get("missing") or []:
            print(f"  missing: {m}")
        for b in q.get("blocked_by_policy") or []:
            print(f"  ✕ not sent: {b}")
        if q.get("would_send"):
            print("  would send:\n" + json.dumps(q["would_send"], indent=2, default=str)[:1200])
    for x in d.get("approvals") or []:
        print(f'  approval {x.get("id")}: {x.get("amount")} {x.get("currency") or ""} {x.get("label") or ""}'
              f'\n      → irlpls approve {d["id"]} --approval-id {x.get("id")} --total {x.get("amount")} --confirm --idempotency-key KEY')
    for w in d.get("warnings") or []:
        print(f'  ! {w.get("code")}: {w.get("msg")}')
    for r in d["results"][:8]:
        what = f'{r.get("value")} {r.get("unit") or ""}' if r.get("value") is not None else (r.get("camera") or "") + "  " + (r.get("url") or "")
        where = r.get("site") or r.get("station") or ""
        print(f'  {r.get("phenomenon_time")}  {r.get("label","")}: {what}  ({where + ", " if where else ""}{r.get("distance_km","?")} km)')


def _answers(a):
    return {k: v for k, v in dict(place=getattr(a, "place", None), lat=getattr(a, "lat", None), lon=getattr(a, "lon", None),
                                  within_minutes=getattr(a, "within_minutes", None), quantity=getattr(a, "quantity", None),
                                  max_order_usd=getattr(a, "max_order_usd", None)).items() if v is not None}


def _out(job, a):
    if getattr(a, "json", False):
        return print(json.dumps(job.to_dict(), indent=2, default=str))
    show(job)


def cmd_request(a):
    pol = _policy_file(a.policy)
    hints = {k: v for k, v in dict(lat=a.lat, lon=a.lon, within_minutes=a.within_minutes, quantity=a.quantity).items() if v is not None}
    job = irlpls.request(a.need, why=a.why, policy=pol, **hints)
    if a.place:
        job.answer(place=a.place)
    if a.submit and job.state != "clarifying":
        job.submit(spec=_spec_arg(a.spec), confirm=a.confirm, idempotency_key=a.idempotency_key)
    elif a.submit:
        print("not submitted: answer the blocking questions first (irlpls answer JOB --place ...)", file=sys.stderr)
    _out(job, a)


def cmd_answer(a):
    fields = _answers(a)
    if not fields:
        raise SystemExit("irlpls answer: give at least one of --place, --lat/--lon, --within-minutes, --quantity, --max-order-usd")
    _out(_job(a.job).answer(**fields), a)


def cmd_choose(a):
    _out(_job(a.job).choose(a.card_id), a)


def cmd_submit(a):
    _out(_job(a.job).submit(spec=_spec_arg(a.spec), confirm=a.confirm, idempotency_key=a.idempotency_key), a)


def cmd_refresh(a):
    _out(_job(a.job).refresh(), a)


def cmd_job(a):
    _out(_job(a.job), a)


def cmd_approve(a):
    j = _job(a.job)
    out = j.approve(approval_id=a.approval_id, confirm=a.confirm, idempotency_key=a.idempotency_key, total=a.total)
    if not a.confirm:
        print("nothing sent: the approval would send\n" + json.dumps(out, indent=2, default=str)[:1200])
        print("after your person says yes to this exact price: add --confirm --idempotency-key KEY --total AMOUNT")
        return
    _out(j, a)


def cmd_show(a):
    c = irlpls.card(a.id)
    if not c:
        sys.exit(f"no card {a.id!r}")
    print(json.dumps(c, indent=2))


def cmd_validate(a):
    from . import validate
    bad = 0
    for t in a.targets:
        try:
            card, url = validate.load(t)
        except (OSError, ValueError, irlpls.http.HTTPError) as e:
            print(f"{t}: cannot read ({e})")
            bad += 1
            continue
        ps = validate.problems(card, fetched_from=url)
        if a.against:
            ref = irlpls.card(a.against if not a.against.endswith(".json") else json.load(open(a.against))["id"])
            if ref is None:
                sys.exit(f"no audited card {a.against!r}")
            for where, they, we in validate.overclaims(card, ref):
                ps.append(("warn", where, f"claims {they!r}; the audited card says {we!r}: check before relying on it"))
        errs = [p for p in ps if p[0] == "error"]
        partial = any(w == "schema" and "not installed" in m for _, w, m in ps)
        verdict = "invalid" if errs else ("rules ok, SCHEMA NOT CHECKED" if partial else "valid")
        print(f"{t}: {verdict} ({len(errs)} errors, {len(ps) - len(errs)} warnings)")
        for sev, where, msg in ps:
            print(f"  {sev:5} {where}: {msg}")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


def cmd_card_file(a):
    from . import validate
    c = irlpls.card(a.id)
    if not c:
        sys.exit(f"no card {a.id!r}")
    doc, todo = validate.to_provider_file(c)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if a.out:
        import pathlib
        p = pathlib.Path(a.out) / ".well-known" / "irlpls"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        print(f"wrote {p}")
    else:
        print(text)
    print("\nFor the provider to settle before serving it:", file=sys.stderr)
    for t in todo:
        print(" - " + t, file=sys.stderr)


def cmd_live(a):
    for k in ("lat", "lon"):
        v = getattr(a, k)
        if v is not None and not (-90 <= v <= 90 if k == "lat" else -180 <= v <= 180):
            raise SystemExit(f"irlpls live: --{k} must be between {-90 if k == 'lat' else -180} and {90 if k == 'lat' else 180} (got {v:g})")
    if (a.lat is None) != (a.lon is None):
        raise SystemExit("irlpls live: give --lat together with --lon (or --at 'place')")
    obs = irlpls.live(a.id, place=a.at, lat=a.lat, lon=a.lon, radius_km=a.radius)
    if a.json:
        return print(json.dumps(obs, indent=2))
    for o in obs:
        where = o.get("station") or o.get("site") or ""
        print(f'{o["phenomenon_time"]}  {o.get("label",""):<24} {o.get("value")!s:>10} {o.get("unit","") or "":<8} {where} ({o.get("distance_km","?")} km)')


def cmd_commission(a):
    spec = _spec_arg(a.spec) or {}
    if a.lat is not None:
        spec.update(lat=a.lat, lon=a.lon)
    if a.confirm and not a.idempotency_key:
        raise SystemExit("irlpls commission: --confirm places a real order; add --idempotency-key KEY (any unique string, "
                         "reused on a retry so it is never ordered twice)")
    print(json.dumps(irlpls.commission(a.id, spec, confirm=a.confirm, policy=_policy_file(a.policy),
                                       idempotency_key=a.idempotency_key), indent=2, default=str))


def adapter_state(ad):
    """(state, what it needs) for one adapter, in the catalog's words: wallet providers are never 'needs key'."""
    if getattr(ad, "needs_wallet", False):
        return "wallet", "your wallet signs paid calls (no API key)"
    if not ad.env:
        return "ready", "no key"
    return ("ready" if ad.configured() else "needs key"), ad.env


def cmd_adapters(a):
    rows = [(cid, "+".join(x for x, ok in (("live", ad.can_live), ("commission", ad.can_commission)) if ok), *adapter_state(ad))
            for cid, ad in irlpls.adapters.ADAPTERS.items()]
    w = max(len(r[0]) for r in rows)
    for cid, caps, state, need in rows:
        print(f"{cid:<{w}}  {caps:<16} {state:<10} {need}")
    tally = {}
    for r in rows:
        tally[r[2]] = tally.get(r[2], 0) + 1
    print("\n" + " · ".join(f"{n} {k}" for k, n in sorted(tally.items())))


def main(argv=None):
    p = argparse.ArgumentParser(prog="irlpls", description="Get new real-world data: live (reporting now) or commissioned (a provider captures what you specify).")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="rank providers for a need")
    f.add_argument("need")
    f.add_argument("--limit", type=int, default=10)
    f.add_argument("--json", action="store_true")
    f.add_argument("--no-geo", action="store_true", help="don't geocode the place")
    f.add_argument("--policy", help="policy JSON file (see irlpls/policy.py)")
    f.add_argument("--strict", action="store_true", help="drop providers that violate the policy")
    f.set_defaults(fn=cmd_find)
    q = sub.add_parser("request", help="ask the broker for new real-world data")
    q.add_argument("need")
    q.add_argument("--why")
    q.add_argument("--place")
    q.add_argument("--lat", type=float)
    q.add_argument("--lon", type=float)
    q.add_argument("--within-minutes", type=int, help="deadline, in minutes from now")
    q.add_argument("--quantity", type=int, help="how many items (photos, clips) for a made-to-order ask")
    q.add_argument("--policy", help="policy JSON file")
    q.add_argument("--submit", action="store_true", help="execute: live reads run; commissions return a quote")
    q.add_argument("--confirm", action="store_true", help="with --submit, actually place a commission (may cost money)")
    q.add_argument("--idempotency-key", help="required with --confirm: a unique string, reused on retry so the order is never placed twice")
    q.add_argument("--spec", help="JSON spec for a commission (order details the provider needs, e.g. '{\"price\": 25}')")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_request)
    an = sub.add_parser("answer", help="answer a job's questions; the plan is rebuilt")
    an.add_argument("job")
    an.add_argument("--place")
    an.add_argument("--lat", type=float)
    an.add_argument("--lon", type=float)
    an.add_argument("--within-minutes", type=int)
    an.add_argument("--quantity", type=int)
    an.add_argument("--max-order-usd", type=_usd, help="most your person will spend on this order, in USD; 0 = free only")
    an.add_argument("--json", action="store_true")
    an.set_defaults(fn=cmd_answer)
    ch = sub.add_parser("choose", help="pick a provider from a job's plan instead of the default")
    ch.add_argument("job")
    ch.add_argument("card_id")
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(fn=cmd_choose)
    sb = sub.add_parser("submit", help="run a job: live reads now; made-to-order returns a quote unless --confirm")
    sb.add_argument("job")
    sb.add_argument("--spec", help="JSON order details the provider needs (see the quote's missing list)")
    sb.add_argument("--confirm", action="store_true", help="actually place the order (may cost money)")
    sb.add_argument("--idempotency-key", help="required with --confirm: a unique string, reused on retry")
    sb.add_argument("--json", action="store_true")
    sb.set_defaults(fn=cmd_submit)
    rf = sub.add_parser("refresh", help="ask the provider how a placed order is going")
    rf.add_argument("job")
    rf.add_argument("--json", action="store_true")
    rf.set_defaults(fn=cmd_refresh)
    ap = sub.add_parser("approve", help="the second, paid step of a two-step order (spends money with --confirm)")
    ap.add_argument("job")
    ap.add_argument("--approval-id")
    ap.add_argument("--total", help="required with --confirm: the amount exactly as listed in the job's approvals")
    ap.add_argument("--confirm", action="store_true", help="send the approval (spends money); only after your person's yes")
    ap.add_argument("--idempotency-key", help="required with --confirm: a unique string, reused on retry")
    ap.add_argument("--json", action="store_true")
    ap.set_defaults(fn=cmd_approve)
    jb = sub.add_parser("job", help="show a saved job")
    jb.add_argument("job")
    jb.add_argument("--json", action="store_true")
    jb.set_defaults(fn=cmd_job)
    s = sub.add_parser("show", help="print a Capture Card")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    v = sub.add_parser("validate", help="check Capture Cards (files or https URLs) against the standard")
    v.add_argument("--against", help="an audited card id (e.g. skyfi): flag anything the checked card claims beyond it")
    v.add_argument("targets", nargs="+", help="card .json files, or URLs such as https://provider.example/.well-known/irlpls")
    v.set_defaults(fn=cmd_validate)
    g = sub.add_parser("card-file", help="a provider's card as the file it would serve at /.well-known/irlpls, with a checklist")
    g.add_argument("id")
    g.add_argument("--out", help="a directory: writes <out>/.well-known/irlpls")
    g.set_defaults(fn=cmd_card_file)
    o = sub.add_parser("live", help="pull what is reporting now near a place")
    o.add_argument("id")
    o.add_argument("--at", help='place name, e.g. "Austin, TX"')
    o.add_argument("--lat", type=float)
    o.add_argument("--lon", type=float)
    o.add_argument("--radius", type=float, default=15, help="km")
    o.add_argument("--json", action="store_true")
    o.set_defaults(fn=cmd_live)
    c = sub.add_parser("commission", help="ask a provider to capture what you specify (dry run unless --confirm)")
    c.add_argument("id")
    c.add_argument("--lat", type=float)
    c.add_argument("--lon", type=float)
    c.add_argument("--spec", help="JSON spec for the adapter")
    c.add_argument("--confirm", action="store_true", help="actually place the order (may cost money); needs --idempotency-key")
    c.add_argument("--idempotency-key", help="required with --confirm: a unique string, reused on retry so it is never ordered twice")
    c.add_argument("--policy", help="policy JSON file; a violating order is never sent")
    c.set_defaults(fn=cmd_commission)
    sub.add_parser("adapters", help="list adapters").set_defaults(fn=cmd_adapters)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except irlpls.broker.JobError as e:
        hint = e.hint("cli").replace("JOB", getattr(a, "job", None) or "JOB")
        sys.exit(f"irlpls: {e.message}" + (f"\n  → {hint}" if hint else ""))
    except irlpls.adapters.base.MissingDetails as e:
        sys.exit(f"irlpls: not sent, missing:\n" + "\n".join(f"  - {m}" for m in e.missing)
                 + "\n  → give them with --spec '{\"field\": ...}' (an address can be the --place)")
    except (irlpls.adapters.MissingCredentials, irlpls.adapters.NotSupported, irlpls.PolicyViolation, ValueError) as e:
        sys.exit(f"irlpls: {e}")


if __name__ == "__main__":
    main()
