"""Training loop: a driving model asks for its hard cases, under a $50 standing mandate (no human in the loop).

    python examples/active_learning.py

A PyTorch-style sketch with the model stubbed by `random`, so it needs no torch, no GPU and no network.
With no provider keys set, every order is a dry run and the script says so; nothing is bought.
"""
import random

import irlpls
from irlpls import Policy

SCENES = ["right turn, snowy afternoon", "cyclist at dusk", "flooded street", "roadwork at night",
          "highway merge, clear noon", "parked cars, overcast", "four-way stop, morning", "school zone, daytime"]
mandate = Policy(auto_submit=True, max_order_usd=50)  # pre-approved: the broker may confirm orders within $50 itself


class StubModel:
    """Stands in for your perception model: per-scene predictive entropy. The hard cases are hard on purpose."""
    HARD = set(SCENES[:4])

    def uncertainty(self, scene):
        return random.uniform(0.6, 0.9) if scene in self.HARD else random.uniform(0.05, 0.3)


def top_uncertain(model, k):
    # with torch: scores = entropy(model(batch).softmax(-1)); scenes = scores.topk(k)
    return sorted(SCENES, key=model.uncertainty, reverse=True)[:k]


model = StubModel()
for epoch in range(1):  # for epoch in range(n): train_one_epoch(model, loader) ... then ask for the hard cases
    hard = top_uncertain(model, 4)
    print(f"epoch {epoch}: least sure about {hard}\n")
    dry_runs = 0
    for scene in hard:
        job = irlpls.request(f"fresh dashcam video of {scene}", why="active learning: the model is least sure here",
                             policy=mandate, geocode=False, lat=37.7749, lon=-122.4194, country_code="us", state="california")
        top = job.plan[0] if job.plan else None
        print(f"{scene}")
        print(f"  top provider: {top['name'] + ' (' + top['card_id'] + f', fit {top["fit"]})' if top else 'none'}")
        job.submit(spec={"radius_m": 60, "days": 7})
        q = job.quote or {}
        dry = bool(q.get("dry_run"))
        dry_runs += dry
        print(f"  order: {job.state} via {job.choice['card_id'] if job.choice else '-'}" + (" (DRY RUN: nothing sent, nothing bought)" if dry else ""))
        for v in q.get("blocked_by_policy", []):
            print(f"    held by policy: {v}")
        if dry and job.choice and job.choice["sdk"]["needs_key"]:
            print(f"    {job.choice['sdk']['env']} not set")
    # with clips back: dataset.extend(label(clips)); train_one_epoch(model, loader)
    print(f"\n{dry_runs} of {len(hard)} requests were dry runs; no clips to label this epoch." if dry_runs else "\nall orders placed; label the clips when they arrive")
