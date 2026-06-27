#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Refresh driver: rebuild only the day-CSVs that actually changed.

Asks find_updated_days.py which (product, created-day) pairs were modified in a
window (default: yesterday..today UTC), then re-runs the existing, unchanged
scrapers (scrape_questions.py + scrape_answers.py) once per pair. Each rebuild is
deterministic, so unchanged days produce byte-identical CSVs and only real
changes show up in git.

Unlike run_backfill.py (contiguous range, 2-10 MIN between days), this iterates a
sparse, data-driven set with a short pause so it fits an hourly cron.

    uv run python run_refresh.py                      # yesterday .. today UTC
    uv run python run_refresh.py 2026-06-01 2026-06-26
"""

import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import find_updated_days as fud
from sumo import SumoBrowser

PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]
MIN_WAIT, MAX_WAIT = 5, 30  # seconds between (product, day) jobs


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"WARN exit {r.returncode}: {' '.join(cmd)}", flush=True)


def parse(s):
    y, m, d = (int(x) for x in s.split("-"))
    return datetime(y, m, d, tzinfo=timezone.utc)


def main():
    if len(sys.argv) > 1:
        start_dt = parse(sys.argv[1])
        end_dt = parse(sys.argv[2]) if len(sys.argv) > 2 else start_dt
    else:
        start_dt, end_dt = fud.default_window()

    print(f"Refresh window: {start_dt.date()} .. {end_dt.date()} (UTC)", flush=True)

    # One browser for discovery; each scrape subprocess opens its own (as in
    # run_backfill.py). Reusing a single browser across all scrapes is a future
    # optimisation that would require importable scraper functions.
    with SumoBrowser(headless=True) as sumo:
        pairs = fud.find_updated_days(sumo, start_dt, end_dt, PRODUCTS,
                                      min_day=fud.cutoff_day())

    labels = dict(PRODUCTS)
    print(f"Refreshing {len(pairs)} (product, day) pairs", flush=True)

    rebuilt = []
    for i, (slug, day) in enumerate(pairs):
        label = labels.get(slug, slug)
        y, m, dd = (int(x) for x in day.split("-"))
        print(f"\n=== {i+1}/{len(pairs)}: {slug} {day} ===", flush=True)

        run(["uv", "run", "python", "scrape_questions.py",
             str(y), str(m), str(dd), str(y), str(m), str(dd),
             "--product", slug, "--headless", "--random-delay"])

        q = f"{day[:4]}/questions-{label}-{day}.csv"
        if os.path.exists(q):
            run(["uv", "run", "python", "scrape_answers.py",
                 "--questions", q, "--headless", "--random-delay"])
            rebuilt.append(q)
        else:
            print(f"WARN no questions CSV at {q}", flush=True)

        if i < len(pairs) - 1:
            time.sleep(random.uniform(MIN_WAIT, MAX_WAIT))

    print(f"\nREFRESH COMPLETE: {len(pairs)} pairs, "
          f"{len(rebuilt)} question/answer CSV sets rebuilt", flush=True)


if __name__ == "__main__":
    main()
