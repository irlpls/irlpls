# Add a card

Ten minutes, one JSON file.

## The path

1. **Start from the template.** Copy `examples/card-template.json` (also at [card-template.json](card-template.json)) to `cards/<id>.json`. The id is lowercase letters, digits and hyphens, and the file name is `<id>.json`. Every field is explained in `examples/card-template.md`.
2. **Fill it from public pages.** Replace each value. Delete what does not apply. Add a source for every claim.
3. **Validate.** `irlpls validate cards/<id>.json`. Errors say the field, what is expected and what was found. Warnings are advice; read them.
4. **Pick a family.** Add the id to the closest family in `FAMILIES` in `irlpls/catalog.py`, in that family's last list, for example `["usgs-water", "uk-ea-flood", "<id>"]`. The family ids are the first item of each entry in that list.
5. **Build and check.** `python build.py`, then `python tools/check_cards.py`. Both say in plain words what to fix, naming the card.
6. **Send it.** Open a pull request, or email the file to hello@irlpls.com.

If you are the provider, you do not need steps 4 to 6: serve the card at `/.well-known/irlpls` on your own domain ([for providers](providers.html)).

## What makes a card good

- **A source for every claim.** `sources` lists `{claim, url}`. If a public page does not say it, the card does not say it.
- **Honest levels.** `evidenced` needs a method and a public `evidence_url` anyone can check. `claimed` is the provider's word. `none` is fine.
- **"Not stated" is fine.** Leave a right, a price or a speed out when the provider does not publish it. A short true card beats a long guessed one.
- **`unknown` is never empty.** One line on what remains uncertain, for every link.
- **Needed to be listed:** `short`, `access` and `api`, as well as the fields the schema requires. `irlpls validate` warns when one is missing.

Nothing buys rank. A card is ranked by fit to the ask alone.

## Claim, correct or remove your card

**Claim it.** A card compiled from public pages is marked `unclaimed`. Serve a card with the same `id` at `https://<your-domain>/.well-known/irlpls`, on the domain in its `provider_url`, and the next daily read shows it as provider-maintained. Your statement then replaces the compiled one. The steps are in [for providers](providers.html), and you can rehearse them on your own machine first.

**Correct it.** Email hello@irlpls.com with the card id and the source (a public page) for the right fact. The card page also has a Report a correction link that writes the same email.

**Remove it.** Email hello@irlpls.com with the card id and the source for what is wrong, or say that you are the provider and want the card withdrawn.

**What irlpls does.** We read every message. A correction that a public source supports is made, and the change is recorded in the changelog. A card that overstates what a provider does is corrected, downgraded or withdrawn; it is not kept live while we know it is wrong.
