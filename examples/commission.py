"""A data commission: a lab agent orders 500 first-person clips under the lab's policy. Never places a real order.

    python examples/commission.py

No network. The submit is a dry run: it returns exactly what would be sent, or the provider's quote where the adapter
can fetch one with your key. Placing the order takes job.submit(confirm=True) after a person reads the quote.
"""
import json

import irlpls
from irlpls import Policy

policy = Policy(need_ai_training=True, max_order_usd=2500)
job = irlpls.request("500 first-person clips of a hand opening a door, 7 days",
                     why="fine-tune a door-opening manipulation policy", policy=policy, geocode=False)

print(f"{job.state}: {len(job.questions)} question(s)")
for q in job.questions:
    print(f"  {'blocking' if q['blocking'] else 'optional'}: {q['ask']}  ({q['why']})")
# The lab agent's own model answers: the lab is in Palo Alto (lat/lon, so no geocoding call), and 7 days means within 7 days.
job.answer(lat=37.43, lon=-122.17, country_code="us", state="california", within_minutes=7 * 24 * 60)
print(f"answered place and deadline -> {job.state}")

r = job.request
print(f"\nunderstood: {r.mode}, {r.quantity} x {', '.join(r.modalities)}, terms {r.terms}, within {r.within_minutes // 1440} days")
print("\nplan, checked against the policy (AI-training rights required, order <= $2,500):")
for o in job.plan:
    print(f"  {o['fit']:>3}  {o['name']:<34} {o['route']:<10} " + ("ok" if not o["policy"] else ""))
    for v in o["policy"]:
        print(f"         x {v}")

best = job.best()
if not best:
    raise SystemExit("\nno provider meets the policy; nothing will be ordered")
print(f"\nbest compliant: {best['card_id']}" + (f" (needs {best['sdk']['env']}; not set, so this is a dry run)" if best["sdk"]["needs_key"] else ""))

job.submit(spec={"title": "Film a hand opening a door, first person", "notes": job.need, "description": job.need,
                 "evidence": ["video"], "spots": 500, "price": 4})  # confirm=False: never placed
q = job.quote or {}
print(f"{job.state} via {job.choice['card_id']}: dry run, nothing sent")
for v in q.get("blocked_by_policy", []):
    print(f"  held by policy: {v}")
if q.get("quote"):
    print("  provider quote:", json.dumps(q["quote"])[:300])
if q.get("would_send"):
    w = q["would_send"]
    print(f"  would send: {w['method']} {w['url']}")
    print("  " + json.dumps(w["body"])[:400])
print("\nNothing was ordered. Only a person, after reading a quote under $2,500, calls job.submit(spec, confirm=True); this script never does.")
