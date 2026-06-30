#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Scan scraped CSVs for leaked credentials (read-only).

Reports, per match: file, csv row id, parent question id (answers) / question
id, and which pattern matched. Prints the secret only as a masked prefix so the
scan output itself is safe to paste into an issue. Detection patterns mirror the
high-confidence GitHub secret-scanning types most likely in forum paste content.
"""
import csv
import re
import sys
from glob import glob

PATTERNS = {
    "google_oauth_refresh_token": re.compile(r"1//0[A-Za-z0-9_-]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "aws_access_key_id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack_token": re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    "github_pat": re.compile(r"gh[ps]_[0-9A-Za-z]{36,}"),
}


def mask(s):
    return s[:6] + "…(redacted len=%d)" % len(s)


def main():
    files = sorted(glob("20*/*.csv"))
    hits = 0
    for path in files:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    continue
                idx = {name: i for i, name in enumerate(header)}
                rid = idx.get("id")
                qid = idx.get("question_id", idx.get("question"))
                for lineno, row in enumerate(reader, start=2):
                    blob = ",".join(row)
                    for label, pat in PATTERNS.items():
                        m = pat.search(blob)
                        if m:
                            hits += 1
                            row_id = row[rid] if rid is not None and rid < len(row) else "?"
                            q_id = row[qid] if qid is not None and qid < len(row) else "?"
                            print(f"{path}\tline={lineno}\trow_id={row_id}\t"
                                  f"question_id={q_id}\t{label}\t{mask(m.group(0))}")
        except Exception as e:  # noqa: BLE001
            print(f"# ERROR reading {path}: {e}", file=sys.stderr)
    print(f"\n# total matches: {hits}", file=sys.stderr)


if __name__ == "__main__":
    main()
