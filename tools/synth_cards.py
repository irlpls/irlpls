#!/usr/bin/env python3
"""Deterministic synthetic Capture Card generator for scale testing (never writes into cards/ or site/).

Clones real cards from cards/ into N schema-valid synthetic cards: new id, a clearly synthetic provider name,
status 'sample' (fictional), and coverage drawn from real ISO country codes plus a few real city names.
Nothing else about a clone is invented: capabilities, tags, verification etc. come from the template unchanged
(except adapter and provider_url, which would point at a real provider).

    python3 tools/synth_cards.py --out DIR --n 10000 --seed 1
"""
import argparse
import copy
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (ISO code, country name, a few real cities)
PLACES = [
    ("NZ", "New Zealand", ["Wellington", "Dunedin"]), ("CL", "Chile", ["Valparaiso", "Concepcion"]),
    ("AR", "Argentina", ["Rosario", "Mendoza"]), ("PE", "Peru", ["Arequipa", "Cusco"]),
    ("IS", "Iceland", ["Akureyri", "Reykjavik"]), ("NO", "Norway", ["Bergen", "Tromso"]),
    ("FI", "Finland", ["Tampere", "Oulu"]), ("EE", "Estonia", ["Tartu", "Parnu"]),
    ("LV", "Latvia", ["Riga", "Daugavpils"]), ("LT", "Lithuania", ["Kaunas", "Vilnius"]),
    ("UY", "Uruguay", ["Montevideo", "Salto"]), ("BO", "Bolivia", ["Sucre", "Cochabamba"]),
    ("MN", "Mongolia", ["Darkhan", "Erdenet"]), ("KZ", "Kazakhstan", ["Karaganda", "Aktobe"]),
    ("NA", "Namibia", ["Windhoek", "Walvis Bay"]), ("BW", "Botswana", ["Gaborone", "Maun"]),
]


def is_inside(path, base):
    p, b = pathlib.Path(path).resolve(), pathlib.Path(base).resolve()
    return p == b or b in p.parents


def clone(t, i, rng, places=PLACES):
    c = copy.deepcopy(t)
    k = rng.randint(1, 3)
    pick = rng.sample(places, k)
    c["id"] = f"synth-{i:05d}"
    c["name"] = f"Synthetic Provider {i:05d}"
    c["status"] = "sample"
    c["provider_url"] = f"https://example.com/synthetic/{i:05d}"
    c.pop("adapter", None)
    c["summary"] = "Synthetic scale-test card (fictional). " + c.get("summary", "")
    cov = c["coverage"]
    cov["areas"] = [p[0] for p in pick]
    cov["regions"] = [p[1] for p in pick]
    cov["notes"] = "Synthetic coverage for scale testing, including " + ", ".join(
        rng.choice(p[2]) for p in pick) + "."
    return c


def generate(templates, n, seed):
    """n cards, a pure function of (templates, n, seed)."""
    rng = random.Random(seed)
    return [clone(templates[rng.randrange(len(templates))], i, rng) for i in range(n)]


def load_templates(cards_dir=None):
    d = pathlib.Path(cards_dir or ROOT / "cards")
    out = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
    return [c for c in out if c.get("status") != "sample" and c.get("coverage")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    for forbidden in (ROOT / "cards", ROOT / "site"):
        if is_inside(a.out, forbidden):
            sys.exit(f"refusing to write synthetic cards inside {forbidden.name}/")
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for c in generate(load_templates(), a.n, a.seed):
        (out / f"{c['id']}.json").write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {a.n} synthetic cards to {out}")


if __name__ == "__main__":
    main()
