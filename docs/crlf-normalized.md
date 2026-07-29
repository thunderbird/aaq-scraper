<!--
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->

# One-time CRLF -> LF normalization (2026-07-29)

Audit of the in-place line-ending back-fill applied by
`normalize_line_endings.py`, mirroring `docs/escaped-cells.md` for the
formula-escaping back-fill.

## What and why

The scrapers write LF (`lineterminator="\n"`), but **125 of 3997**
committed CSVs still terminated their records with CRLF, from before that fix.
Those days would only ever have been normalised if something happened to
re-scrape them, so the rest were back-filled here without touching the API.

| Year | Files |
|------|-------|
| 2023 | 2 |
| 2024 | 2 |
| 2025 | 5 |
| 2026 | 116 |
| **Total** | **125** |

**4,976** CRLF record separators were rewritten.

## Method

A **byte-level** rewrite (`\r\n` -> `\n`), not a CSV parse-and-rewrite, so
only the record separators change and every other byte -- quoting style,
escaping, field order -- is preserved. A re-scrape of a normalised day therefore
still produces no diff.

That is only safe where the rewrite cannot change what the CSV *means*, since a
global byte replace cannot distinguish an embedded CRLF inside a quoted cell
from a record separator. The script therefore parses each file **before and
after** in memory and **refuses** (loudly, leaving the file untouched) if the
two parses differ. On this corpus **0 files were skipped** -- all 125 were clean.

An earlier version approximated this by scanning cells for a bare CR/LF. Review
found that misses `\r\r\n`, which csv reads as an extra **empty** row: an empty
row has no cells, so the scan passed vacuously while the byte replace silently
dropped the blank row -- changing the parsed CSV while still satisfying the
byte-level check. No committed file contained `\r\r` (all 3997 were checked), so
nothing was corrupted, but the guard now enforces parse-equivalence directly
rather than approximating it.

## Regression guard

`.gitattributes` was empty, so nothing stopped CRLF being reintroduced by a
contributor's git config or a tool that rewrites with platform line endings, and
no CI check covers it. It now carries `*.csv text eol=lf`. Verified a no-op:
`git add --renormalize .` restages no data file.

## Verification

- Every file: parsed CSV content **identical** before and after.
- Every file: raw bytes differ **only** by removed `\r` -- checked as
  `old.replace(b"\r\n", b"\n") == new`.
- Idempotent: an immediate re-run converts 0.
- Header-based detection, so a file whose *content* happens to contain `\r\n`
  is not mistaken for a CRLF-terminated file.

## Relationship to the reconcile

Run immediately after the shakedown reconcile, which had already landed 4 of the
originally-129 CRLF files LF-native (they were rewritten wholesale from the
CronJob's LF output). Hence 125, not 129.
