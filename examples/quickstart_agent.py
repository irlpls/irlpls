"""Ten-minute agent loop: ask, check policy, run live, report proof as stated.

    uv run python examples/quickstart_agent.py "river level at Comfort, TX now"

Network: the free USGS water API (no key). Nothing is ordered; live reads cost nothing.
"""
import sys
import irlpls
from irlpls import Policy

need = sys.argv[1] if len(sys.argv) > 1 else "river level at Comfort, TX now"
policy = Policy(free_only=True, max_order_usd=0, deny=["noaa-tides"])  # your person's rules, set once

job = irlpls.request(need, why="deciding whether to go tubing", policy=policy)
for q in job.questions:                      # ask only what you cannot answer yourself
    if q["blocking"]:
        raise SystemExit(f"need from my person: {q['ask']} ({q['why']})")

print(f"{job.state}: {len(job.plan)} option(s), {len(job.ruled_out)} ruled out")
for o in job.ruled_out[:3]:
    print(f"  ruled out {o['name']}: {o['reason']}")

pick = job.best()                            # best option that complies with the policy and can be called
if pick is None or pick["policy"]:
    raise SystemExit("Missing: no compliant provider. Next step: relax the policy or add an account.")
print(f"choice: {pick['name']} ({pick['route']})")

job.submit()                                 # live route: runs now, free. A commission would stop at a quote.
r = next((x for x in job.results if x.get("value") is not None), None)
if not r:
    raise SystemExit(f"no reading; warnings: {job.warnings}")
card = irlpls.card(pick["card_id"])
stated = {k: card["verification"][k]["level"] for k in ("time", "location", "content")}
print(f"\n{r['label']}: {r['value']} {r['unit']} at {r['phenomenon_time']} from {pick['name']}")
print("proof as the provider states it:", ", ".join(f"{k} {v}" for k, v in stated.items()))
print("irlpls verified nothing; any advice from this number is my own reading.")
