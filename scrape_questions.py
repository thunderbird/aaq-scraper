#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Bucket 1 — scrape SUMO questions for a date window to CSV.

Reproduces the original Ruby script
`get-tb-creator-answers-questions-for-arbitrary-time-period.rb`, but drives a
real browser (see sumo.py) to pass the JS challenge. Output keeps the original
columns/format and adds three clean columns: operating_system,
thunderbird_version, taken_by.

The window is start-day..end-day inclusive (UTC). For a single calendar day,
pass the same date twice:

    uv run python scrape_questions.py 2026 6 25 2026 6 25 --headless
"""

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from csv_safety import escape_formula, redact_credentials
from sumo import API_BASE, SumoBrowser

# API product slug -> human label used in output filenames.
PRODUCT_LABELS = {"thunderbird": "thunderbird-desktop"}

# Original leading column order (parity with the Ruby script).
LEADING_KEYS = [
    "id", "created", "updated", "locale", "product", "title", "is_solved",
    "solution", "solved_by", "is_spam", "last_answer", "answers", "topic",
    "tags", "creator", "content",
]
# New clean columns we add (derived; not raw API keys).
DERIVED_KEYS = ["operating_system", "thunderbird_version"]


def metadata_value(metadata, name):
    """Return the value of the `name` entry in a SUMO metadata list, or ''."""
    if isinstance(metadata, list):
        for m in metadata:
            if isinstance(m, dict) and m.get("name") == name:
                return m.get("value", "")
    return ""


def username_of(obj):
    """username from a {username: ...} object, '' if None/absent."""
    if isinstance(obj, dict):
        return obj.get("username", "")
    return ""


def flatten_question(q):
    """Flatten one question into a CSV-ready dict (original rules + new cols)."""
    row = dict(q)

    # Derived clean columns (read from the raw metadata list BEFORE flattening).
    metadata = q.get("metadata")
    row["operating_system"] = metadata_value(metadata, "os")
    row["thunderbird_version"] = metadata_value(metadata, "tb_version")

    # tags -> ";"-joined slugs (trailing ;), matching the original.
    tags = q.get("tags") or []
    row["tags"] = "".join(f"{t.get('slug', '')};" for t in tags if isinstance(t, dict))

    # answers -> ";"-joined stringified entries.
    answers = q.get("answers") or []
    row["answers"] = "".join(f"{a};" for a in answers)

    # involved -> ";"-joined usernames.
    involved = q.get("involved") or []
    row["involved"] = "".join(
        f"{i.get('username', '')};" for i in involved if isinstance(i, dict)
    )

    # metadata -> ";name:value" pairs (leading semicolon), matching the original.
    if isinstance(metadata, list):
        row["metadata"] = "".join(
            f";{m.get('name', '')}:{m.get('value', '')}"
            for m in metadata if isinstance(m, dict)
        )

    # Username-only flattening for people objects.
    row["creator"] = username_of(q.get("creator"))
    row["taken_by"] = username_of(q.get("taken_by"))
    if q.get("updated_by") is not None:
        row["updated_by"] = username_of(q.get("updated_by"))
    if q.get("solved_by") is not None:
        row["solved_by"] = username_of(q.get("solved_by"))

    # Strip newlines from content.
    if isinstance(row.get("content"), str):
        row["content"] = row["content"].replace("\n", " ").replace("\r", " ")

    # Any remaining non-scalar value -> str, to keep the CSV clean; then redact
    # leaked credentials and guard against spreadsheet formula injection on
    # every string cell.
    for k, v in list(row.items()):
        if isinstance(v, (dict, list)):
            v = str(v)
        elif v is None:
            v = ""
        row[k] = escape_formula(redact_credentials(v))
    return row


def build_fieldnames(rows):
    """Leading columns, then remaining API keys (first-seen), then derived."""
    fieldnames = list(LEADING_KEYS)
    seen = set(LEADING_KEYS) | set(DERIVED_KEYS)
    for row in rows:
        for k in row:
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)
    fieldnames.extend(DERIVED_KEYS)
    return fieldnames


def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def default_output_path(kind, product, start_dt, end_dt):
    """<start-year>/<kind>-<product-label>-<dates>.csv"""
    label = PRODUCT_LABELS.get(product, product)
    s = start_dt.strftime("%Y-%m-%d")
    e = end_dt.strftime("%Y-%m-%d")
    dates = s if s == e else f"{s}_{e}"
    return os.path.join(start_dt.strftime("%Y"), f"{kind}-{label}-{dates}.csv")


def main():
    p = argparse.ArgumentParser(description="Scrape SUMO questions to CSV")
    p.add_argument("sy", type=int); p.add_argument("sm", type=int)
    p.add_argument("sd", type=int); p.add_argument("ey", type=int)
    p.add_argument("em", type=int); p.add_argument("ed", type=int)
    p.add_argument("--product", default="thunderbird")
    p.add_argument("--ordering", default="created",
                   help="API ordering; 'created' is ascending (default)")
    p.add_argument("--out", default=None,
                   help="output CSV path (default: <year>/questions-<product>-<dates>.csv)")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="fixed delay (s) between API calls (default 2)")
    p.add_argument("--random-delay", action="store_true",
                   help="randomly vary each delay between --min-delay and --max-delay")
    p.add_argument("--min-delay", type=float, default=2.0)
    p.add_argument("--max-delay", type=float, default=10.0)
    args = p.parse_args()

    start_dt = datetime(args.sy, args.sm, args.sd, tzinfo=timezone.utc)
    end_dt = datetime(args.ey, args.em, args.ed, tzinfo=timezone.utc)
    greater_than = start_dt - timedelta(seconds=1)      # start day inclusive
    less_than = end_dt + timedelta(days=1)              # end day inclusive (23:59:59)
    ascending = not args.ordering.startswith("-")

    out = args.out or default_output_path("questions", args.product, start_dt, end_dt)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    def delay():
        return random.uniform(args.min_delay, args.max_delay) if args.random_delay \
            else args.sleep

    params = {
        "format": "json",
        "product": args.product,
        "created__gt": greater_than.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created__lt": less_than.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if args.ordering != "none":
        params["ordering"] = args.ordering
    first_url = f"{API_BASE}question/?{urlencode(params)}"

    print(f"Window: {greater_than.isoformat()} < created < {less_than.isoformat()}",
          file=sys.stderr)
    print(f"Product: {args.product} | ordering: {args.ordering}", file=sys.stderr)
    print(f"First URL: {first_url}", file=sys.stderr)

    collected = []
    with SumoBrowser(headless=args.headless) as sumo:
        url = first_url
        page_num = 0
        stop = False
        while url and not stop:
            data = sumo.fetch_json(url)
            page_num += 1
            results = data.get("results", [])
            for q in results:
                created = parse_dt(q.get("created"))
                if ascending and created is not None and created >= less_than:
                    stop = True
                    break
                collected.append(q)
            print(f"page {page_num}: +{len(results)} (total {len(collected)})",
                  file=sys.stderr)
            if stop:
                break
            url = data.get("next")
            if url:
                time.sleep(delay())

    rows = [flatten_question(q) for q in collected]
    rows.sort(key=lambda r: int(r["id"]))  # CSV sorted by ascending id
    fieldnames = build_fieldnames(rows)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} questions to {out} ({len(fieldnames)} columns)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
