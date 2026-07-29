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

That is only safe while no CR or LF appears inside a quoted cell, since a global
byte replace cannot distinguish an embedded CRLF from a record separator. The
script CSV-parses each file first and **refuses** (loudly, leaving the file
untouched) if any cell contains a bare CR or LF. On this corpus **0 files were
skipped** -- all 125 were clean.

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
