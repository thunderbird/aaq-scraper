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

That is only safe where the rewrite cannot change what the CSV *means* -- a
global byte replace cannot tell an embedded CRLF inside a quoted cell from a
record separator. Data here is untrusted, so rather than approximate that with a
cell scan, the file is parsed before and after in memory and SKIPPED (loudly) if
the two parses differ. Enforcing it directly matters: a cell-level check misses
`\\r\\r\\n`, which csv reads as an extra EMPTY row -- no cells to scan, so the
check passes vacuously while the byte replace silently drops the blank row.
Refusing is the safe failure here; corrupting data is not.

Idempotent: a file already LF-terminated is left untouched and reported as such.

    uv run python normalize_line_endings.py            # all 20*/*.csv
    uv run python normalize_line_endings.py --check    # report only, change nothing
    uv run python normalize_line_endings.py 2026/questions-thunderbird-desktop-2026-05-20.csv
"""

import csv
import glob
import io
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


def _parse(data):
    """Parse CSV bytes to a list of rows, or None if it isn't decodable."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return list(csv.reader(io.StringIO(text, newline="")))


def changes_parsed_content(before, after):
    """True if the rewrite would alter the parsed CSV at all.

    This ENFORCES the safety property rather than approximating it. An earlier
    version only scanned cells for a bare CR/LF, which misses `\\r\\r\\n`: csv
    reads that as an extra EMPTY row, and an empty row has no cells for a
    cell-level scan to look at, so the check passed vacuously while the byte
    replace silently dropped the blank row. Comparing the parse directly closes
    that hole and any other pathological input, at the cost of one extra parse.
    """
    b, a = _parse(before), _parse(after)
    return b is None or a is None or b != a


def normalize(path):
    """Rewrite CRLF -> LF in place. Returns 'converted' | 'clean' | 'skipped'."""
    if not is_crlf_terminated(path):
        return "clean"
    with open(path, "rb") as fh:
        data = fh.read()
    out = data.replace(b"\r\n", b"\n")
    if changes_parsed_content(data, out):
        return "skipped"
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
            else:
                data = open(p, "rb").read()
                if changes_parsed_content(data, data.replace(b"\r\n", b"\n")):
                    counts["skipped"] += 1
                    print(f"WOULD SKIP (rewrite would alter parsed content): {p}")
                    continue
                counts["converted"] += 1
                print(f"WOULD CONVERT: {p}")
            continue
        r = normalize(p)
        counts[r] += 1
        if r == "converted":
            print(f"converted: {p}")
        elif r == "skipped":
            print(f"SKIPPED (rewrite would alter parsed content): {p}", file=sys.stderr)

    verb = "would convert" if check_only else "converted"
    print(f"\n{verb} {counts['converted']}, already LF {counts['clean']}, "
          f"skipped {counts['skipped']}, of {len(paths)} file(s)")
    return 1 if counts["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
