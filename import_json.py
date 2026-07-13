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

**Per-day output.** The bundle may span multiple days (extension Start != End).
Questions are bucketed by their `created` UTC day and written to one
`<year>/questions-<label>-<day>.csv` per day; answers are bucketed by their
PARENT question's created day (matching scrape_answers, whose per-day answers
file pairs with that day's questions file). Each per-day file is byte-identical
to a single-day scrape/fetch of that day, so extension output drops straight into
the tracked per-day layout and the refresh/backfill.

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
from collections import defaultdict
from datetime import datetime, timezone

from scrape_answers import COLUMNS, flatten_answer
from scrape_questions import (
    build_fieldnames,
    default_output_path,
    flatten_question,
    parse_dt,
)

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


def _created_day(obj):
    """UTC date of an object's `created`, or None if missing/unparsable."""
    dt = parse_dt(obj.get("created"))
    return dt.astimezone(timezone.utc).date() if dt is not None else None


def _day_dt(day):
    """datetime.date -> aware UTC datetime at midnight (for default_output_path)."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def main():
    p = argparse.ArgumentParser(
        description="Import an extension JSON bundle into per-day questions/answers "
                    "CSVs (bucketed by created day; multi-day bundles are split).")
    p.add_argument("bundle", help="path to the JSON bundle from the extension")
    p.add_argument("--questions-out", default=None,
                   help="override the questions CSV path (single-day bundle only)")
    p.add_argument("--answers-out", default=None,
                   help="override the answers CSV path (single-day bundle only)")
    args = p.parse_args()

    try:
        with open(args.bundle, encoding="utf-8") as f:
            bundle = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"error: could not read JSON bundle {args.bundle!r}: {e}")
    if not isinstance(bundle, dict):
        sys.exit("error: bundle must be a JSON object")

    product = _require(bundle, "product")
    _require(bundle, "start")            # window bounds are informational; output
    _require(bundle, "end")              # paths come from each row's created day
    questions = _require(bundle, "questions")

    # Bucket questions by their created UTC day.
    q_by_day = defaultdict(list)
    for q in questions:
        day = _created_day(q)
        if day is None:
            sys.exit(f"error: question {q.get('id')!r} has no parsable 'created'")
        q_by_day[day].append(q)

    # Map question id -> created day, so answers land in their parent's day file.
    qid_day = {}
    for day, qs in q_by_day.items():
        for q in qs:
            try:
                qid_day[int(q["id"])] = day
            except (KeyError, TypeError, ValueError):
                pass

    override = args.questions_out or args.answers_out
    if override and len(q_by_day) > 1:
        sys.exit("error: --questions-out/--answers-out only apply to a single-day "
                 f"bundle; this one spans {len(q_by_day)} days — omit them to write "
                 "per-day files automatically")

    # Answers (optional): bucket by parent question's created day.
    answers = bundle.get("answers")
    a_by_day = defaultdict(list)
    orphans = 0
    if answers is not None:
        for a in answers:
            try:
                qid = int(a.get("question"))
            except (TypeError, ValueError):
                qid = None
            day = qid_day.get(qid)
            if day is None:
                orphans += 1
                continue
            a_by_day[day].append(a)

    # Write one questions CSV per day (byte-identical to a single-day scrape:
    # flatten + id-sort + build_fieldnames over just that day's rows).
    for day in sorted(q_by_day):
        rows = [flatten_question(q) for q in q_by_day[day]]
        rows.sort(key=lambda r: int(r["id"]))
        fieldnames = build_fieldnames(rows)
        out = args.questions_out or default_output_path(
            "questions", product, _day_dt(day), _day_dt(day))
        _write_csv(rows, fieldnames, out, restval="")
        print(f"Wrote {len(rows)} questions to {out} ({len(fieldnames)} columns)",
              file=sys.stderr)

    # Answers are optional (only if the extension fetched them). Write one file per
    # QUESTIONS day (header-only when that day has no answers), pairing with the
    # questions file exactly like scrape_answers.
    if answers is not None:
        for day in sorted(q_by_day):
            rows = [flatten_answer(a) for a in a_by_day.get(day, [])]
            rows.sort(key=lambda r: int(r["id"]))
            out = args.answers_out or default_output_path(
                "answers", product, _day_dt(day), _day_dt(day))
            _write_csv(rows, COLUMNS, out)
            print(f"Wrote {len(rows)} answers to {out}", file=sys.stderr)
        if orphans:
            print(f"note: skipped {orphans} answers whose parent question was not "
                  "in the bundle", file=sys.stderr)


if __name__ == "__main__":
    main()
