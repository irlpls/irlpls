# Add a card

Ten minutes, one JSON file.

## The path

1. **Start from the template.** Copy `examples/card-template.json` (also at [card-template.json](card-template.json)) to `cards/<id>.json`. The id is lowercase letters, digits and hyphens, and the file name is `<id>.json`. Every field is explained in `examples/card-template.md`.
2. **Fill it from public pages.** Set `updated` to today and `expires` to today plus one year. Replace each value. Delete what does not apply. Add a source for every claim.
3. **Run the gate.** "The gate" means exactly these commands, about two seconds in all, nothing to install:
   `python3 tools/gate.py cards/<id>.json`, then `python3 tools/check_cards.py`, then `python3 build.py`.
   Each error names the field, what is expected, what was found and a one-line fix. Warnings are advice; read them. (`irlpls validate cards/<id>.json` is the same schema check, but only works once the package is installed.) The full test suite takes 10+ minutes and needs a browser; you do not need to run it, CI does. For a quick extra check: `pytest -q tests/test_match_bench.py tests/test_family_field.py tests/test_page_weight.py` (about 30 seconds).
4. **Pick a family.** Set the card's own `family` field to the family whose title best matches what the card reads or does, for example `"family": "river-water-level"`. Use one of these ids; if none fits, use the closest and say so in the pull request. You do not edit any code to add a card.

| Family id | What it covers |
|---|---|
| `river-water-level` | River and stream level, flow, flood |
| `coast-tide-wave` | Tide, sea level, waves, buoys |
| `weather-station` | Weather at a station: temperature, wind, rain, lightning |
| `air-quality` | Air quality, PM2.5, smoke, AQI |
| `parking-occupancy` | Car park occupancy and free spaces |
| `earthquake` | Earthquakes and volcano alert levels |
| `wildfire-detection` | Active fire detections from satellites |
| `ship-positions` | Ship positions (AIS) |
| `traffic-road-camera` | Traffic and road cameras |
| `wildfire-camera` | Wildfire-watch cameras |
| `webcam-anywhere` | Public webcams worldwide |
| `photo-of-address` | Photo or check of a specific address |
| `store-shelf-check` | Shelf photos, price checks, mystery shopping |
| `errand-anywhere` | A person does an errand or checks something in person |
| `address-verification` | Address verification by field agent |
| `property-scan-3d` | Homeowner phone scan to a 3D property model |
| `street-level-imagery` | Fresh street-level imagery from a dashcam network |
| `drone-capture` | Drone photos, surveys, inspections |
| `satellite-optical` | New optical satellite image of an area |
| `satellite-radar` | Radar (SAR) image, through cloud and at night |
| `satellite-thermal-hyperspectral` | Thermal infrared or hyperspectral satellite image |
| `free-latest-satellite` | Newest free satellite pass (Sentinel) |
| `dataset-egocentric` | First-person (egocentric) video and robot-learning data |
| `dataset-driving` | Driving and dashcam video for AI training |
| `dataset-crowd-media` | Custom photos, video, audio or speech from a contributor crowd |
| `survey-participants` | Survey or study participants |

5. **Check once more.** After the edits from steps 3 and 4, run the gate again. Both tools say in plain words what to fix, naming the card.
6. **Send it.** Add one line to `CHANGELOG.md` (card id, what it reads). Open a pull request and paste the output of `python3 tools/gate.py cards/<id>.json` and the curl you used to check the feed answers; or email the file to hello@irlpls.com.

You do not need to touch tests. A new card in an existing family does not change the matcher benchmark; a bench row is only needed when you change matching itself (`docs/DEVELOPING.md`). If a test fails after your card is added, paste the failing test name in the pull request and a maintainer will deal with it.

The page source is this file, `docs/ADD-A-CARD.md`; `build.py` renders it as `add-a-card.html`.

If you are the provider, you do not need steps 4 to 6: serve the card at `/.well-known/irlpls` on your own domain ([for providers](providers.html)).

## What makes a card good

- **A source for every claim.** `sources` lists `{claim, url}`. If a public page does not say it, the card does not say it.
- **Honest levels.** `evidenced` needs a method and a public `evidence_url` anyone can check. `claimed` is the provider's word. `none` is fine.
- **"Not stated" is fine.** Leave a right, a price or a speed out when the provider does not publish it. A short true card beats a long guessed one. The gate prints one warning line listing the rights you left out; that is expected.
- **`unknown` is never empty.** One line on what remains uncertain, for every link.
- **Needed to be listed:** `short`, `access` and `api`, as well as the fields the schema requires. `irlpls validate` warns when one is missing.

Nothing buys rank. A card is ranked by fit to the ask alone. A key such as `sponsored`, `boost` or `rank_boost` is a gate error, and words like "best", "leading" or "premium" in `name`, `short` or `summary` draw a warning: state what the provider serves, not how good it is.

**Wording that trips the maintainer release scan.** A maintainer-side check runs before anything is published and flags some words, including a short personal name that also appears inside some real place names. It is not part of the gate, so your card passes without it. Write the place name correctly anyway; a maintainer may reword it later and that is not a problem with your card.

## Claim, correct or remove your card

**Claim it.** A card compiled from public pages is marked `unclaimed`. Serve a card with the same `id` at `https://<your-domain>/.well-known/irlpls`, on the domain in its `provider_url`, and the next daily read shows it as provider-maintained. Your statement then replaces the compiled one. The steps are in [for providers](providers.html), and you can rehearse them on your own machine first.

**Correct it.** Email hello@irlpls.com with the card id and the source (a public page) for the right fact. The card page also has a Report a correction link that writes the same email.

**Remove it.** Email hello@irlpls.com with the card id and the source for what is wrong, or say that you are the provider and want the card withdrawn.

**What irlpls does.** We read every message. A correction that a public source supports is made, and the change is recorded in the changelog. A card that overstates what a provider does is corrected, downgraded or withdrawn; it is not kept live while we know it is wrong.
