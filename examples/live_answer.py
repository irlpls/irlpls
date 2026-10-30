"""Live answer, right now: a person's assistant asks one question, free reads only.

    python examples/live_answer.py

Network: the free USGS water APIs only (gauge readings, river camera, and USGS daily flow statistics).
irlpls returns the numbers; the assistant (this script) turns them into a verdict.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import irlpls
from irlpls import Policy
from irlpls.adapters.usgs_water import daily_stats

SITE = "08167000"  # USGS 08167000 Guadalupe Rv at Comfort, TX

job = irlpls.request(
    "is the Guadalupe at Comfort, TX good for tubing today",
    why="user deciding whether to go tubing today",
    policy=Policy(free_only=True),
    geocode=False, lat=29.9677, lon=-98.9050, country_code="us", state="texas",  # the assistant already knows where Comfort is
)
print("plan (free only):")
for o in job.plan[:4]:
    print(f"  {o['fit']:>3}  {o['name']:<32} {o['route']:<10} {'ok' if not o['policy'] else 'x ' + '; '.join(o['policy'])}")

job.submit()
print(f"\n{job.state} via {job.choice['card_id']}: {len(job.results)} observations")
flow = next((r for r in job.results if r.get("site") == f"USGS-{SITE}" and r.get("label") == "discharge"), None)
cam = next((r for r in job.results if r.get("kind") == "photo"), None)
if cam:
    print(f"  river camera  {cam['phenomenon_time']}  {cam['url']}")
if not flow:
    raise SystemExit("  no discharge reading from the Comfort gauge right now; no verdict")

# Typical flow for today's date: USGS's own day-of-year statistics (the USGS Water Data Statistics API, the same source
# the landing page uses; the legacy waterservices nwis/stat is shut on 2027-02-22).
today = datetime.now(ZoneInfo("America/Chicago"))
stats = daily_stats([SITE], [(today.month, today.day)]).get((SITE, "00060"), {}).get((today.month, today.day))
if not stats:
    raise SystemExit(f"  USGS publishes no daily statistics for {SITE} on {today:%b %-d}; no verdict")
p25, p50, p75 = stats["p25"], stats["p50"], stats["p75"]

age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(flow["phenomenon_time"].replace("Z", "+00:00"))).total_seconds() / 60
cfs = flow["value"]
print(f"  flow          {cfs:g} {flow['unit']} at {flow['phenomenon_time']} ({age_min:.0f} min old)")
print(f"  typical       {p50:g} ft3/s median for {today:%b %-d} (middle half {p25:g}-{p75:g}; USGS statistics, {stats['count']} years)")

ratio = cfs / p50
if ratio > 3:
    verdict = f"No: running {ratio:.1f}x the usual flow for today; too high and fast to tube safely."
elif cfs > p75:
    verdict = f"Maybe: above normal ({ratio:.1f}x the median); fast, so experienced tubers only."
elif cfs >= p25:
    verdict = f"Yes: flow is normal for {today:%b %-d} ({ratio:.1f}x the median)."
else:
    verdict = f"Slow: below normal ({ratio:.1f}x the median); expect a lazy float and some walking over shallows."
print(f"\nverdict: {verdict}")
