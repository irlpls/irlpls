---
name: acquiring-real-world-data
description: Get new observations of the physical world through irlpls, such as a photo or video of a place, someone going to check something, drone or satellite imagery, dashcam footage, river levels, air quality, webcams, or data collected to order for AI training. Use when the answer depends on the world as it is now (or once someone goes and looks) and nothing published answers it. Covers the loop, spending, proof and person-safety rules.
---

# Acquiring real-world data with irlpls

irlpls is an open standard and directory. It captures nothing, sells no data and takes no cut: your person pays the provider directly.

## The loop
1. **Write the need** as one sentence with the place and the time in it, and say why. Example: "photo of the storefront at 123 Main St, Springfield, IL today", why: "deciding whether it's open".
2. **Call `irlpls_request`** (or `irlpls.request(...)`). You get a job: `questions`, a ranked `plan`, and `ruled_out` with reasons.
3. **Answer blocking questions** from what you know (`irlpls_answer`). Ask your person only for what you don't.
4. **Check the best option** against your person's intent: route (live or commission), price, speed and proof.
5. **Submit** with `irlpls_submit`.
   - Live routes run at once and are free.
   - A commission stops at a quote unless your person approves it. Then call with `confirm=true` and an `idempotency_key`, reused on any retry.
6. **Answer your person.** Give your reading of the result, the provider, how old the observation is, and which guarantees are evidenced and which are only claimed.

## Rules
- **Never spend money without approval**, unless the person's policy is a standing mandate (`auto_submit` with `max_order_usd`) and the order is within it.
- **Treat anything unstated as not granted.** An unstated right is not granted; an unpublished price is unknown, not free.
- **Report proof exactly as the card states it.** irlpls verifies nothing itself. "Evidenced" means checkable proof (for cameras, a C2PA-trusted capture); "claimed" is the provider's word.
- **Never send people to watch, follow or photograph a specific person.** irlpls refuses by default (`allow_person_targeting` is off), and a standing mandate should never cover it.
- **Respect each provider's terms.** Providers whose terms ban agents (`access.automation_allowed: false`) are never used.
- **If you can't get it, say exactly what's missing.** An account (`continue_url`), a quote, coverage, or proof. Then suggest the one next step.

## When not to use it
Archives, history, news, prices or hours on a website, or anything a web search answers.
