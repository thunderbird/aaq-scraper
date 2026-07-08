#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Bucket 2 — scrape SUMO answers for a set of questions to CSV.

Reproduces the original Ruby script
`get-tb-answers-from-questions-file-for-arbitrary-time-period.rb`, but drives a
real browser (see sumo.py) to pass the JS challenge. Reads question IDs from a
Bucket 1 questions CSV and fetches each question's answers from /api/2/answer/.

Run:
    uv run python scrape_answers.py --questions questions-2026-06-10.csv --headless
"""

import argparse
import csv
import os
import random
import sys
import time
from urllib.parse import urlencode

from csv_safety import escape_formula, redact_credentials
from sumo import API_BASE, DEFERRAL_EXIT_CODE, RateLimitDeferral, SumoBrowser

# Importing sumo raises csv.field_size_limit to the platform maximum, so reading
# questions files with very large `content` fields works.

# Output columns, in order, matching the Ruby original.
COLUMNS = [
    "id", "question_id", "created", "updated", "content", "creator",
    "is_spam", "num_helpful", "num_unhelpful",
]


def username_of(obj):
    return obj.get("username", "") if isinstance(obj, dict) else ""


def flatten_answer(a):
    """Map one API answer into the original CSV column set."""
    content = a.get("content") or ""
    if isinstance(content, str):
        content = content.replace("\n", " ").replace("\r", " ")
    return {
        "id": a.get("id"),
        "question_id": a.get("question"),
        "created": a.get("created"),
        "updated": a.get("updated"),
        "content": escape_formula(redact_credentials(content)),
        "creator": escape_formula(username_of(a.get("creator"))),
        "is_spam": a.get("is_spam"),
        # API exposes *_votes; the original CSV columns drop the suffix.
        "num_helpful": a.get("num_helpful_votes"),
        "num_unhelpful": a.get("num_unhelpful_votes"),
    }


def read_question_ids(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [row["id"] for row in csv.DictReader(f) if row.get("id")]


def main():
    p = argparse.ArgumentParser(description="Scrape SUMO answers to CSV")
    p.add_argument("--questions", required=True,
                   help="path to a Bucket 1 questions CSV (uses its 'id' column)")
    p.add_argument("--out", default=None,
                   help="output CSV path (default: mirrors the questions file, "
                        "'questions' -> 'answers')")
    p.add_argument("--ordering", default="created")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="fixed delay (s) between API calls (default 2)")
    p.add_argument("--random-delay", action="store_true",
                   help="randomly vary each delay between --min-delay and --max-delay")
    p.add_argument("--min-delay", type=float, default=2.0)
    p.add_argument("--max-delay", type=float, default=10.0)
    p.add_argument("--max-429-wait", type=float, default=None, dest="max_429_wait",
                   help="defer (exit %d) instead of waiting when a 429 demands "
                        "longer than this many seconds (default: wait in full)"
                        % DEFERRAL_EXIT_CODE)
    args = p.parse_args()

    qids = read_question_ids(args.questions)
    print(f"Read {len(qids)} question ids from {args.questions}", file=sys.stderr)

    if args.out:
        out = args.out
    else:
        d, base = os.path.split(args.questions)
        name = base.replace("questions", "answers", 1) if "questions" in base \
            else f"answers-{base}"
        out = os.path.join(d or ".", name)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    def delay():
        return random.uniform(args.min_delay, args.max_delay) if args.random_delay \
            else args.sleep

    rows = []
    try:
        with SumoBrowser(headless=args.headless,
                         max_429_wait_s=args.max_429_wait) as sumo:
            for n, qid in enumerate(qids, 1):
                params = {"format": "json", "question": qid}
                if args.ordering != "none":
                    params["ordering"] = args.ordering
                url = f"{API_BASE}answer/?{urlencode(params)}"
                got = 0
                while url:
                    data = sumo.fetch_json(url)
                    for a in data.get("results", []):
                        rows.append(flatten_answer(a))
                        got += 1
                    url = data.get("next")
                    if url:
                        time.sleep(delay())
                print(f"[{n}/{len(qids)}] question {qid}: {got} answers "
                      f"(total {len(rows)})", file=sys.stderr)
                if n < len(qids):
                    time.sleep(delay())
    except RateLimitDeferral as e:
        # Leave any existing CSV untouched and signal "deferred" so run_refresh
        # holds the high-water mark and retries this day on a later run.
        print(f"DEFERRED (rate-limited): {e}", file=sys.stderr)
        sys.exit(DEFERRAL_EXIT_CODE)

    rows.sort(key=lambda r: int(r["id"]))  # CSV sorted by ascending id
    # Atomic write (tmp + replace): a hard kill mid-write can't truncate the CSV.
    tmp = out + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, out)

    print(f"Wrote {len(rows)} answers to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
