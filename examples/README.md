# Examples

The landing page's three cases, runnable with no keys. Only `live_answer.py` touches the network, and only the free USGS water APIs. Run from the repo root (`uv run python examples/<name>.py`, or `python examples/<name>.py` with irlpls installed). Output below is from real runs on 2026-10-25.

## 1. Live answer: `live_answer.py`

"is the Guadalupe at Comfort, TX good for tubing today", with a free-only policy. It submits against USGS, compares the Comfort gauge (USGS 08167000) with USGS's daily flow statistics for today's date (the same `nwis/stat` source the landing page uses), and the script, acting as the assistant, gives a one-line verdict.

```
$ python examples/live_answer.py
plan (free only):
   71  USGS stream gauges               live       ok

delivered via usgs-water: 3 observations
  flow          76.3 ft3/s at 2026-10-25T17:35:00.000-05:00 (16 min old)
  typical       103 ft3/s median for Oct 25 (middle half 51-201; USGS daily statistics 1940-2025)

verdict: Yes: flow is normal for Oct 25 (0.7x the median).
```

## 2. Commission: `commission.py`

"500 first-person clips of a hand opening a door, 7 days", with `Policy(need_ai_training=True, max_order_usd=2500)`. It shows the questions, the plan with each provider's policy violations, then submits as a dry run. It never places an order.

```
$ python examples/commission.py
clarifying: 2 question(s)
  blocking: Where? A place name or lat/lon.  (Every provider covers specific places; without one nothing can be matched or dispatched.)
  optional: By when do you need it?  (Changes which providers can deliver in time; assuming 7 days.)
answered place and deadline -> proposed

understood: commission, 500 x video, terms ['egocentric', 'hand', 'opening', 'door'], within 7 days

plan, checked against the policy (AI-training rights required, order <= $2,500):
   53  Prolific                           commission 
         x AI training rights not stated
   26  RentAHuman                         commission ok
   25  WeGoLook                           commission 
         x AI training rights not stated

best compliant: rentahuman (needs RENTAHUMAN_API_KEY; not set, so this is a dry run)
quoted via rentahuman: dry run, nothing sent
  held by policy: no quoted total to check against max_order_usd
  would send: POST https://rentahuman.ai/api/bounties
  {"title": "Film a hand opening a door, first person", "description": "500 first-person clips of a hand opening a door, 7 days", "price": 4, "estimatedHours": 1, "evidenceTypes": ["video"], "liveCaptureRequirement": true, "checkIns": {"enabled": true, "intervalMinutes": 30}, "location": {"isRemoteAllowed": false}, "spotsAvailable": 500}

Nothing was ordered. Only a person, after reading a quote under $2,500, calls job.submit(spec, confirm=True); this script never does.
```

## 3. Training loop: `active_learning.py`

A PyTorch-style loop with the model stubbed by `random`. `top_uncertain(model, 4)` returns the four hard driving scenes (their order varies per run), and each gets `irlpls.request` under `Policy(auto_submit=True, max_order_usd=50)`. With no keys, every order is a dry run.

```
$ python examples/active_learning.py
epoch 0: least sure about ['roadwork at night', 'cyclist at dusk', 'flooded street', 'right turn, snowy afternoon']

roadwork at night
  top provider: Hivemapper (Bee Maps) (hivemapper, fit 33)
  order: quoted via hivemapper (DRY RUN: nothing sent, nothing bought)
    held by policy: no quoted total to check against max_order_usd
    BEEMAPS_API_KEY not set
cyclist at dusk
  top provider: Hivemapper (Bee Maps) (hivemapper, fit 33)
  order: quoted via hivemapper (DRY RUN: nothing sent, nothing bought)
    held by policy: no quoted total to check against max_order_usd
    BEEMAPS_API_KEY not set
flooded street
  top provider: Hivemapper (Bee Maps) (hivemapper, fit 51)
  order: quoted via hivemapper (DRY RUN: nothing sent, nothing bought)
    held by policy: no quoted total to check against max_order_usd
    BEEMAPS_API_KEY not set
right turn, snowy afternoon
  top provider: Hivemapper (Bee Maps) (hivemapper, fit 51)
  order: quoted via hivemapper (DRY RUN: nothing sent, nothing bought)
    held by policy: no quoted total to check against max_order_usd
    BEEMAPS_API_KEY not set

4 of 4 requests were dry runs; no clips to label this epoch.
```
