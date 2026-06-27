#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Back-fill CSV formula-injection escaping onto already-committed data CSVs.

The scrapers escape on write (see csv_safety.escape_formula); this applies the
SAME transform to existing files in place, without re-hitting the API. It reads
and rewrites with the csv module's default dialect -- exactly what the scrapers
use -- so unchanged cells stay byte-identical and a later re-scrape of a
normalised day produces no diff. Idempotent: already-escaped cells (leading ')
are left alone, and a file is rewritten only if a cell actually changes.

    uv run python normalize_csv_escaping.py                 # all 20*/*.csv
    uv run python normalize_csv_escaping.py 2026/questions-thunderbird-desktop-2026-05-01.csv ...
"""

import csv
import glob
import sys

from csv_safety import escape_formula


def raise_field_limit():
    """Match the scrapers: allow very large `content` cells."""
    n = sys.maxsize
    while True:
        try:
            csv.field_size_limit(n)
            return
        except OverflowError:
            n = int(n / 10)


def normalize_file(path):
    """Escape every cell in `path`; rewrite only if something changed. Returns
    the number of cells changed."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    changed = 0
    out = []
    for row in rows:
        new = [escape_formula(c) for c in row]
        changed += sum(1 for a, b in zip(row, new) if a != b)
        out.append(new)

    if changed:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(out)
    return changed


def main():
    raise_field_limit()
    paths = sys.argv[1:] or sorted(glob.glob("20*/*.csv"))
    files_changed = cells_changed = 0
    for p in paths:
        n = normalize_file(p)
        if n:
            files_changed += 1
            cells_changed += n
            print(f"escaped {n} cell(s): {p}")
    print(f"\n{files_changed}/{len(paths)} files changed "
          f"({cells_changed} cells escaped)")


if __name__ == "__main__":
    main()
