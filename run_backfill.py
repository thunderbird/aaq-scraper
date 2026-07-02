#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
One-off backfill driver: run the scrapers once per day over a date range, with a
random 2-10 MINUTE pause between days (polite spacing). Within each run the
scrapers use their default fixed 2s delay between API calls.

    uv run python run_backfill.py            # 2026-06-01 .. 2026-06-24
    uv run python run_backfill.py 2026-06-01 2026-06-24
"""

import os
import random
import subprocess
import sys
import time
from datetime import date, timedelta

PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]
MIN_WAIT, MAX_WAIT = 120, 600  # seconds (2-10 minutes)


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"WARN exit {r.returncode}: {' '.join(cmd)}", flush=True)


def parse(s):
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def main():
    start = parse(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 6, 1)
    end = parse(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 6, 24)

    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    print(f"Backfill {start} .. {end} ({len(days)} days), "
          f"random {MIN_WAIT//60}-{MAX_WAIT//60} min between days", flush=True)

    for i, day in enumerate(days):
        y, m, dd = day.year, day.month, day.day
        iso = day.strftime("%Y-%m-%d")
        print(f"\n=== DAY {i+1}/{len(days)}: {iso} ===", flush=True)

        for product, _ in PRODUCTS:
            run(["uv", "run", "python", "scrape_questions.py",
                 str(y), str(m), str(dd), str(y), str(m), str(dd),
                 "--product", product, "--headless"])

        for _, label in PRODUCTS:
            q = f"{y}/questions-{label}-{iso}.csv"
            if os.path.exists(q):
                run(["uv", "run", "python", "scrape_answers.py",
                     "--questions", q, "--headless"])

        if i < len(days) - 1:
            wait = random.uniform(MIN_WAIT, MAX_WAIT)
            print(f"sleeping {wait/60:.1f} min before next day...", flush=True)
            time.sleep(wait)

    print("\nBACKFILL COMPLETE", flush=True)


if __name__ == "__main__":
    main()
