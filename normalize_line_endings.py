#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Normalise CRLF-terminated data CSVs to LF, in place and without the API.

The scrapers write LF (`lineterminator="\\n"`), but files committed before that
fix still terminate records with CRLF. Those days would only be normalised if
and when something happened to re-scrape them, so this back-fills the rest --
the same pattern as normalize_csv_escaping.py, which back-filled formula
escaping onto committed data.

Deliberately a BYTE-level rewrite (`\\r\\n` -> `\\n`) rather than a csv
parse-and-rewrite: it changes only the record separators and leaves every other
byte -- quoting style, escaping, field order -- exactly as it was, so the diff
is provably just line endings and a later re-scrape of a normalised day still
produces no diff.

That is only safe while no CR or LF appears INSIDE a quoted field, because a
global byte replace cannot tell an embedded CRLF from a record separator. The
scrapers strip newlines from `content`, and all 125 affected files were verified
clean, but data is untrusted: each file is CSV-parsed first and SKIPPED (loudly)
if any cell contains a bare CR or LF. Refusing is the safe failure here --
corrupting user content silently is not.

Idempotent: a file already LF-terminated is left untouched and reported as such.

    uv run python normalize_line_endings.py            # all 20*/*.csv
    uv run python normalize_line_endings.py --check    # report only, change nothing
    uv run python normalize_line_endings.py 2026/questions-thunderbird-desktop-2026-05-20.csv
"""

import csv
import glob
import os
import sys


def raise_field_limit():
    """Match the scrapers: allow very large `content` cells."""
    n = sys.maxsize
    while True:
        try:
            csv.field_size_limit(n)
            return
        except OverflowError:
            n //= 10


def is_crlf_terminated(path):
    """True if the HEADER line ends CRLF.

    The header is generated, never user content, so it is the reliable signal
    for how the file's records are terminated. Counting `\\r\\n` anywhere in the
    file would also match a CR that legitimately sits inside a quoted cell.
    """
    with open(path, "rb") as fh:
        return fh.readline().endswith(b"\r\n")


def has_embedded_newline(path):
    """True if any CELL contains a bare CR or LF (byte rewrite unsafe)."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if any("\r" in c or "\n" in c for c in row):
                return True
    return False


def normalize(path):
    """Rewrite CRLF -> LF in place. Returns 'converted' | 'clean' | 'skipped'."""
    if not is_crlf_terminated(path):
        return "clean"
    if has_embedded_newline(path):
        return "skipped"
    with open(path, "rb") as fh:
        data = fh.read()
    out = data.replace(b"\r\n", b"\n")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(out)
    os.replace(tmp, path)  # atomic: a kill mid-write can't truncate the CSV
    return "converted"


def main():
    raise_field_limit()
    args = [a for a in sys.argv[1:] if a != "--check"]
    check_only = "--check" in sys.argv[1:]
    paths = args or sorted(glob.glob("20*/*.csv"))

    counts = {"converted": 0, "clean": 0, "skipped": 0}
    for p in paths:
        if check_only:
            if not is_crlf_terminated(p):
                counts["clean"] += 1
            elif has_embedded_newline(p):
                counts["skipped"] += 1
                print(f"WOULD SKIP (embedded newline): {p}")
            else:
                counts["converted"] += 1
                print(f"WOULD CONVERT: {p}")
            continue
        r = normalize(p)
        counts[r] += 1
        if r == "converted":
            print(f"converted: {p}")
        elif r == "skipped":
            print(f"SKIPPED (embedded newline, left CRLF): {p}", file=sys.stderr)

    verb = "would convert" if check_only else "converted"
    print(f"\n{verb} {counts['converted']}, already LF {counts['clean']}, "
          f"skipped {counts['skipped']}, of {len(paths)} file(s)")
    return 1 if counts["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
