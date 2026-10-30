# Developing

Private repo, one maintainer. All commands run from the repo root with `uv run`; the core package has no dependencies.

## Gate commands

```bash
# 1. Build the site and the bundled snapshot (irlpls/data/cards.json). Add snapshot_media.py first for landing changes (network).
uv run python tools/snapshot_media.py      # optional
uv run python build.py

# 2. Tests (~215; tests needing node, playwright or mcp>=2 skip when missing)
uv run --with pytest --with jsonschema --with playwright --with 'mcp>=2' pytest -q tests

# 3. Cards against the schema, and JS syntax
uv run --with jsonschema irlpls validate cards/*.json
node --check src/match.js && node --check site/match.js && node --check site/app.js

# 4. Landing changes only: must print "QA clean"; look at the screenshots
uv run --with playwright python tools/qa.py OUTDIR
```

The UI tests need a browser once: `uv run --with playwright python -m playwright install chromium`.
`site/` is untracked and generated; do not run the build while another process is using it.

## Add an adapter

1. The card exists in `cards/` and carries `"adapter": "<module_name>"`.
2. Copy the closest file in `irlpls/adapters/` (keyless feed: `uk_ea_flood.py`; keyed feed: `openaq.py`; commission: `skyfi.py`). Subclass `Adapter` (`base.py`): set `card_id` (the card's `id`), `can_live` / `can_commission`, `env` and `how_to_get` if a key is needed. Implement `live(lat, lon, radius_km, limit)` and/or `commission(spec, confirm=False)`; add `status()` and `status_map` only with statuses the provider's docs list.
3. Return results with `observation(...)` (`phenomenon_time` is when it happened); report failures with `warning(...)`; never hide one.
4. A commission is a dry run (`self._dry(...)`) unless `confirm=True`.
5. Import it and add an instance to the `ADAPTERS` dict in `irlpls/adapters/__init__.py`.
6. Test with a recorded response (see `test_uk_ea_flood_returns_latest_level_with_units` in `tests/test_core.py`). `test_every_adapter_has_a_card_and_mode` already checks the card and its modes.
7. A keyless live adapter also goes in the `checks` dict of `health()` in `tools/snapshot_media.py`.
8. List it in the adapter paragraph of `README.md`, then run the gate and add a `CHANGELOG.md` line.

Check it: `uv run irlpls adapters`, then `uv run irlpls live <card-id> --at "Austin, TX"` (network).

## Change matching (Python and JS must agree)

1. Edit `irlpls/match.py` (and `need.py` / `geo.py` for parsing) and `src/match.js` together. `build.py` copies `src/match.js` to `site/match.js`.
2. Add the query that motivated the change to the bench in `tests/test_match_bench.py`: the acceptable top provider, and providers that must not appear.
3. `test_js_parity` runs the same queries through `src/match.js` under node and requires the same top result and fit within 2. It skips without node, so make sure node is installed.
4. If the formula or a rule changes, update Section 7 of `docs/STANDARD.md` and `CHANGELOG.md`.
5. Run the gate (build first, so `site/match.js` is current).

## Add a card field

1. Schema: `schema/capture-card.schema.json` (or request, job, policy). New fields are optional within a date version; removing or renaming one needs a new date and a migration note.
2. Validate: `irlpls/validate.py` if the schema cannot state the rule. Check with `uv run --with jsonschema irlpls validate cards/*.json`.
3. Build: show it in `build.py` / `src/app.js`; touch `match.*` only if it should affect fit. Cards that should carry it get edited, then rebuild so `irlpls/data/cards.json` is refreshed.
4. Tests: add one in `tests/test_core.py` (and a bench row if matching changed).
5. Docs and changelog: the field tables in `docs/STANDARD.md`, `docs/AGENTS.md` if agents read it, and an entry in `CHANGELOG.md`.
6. Run the gate.
