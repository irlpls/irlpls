# Starter card: what each field means

`card-template.json` is a complete Capture Card for a fictional provider, Example Weather Co. Copy it to `cards/<id>.json`
(or to `.well-known/irlpls` on your own site), replace every value, delete what does not apply, and run
`irlpls validate` on it. JSON has no comments, so every field is explained here.

State only what a public page says, and cite it. Where you do not know, say so: "Not stated" is a good answer.

| Field | What to put |
|---|---|
| `spec_version`, `capability` | Leave as they are. |
| `id` | Lowercase letters, digits and hyphens. The file name must be `<id>.json`. |
| `name` | The provider's name, as it writes it. |
| `status` | `unclaimed`. A card served from the provider's own domain is shown as provider-maintained; do not set it by hand. |
| `provider_url` | The provider's own site, `https://`. |
| `short` | One line, up to 120 characters. Needed to be listed. |
| `summary` | A few sentences: what is captured, where, how it is reached. Up to 4000 characters. |
| `tags` | A few plain words people would search for. |
| `capabilities` | `modalities` (photo, video, audio, aerial, sensor, text-report) and `tasks` (what can be asked for, in plain words). |
| `pricing` | `model`, `from_usd`, `unit`. `from_usd: 0` means "not published" or free; say which in `notes`. |
| `latency` | `typical` and `notes`: how long it takes, in the provider's words. Informational. |
| `delivery` | `modes`: `live` (readings now) or `commission` (someone goes and captures). `speed`: minutes, hours, days, weeks or unknown. `basis`: the statement that speed comes from. This is what matching uses. |
| `freshness` | `mode` (live, on_demand, archive, mixed) and `typical_age`. |
| `coverage` | `regions` and `notes` in words; `areas` as ISO codes (`US`, `US-OH`), or `*` when no limit is stated. |
| `licensing` | `summary`, and `ai_training` and `resale` as `allowed` or `forbidden`. Leave one out when the terms do not say; it then counts as not granted. |
| `access` | Needed to be listed. `cost`: free, free with key, freemium, paid or quote. `key_required`. `gate`: none (self-serve), approval, contract or enterprise. |
| `api` | Needed to be listed. `available`, `self_serve`, `docs_url`, and `auth`: a list of `{type, ...}` where type is none, api_key_header, api_key_query, bearer, oauth2, basic or other. |
| `capture` | `by`: people, contributors, devices or subject. |
| `verification` | All seven entries: human, application, camera, time, location, content, corroboration. Each has `level` (`none`, `claimed`, `evidenced`) and `unknown`: one line on what remains uncertain. Never leave `unknown` empty. `evidenced` also needs `method` and a public `evidence_url`; otherwise use `claimed`. |
| `updated`, `expires` | `YYYY-MM-DD`. `expires` is at most one year after `updated`. |
| `sources` | `{claim, url}` for each claim. Required while the card is `unclaimed`. |

Next: [docs/ADD-A-CARD.md](../docs/ADD-A-CARD.md).
