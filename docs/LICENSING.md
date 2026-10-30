# Licensing

| Part | Licence |
|---|---|
| Code (`irlpls/`, `tools/`, `build.py`, `tests/`, `schema/`, `site/` templates) | Apache-2.0, see `LICENSE` |
| Public Suffix List snapshot (`irlpls/data/public_suffix_list.dat`) | MPL-2.0, see below |
| Capture Card data (`cards/`) | CC BY 4.0, see `LICENSE-DATA.md` |
| Documentation prose in `docs/` | CC BY 4.0 unless a file says otherwise |
| Media in `src/media/` | Third-party, each file under its own licence (below) |

Copyright holder for our own work: the irlpls maintainers, 2026.

## Third-party media

The photos and video in `src/media/` are not ours. They are not relicensed
under Apache-2.0 or CC BY 4.0. Each keeps the licence recorded in
`src/media/CREDITS.md`, which lists author, source and licence per file. The
mix includes public domain, CC0, CC BY 2.0 / 4.0, CC BY-SA 2.0 / 3.0 / 4.0, and
the Pexels License for `ego-door.mp4`. Share-alike (CC BY-SA) files stay
share-alike; credit the author as listed. Files were resized or trimmed as
noted there.

`src/media/live/` holds river camera frames fetched from USGS (public domain).

If you redistribute the repository or site, keep `CREDITS.md` with the media,
or drop the media. If you are a rights holder and want a file credited
differently or removed, use the contact in `SECURITY.md`.

## Other third-party items

`irlpls/data/public_suffix_list.dat` is a snapshot of the Public Suffix List
(https://publicsuffix.org/list/public_suffix_list.dat), fetched 2026-10-28
(list version 2026-10-22_08-36-41_UTC, commit 8437e526). It is licensed
under the Mozilla Public License 2.0 (https://mozilla.org/MPL/2.0/), not
Apache-2.0; the file keeps its own licence header, unmodified below our
provenance note. `irlpls/psl.py` (Apache-2.0) only reads it.

Provider names, logos and marks belong to their owners and are used only to
identify them. Python dependencies keep their own licences.
