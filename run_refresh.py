#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Refresh driver: rebuild only the day-CSVs that actually changed.

Asks find_updated_days for the (product, created-day) pairs modified in a
window, then re-runs the existing, unchanged scrapers (scrape_questions.py +
scrape_answers.py) once per pair. Each rebuild is deterministic, so unchanged
days produce byte-identical CSVs and only real changes show up in git.

Two modes:

  * Incremental (default, no date args) -- for the hourly cron. Reads a
    high-water mark (the `updated` time we last queried up to) from a small state
    file and queries only `[hwm - overlap, now]`, so each run re-scrapes just the
    handful of days that changed since the last run. On success it writes the new
    high-water mark. With no state file it falls back to a lookback window.

  * Explicit range (YYYY-MM-DD [YYYY-MM-DD]) -- for manual / one-off refreshes.
    Uses a whole-day window and does NOT touch the state file.

    uv run python run_refresh.py                 # incremental (hourly cron)
    uv run python run_refresh.py 2026-06-01 2026-06-26   # explicit range

Unlike run_backfill.py (contiguous range, 2-10 MIN between days), this iterates a
sparse, data-driven set with a short pause so it fits an hourly cron.
"""

import argparse
import os
import random
import subprocess
import time
from datetime import datetime, timedelta, timezone

import find_updated_days as fud
from sumo import SumoBrowser

PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]
MIN_WAIT, MAX_WAIT = 5, 30  # seconds between (product, day) jobs
DEFAULT_STATE = ".refresh-hwm"


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"WARN exit {r.returncode}: {' '.join(cmd)}", flush=True)


def parse_date(s):
    y, m, d = (int(x) for x in s.split("-"))
    return datetime(y, m, d, tzinfo=timezone.utc)


def read_hwm(path):
    """Return the stored high-water-mark datetime, or None if absent/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return fud.parse_dt(f.read().strip())
    except (OSError, ValueError):
        return None


def write_hwm(path, dt):
    with open(path, "w", encoding="utf-8") as f:
        f.write(dt.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")


def main():
    p = argparse.ArgumentParser(description="Refresh day-CSVs that changed")
    p.add_argument("dates", nargs="*",
                   help="YYYY-MM-DD [YYYY-MM-DD] for an explicit whole-day range; "
                        "omit for incremental (high-water-mark) mode")
    p.add_argument("--state", default=DEFAULT_STATE,
                   help=f"high-water-mark state file (default {DEFAULT_STATE})")
    p.add_argument("--overlap-minutes", type=float, default=15,
                   help="re-query this far before the last high-water mark "
                        "(guards clock skew / run-boundary; default 15)")
    p.add_argument("--lookback-hours", type=float, default=26,
                   help="incremental window when no state file exists yet "
                        "(default 26h, covers a missed day)")
    p.add_argument("--max-age-days", type=int, default=fud.DEFAULT_MAX_AGE_DAYS,
                   help="don't refresh created days older than this (default "
                        f"{fud.DEFAULT_MAX_AGE_DAYS}; 0 disables)")
    args = p.parse_args()

    incremental = not args.dates
    now = datetime.now(timezone.utc)

    if incremental:
        hwm = read_hwm(args.state)
        if hwm is not None:
            greater_than = hwm - timedelta(minutes=args.overlap_minutes)
            print(f"Incremental: high-water mark {hwm.isoformat()} "
                  f"(- {args.overlap_minutes:g} min overlap)", flush=True)
        else:
            greater_than = now - timedelta(hours=args.lookback_hours)
            print(f"Incremental: no state file ({args.state}); "
                  f"falling back to {args.lookback_hours:g}h lookback", flush=True)
        less_than = now
    else:
        if len(args.dates) > 2:
            p.error("pass at most two dates (start [end])")
        start_dt = parse_date(args.dates[0])
        end_dt = parse_date(args.dates[1]) if len(args.dates) > 1 else start_dt
        greater_than, less_than = fud.day_bounds(start_dt, end_dt)

    print(f"Refresh window: {greater_than.isoformat()} .. {less_than.isoformat()}",
          flush=True)

    # One browser for discovery; each scrape subprocess opens its own (as in
    # run_backfill.py). Reusing a single browser across all scrapes is a future
    # optimisation that would require importable scraper functions.
    with SumoBrowser(headless=True) as sumo:
        pairs = fud.find_updated_days(sumo, greater_than, less_than, PRODUCTS,
                                      min_day=fud.cutoff_day(args.max_age_days))

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

    # Advance the high-water mark only after a clean incremental run, so a crash
    # mid-run re-queries the same window next time (idempotent).
    if incremental:
        write_hwm(args.state, less_than)
        print(f"High-water mark -> {less_than.isoformat()} ({args.state})",
              flush=True)

    print(f"\nREFRESH COMPLETE: {len(pairs)} pairs, "
          f"{len(rebuilt)} question/answer CSV sets rebuilt", flush=True)


if __name__ == "__main__":
    main()
