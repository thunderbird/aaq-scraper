#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Import a raw-JSON bundle produced by the browser extension (see extension/)
into the same CSVs the Playwright scrapers emit.

This is the offline half of the "genuine browser" stopgap for the Fastly
automated-browser fingerprinting block (issue #29, see #26): a real Firefox/
Chrome extension does the challenge-passing fetch and downloads a bundle of the
*raw, unflattened* API objects; this script runs them through the EXISTING,
unchanged flatten/build/write helpers so the output is byte-for-byte identical
to a scrape_questions.py / scrape_answers.py run of the same day.

Purely additive: it only imports from the existing modules and never edits them.

Bundle shape (JSON):
    {
      "product": "thunderbird" | "thunderbird-android",   # API product slug
      "start":   [YYYY, M, D],                            # window start day (UTC)
      "end":     [YYYY, M, D],                            # window end day (UTC)
      "questions": [ {...raw question...}, ... ],
      "answers":   [ {...raw answer...},   ... ]          # optional
    }

Run:
    uv run python import_json.py aaq-thunderbird-2026-06-10.json
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from scrape_answers import COLUMNS, flatten_answer
from scrape_questions import build_fieldnames, default_output_path, flatten_question

# Match the repo convention: allow very large `content` fields. (Importing sumo,
# transitively, already raises this; we set it explicitly too.)
_limit = sys.maxsize
while True:
    try:
        csv.field_size_limit(_limit)
        break
    except OverflowError:
        _limit = int(_limit / 10)


def _write_csv(rows, fieldnames, out, restval=None):
    """Atomic write (tmp + os.replace), matching the scrapers exactly."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp"
    kwargs = {"fieldnames": fieldnames, "extrasaction": "ignore"}
    if restval is not None:
        kwargs["restval"] = restval
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, **kwargs)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, out)


def _require(bundle, key):
    if key not in bundle:
        sys.exit(f"error: bundle is missing required key {key!r}")
    return bundle[key]


def main():
    p = argparse.ArgumentParser(
        description="Import an extension JSON bundle into questions/answers CSVs")
    p.add_argument("bundle", help="path to the JSON bundle from the extension")
    p.add_argument("--questions-out", default=None,
                   help="override the questions CSV path")
    p.add_argument("--answers-out", default=None,
                   help="override the answers CSV path")
    args = p.parse_args()

    try:
        with open(args.bundle, encoding="utf-8") as f:
            bundle = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"error: could not read JSON bundle {args.bundle!r}: {e}")
    if not isinstance(bundle, dict):
        sys.exit("error: bundle must be a JSON object")

    product = _require(bundle, "product")
    start = _require(bundle, "start")
    end = _require(bundle, "end")
    questions = _require(bundle, "questions")
    try:
        start_dt = datetime(start[0], start[1], start[2], tzinfo=timezone.utc)
        end_dt = datetime(end[0], end[1], end[2], tzinfo=timezone.utc)
    except (TypeError, IndexError, ValueError) as e:
        sys.exit(f"error: bad start/end date in bundle: {e}")

    # Questions -> the same flatten + dynamic-column ordering + sort the scraper uses.
    q_rows = [flatten_question(q) for q in questions]
    q_rows.sort(key=lambda r: int(r["id"]))  # CSV sorted by ascending id
    fieldnames = build_fieldnames(q_rows)
    q_out = args.questions_out or default_output_path(
        "questions", product, start_dt, end_dt)
    _write_csv(q_rows, fieldnames, q_out, restval="")
    print(f"Wrote {len(q_rows)} questions to {q_out} ({len(fieldnames)} columns)",
          file=sys.stderr)

    # Answers are optional (only if the extension fetched them). Absent key ->
    # skip entirely; present-but-empty -> header-only CSV (matches an empty day).
    if "answers" in bundle:
        a_rows = [flatten_answer(a) for a in bundle["answers"]]
        a_rows.sort(key=lambda r: int(r["id"]))  # CSV sorted by ascending id
        a_out = args.answers_out or default_output_path(
            "answers", product, start_dt, end_dt)
        _write_csv(a_rows, COLUMNS, a_out)
        print(f"Wrote {len(a_rows)} answers to {a_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
