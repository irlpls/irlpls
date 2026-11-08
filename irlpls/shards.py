"""Static catalog shards (docs/research/strategy/SCALE-SEARCH-ARCH.md sections 1, 4, 7 step 7; first slice, additive).

Writes site/shards/ after the normal build and touches nothing else:
  manifest.json            version, build id, counts, every shard and pack with bytes/sha256, id -> pack map
  <KEY>[.<modality>[.N]].json   JSON arrays of the cards-lite records; KEY is an ISO country code or "global"
  packs/pack-NNNN.json     256 FULL card records each, in id order

A lookup for country X reads every shard whose manifest entry lists X, plus those listed under "global".
Cards claiming "*" (or no area at all) are global. A subdivision (US-CA) files under its country (US).
File count is O(countries + N/256). Deterministic: same cards in, same bytes out.
"""
import hashlib
import json
import pathlib

VERSION = 1
PACK_SIZE = 256
SHARD_MAX = 1 << 20          # 1 MiB per shard
SITE_FILES_MAX = 15_000      # Cloudflare Pages allows 20,000 files; keep headroom
FILE_MAX = 25 * (1 << 20)    # Cloudflare Pages: 25 MiB per file
GLOBAL = "global"


def _compact(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def keys_of(card):
    """Sorted shard keys a card belongs to: country codes, or ["global"]."""
    areas = (card.get("coverage") or {}).get("areas") or []
    ks = {a.split("-")[0] for a in areas if a != "*" and len(a) >= 2}
    if not areas or "*" in areas:
        ks.add(GLOBAL)
    return sorted(ks)


def _modality(card):
    m = sorted((card.get("capabilities") or {}).get("modalities") or [])
    return m[0] if m else "other"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _split(key, items, dump, limit):
    """items: [(id, modality, lite)] sorted by id. One shard, else by modality group, else by chunk."""
    def enc(group):
        return dump([r for _, _, r in group]).encode("utf-8")
    whole = enc(items)
    if len(whole) <= limit:
        return [(key, whole, items)]
    out = []
    groups = {}
    for it in items:
        groups.setdefault(it[1], []).append(it)
    for mod in sorted(groups):
        g = groups[mod]
        b = enc(g)
        if len(b) <= limit:
            out.append((f"{key}.{mod}", b, g))
            continue
        n, i = 0, 0
        while i < len(g):  # greedy chunks that fit
            step = max(1, min(len(g) - i, len(g) * limit // len(b)))
            cb = enc(g[i:i + step])
            while len(cb) > limit and step > 1:
                step = max(1, step * 3 // 4)
                cb = enc(g[i:i + step])
            out.append((f"{key}.{mod}.{n}", cb, g[i:i + step]))
            n, i = n + 1, i + step
    return out


def write_shards(cards, lite, out_dir, dump=_compact, limit=SHARD_MAX):
    """cards: full records; lite: the build's lite(card) function. Returns the manifest dict (also written)."""
    out = pathlib.Path(out_dir)
    if out.exists():
        for f in sorted(out.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
    (out / "packs").mkdir(parents=True, exist_ok=True)
    cards = sorted(cards, key=lambda c: c["id"])
    by_key = {}
    for c in cards:
        rec = (c["id"], _modality(c), lite(c))
        for k in keys_of(c):
            by_key.setdefault(k, []).append(rec)
    shards = []
    for key in sorted(by_key):
        for name, blob, group in _split(key, by_key[key], dump, limit):
            path = f"{name}.json"
            (out / path).write_bytes(blob)
            shards.append({"path": path, "key": key, "bytes": len(blob), "cards": len(group), "sha256": _sha(blob)})
    packs, pack_of = [], {}
    for i in range(0, len(cards), PACK_SIZE):
        chunk = cards[i:i + PACK_SIZE]
        blob = dump(chunk).encode("utf-8")
        path = f"packs/pack-{i // PACK_SIZE:04d}.json"
        (out / path).write_bytes(blob)
        packs.append({"path": path, "bytes": len(blob), "cards": len(chunk), "sha256": _sha(blob)})
        for c in chunk:
            pack_of[c["id"]] = i // PACK_SIZE
    build_id = _sha("".join(e["sha256"] for e in shards + packs).encode())[:16]
    manifest = {"version": VERSION, "build": build_id, "cards": len(cards), "pack_size": PACK_SIZE,
                "keys": sorted(by_key), "shards": shards, "packs": packs, "pack_of": pack_of}
    (out / "manifest.json").write_bytes((json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return manifest


def check_budgets(out_dir, site_dir=None):
    """Raise ValueError listing every budget breach: shard over 1 MiB, any file over 25 MiB, site over 15,000 files."""
    out = pathlib.Path(out_dir)
    bad = []
    man = json.loads((out / "manifest.json").read_text())
    for s in man["shards"]:
        if s["bytes"] > SHARD_MAX:
            bad.append(f"shard {s['path']} is {s['bytes']} bytes (max {SHARD_MAX})")
    for f in out.rglob("*"):
        if f.is_file() and f.stat().st_size > FILE_MAX:
            bad.append(f"{f.name} is over {FILE_MAX} bytes")
    root = pathlib.Path(site_dir) if site_dir else out
    n = sum(1 for f in root.rglob("*") if f.is_file())
    if n > SITE_FILES_MAX:
        bad.append(f"{n} files under {root.name}/ (max {SITE_FILES_MAX})")
    if bad:
        raise ValueError("shard budget failed:\n  " + "\n  ".join(bad))
    return n
