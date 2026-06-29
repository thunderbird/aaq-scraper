#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Month-by-month backfill driver, newest -> oldest.

Scrapes every day of each month (questions + answers, both products) via the
existing per-day scrapers, then commits and pushes that month before moving on,
with a random 2-10 MINUTE pause between months. Unlike run_backfill.py this uses
the correct year directory (not a hardcoded 2026/), so it works for any year.

Days whose scrape exits non-zero are appended to a failures file
(backfill-failures.txt, NOT committed) as `<iso>\t<product>\t<kind>\texit<code>`
so they can be redone afterwards. Re-running a month is safe: deterministic
scrapes produce byte-identical CSVs, so unchanged data yields "nothing to
commit".

    uv run python run_backfill_months.py 2026-04 2023-01   # Apr 2026 down to Jan 2023
"""

import os
import random
import subprocess
import sys
import time
from calendar import monthrange

PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]
MIN_WAIT, MAX_WAIT = 120, 600  # seconds between months (2-10 min)
FAILURES = "backfill-failures.txt"


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def parse_month(s):
    y, m = s.split("-")
    return (int(y), int(m))


def months_desc(start, end):
    """Yield (year, month) from start down to end, inclusive."""
    y, m = start
    while (y, m) >= end:
        yield y, m
        m -= 1
        if m == 0:
            m, y = 12, y - 1


def record_failure(iso, product, kind, code):
    with open(FAILURES, "a", encoding="utf-8") as f:
        f.write(f"{iso}\t{product}\t{kind}\texit{code}\n")
    print(f"FAIL {kind} {product} {iso} (exit {code}) -> {FAILURES}", flush=True)


def commit_push(year_dir, msg):
    """Stage the year dir; commit+push only if something changed. Pulls --rebase
    before pushing because the hourly refresh workflow also pushes to main."""
    subprocess.run(["git", "add", year_dir])
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print("No changes to commit for this month.", flush=True)
        return
    subprocess.run(["git", "commit", "-q", "-m", msg])
    for attempt in range(1, 4):
        if subprocess.run(["git", "pull", "--rebase", "--quiet"]).returncode != 0:
            subprocess.run(["git", "rebase", "--abort"])
            print(f"rebase conflict; retry {attempt}/3", flush=True)
            time.sleep(5)
            continue
        if subprocess.run(["git", "push", "--quiet"]).returncode == 0:
            print(f"pushed: {msg}", flush=True)
            return
        print(f"push failed; retry {attempt}/3", flush=True)
        time.sleep(5)
    print(f"WARN: could not push '{msg}'; commit is local only.", flush=True)


def main():
    start = parse_month(sys.argv[1])
    end = parse_month(sys.argv[2])
    months = list(months_desc(start, end))
    print(f"Month backfill: {sys.argv[1]} down to {sys.argv[2]} "
          f"({len(months)} months), random {MIN_WAIT//60}-{MAX_WAIT//60} min "
          f"between months", flush=True)

    for i, (y, m) in enumerate(months):
        ndays = monthrange(y, m)[1]
        year_dir = f"{y:04d}"
        print(f"\n===== MONTH {i+1}/{len(months)}: {y:04d}-{m:02d} "
              f"({ndays} days) =====", flush=True)

        for day in range(1, ndays + 1):
            iso = f"{y:04d}-{m:02d}-{day:02d}"
            for product, _label in PRODUCTS:
                code = run(["uv", "run", "python", "scrape_questions.py",
                            str(y), str(m), str(day), str(y), str(m), str(day),
                            "--product", product, "--headless"])
                if code != 0:
                    record_failure(iso, product, "questions", code)
            for product, label in PRODUCTS:
                q = f"{year_dir}/questions-{label}-{iso}.csv"
                if os.path.exists(q):
                    code = run(["uv", "run", "python", "scrape_answers.py",
                                "--questions", q, "--headless"])
                    if code != 0:
                        record_failure(iso, product, "answers", code)

        commit_push(year_dir,
                    f"Backfill {y:04d}-{m:02d} questions + answers "
                    f"(desktop + Android)")

        if i < len(months) - 1:
            wait = random.uniform(MIN_WAIT, MAX_WAIT)
            print(f"sleeping {wait/60:.1f} min before next month...", flush=True)
            time.sleep(wait)

    print("\nMONTH BACKFILL COMPLETE", flush=True)


if __name__ == "__main__":
    main()
