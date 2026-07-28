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
import json
import os
import random
import subprocess
import time
from datetime import datetime, timedelta, timezone

import find_updated_days as fud
import scrape_questions as sq
from sumo import DEFERRAL_EXIT_CODE, RateLimitDeferral, SumoBrowser

PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]
MIN_WAIT, MAX_WAIT = 5, 30  # seconds between (product, day) jobs
DEFAULT_STATE = ".refresh-hwm"


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"WARN exit {r.returncode}: {' '.join(cmd)}", flush=True)
    return r.returncode


def compute_new_hwm(deferred_floors, less_than):
    """High-water mark to persist after a run.

    `deferred_floors` are the earliest-`updated` times of the days that did NOT
    complete this run (rate-limited, failed, or skipped past the soft deadline).
    `less_than` is the window end (the run's start `now`).

    With nothing deferred, advance fully to `less_than`. Otherwise hold the mark
    just before the earliest unapplied change (min floor − 1s) so the next run
    re-queries and retries exactly those days, while every completed day stays
    applied. Never advances past `less_than`.
    """
    if not deferred_floors:
        return less_than
    return min(min(deferred_floors) - timedelta(seconds=1), less_than)


def deferred_path(state_path):
    """Companion file holding the carry-forward set, beside the mark.

    Deliberately a SEPARATE file: `.refresh-hwm` is committed, human-readable,
    and seeded by hand at cutover, so its one-timestamp format stays untouched.
    """
    return state_path + ".deferred"


def read_deferred(path):
    """Return (deferred_pairs, consecutive_stalls). Missing/corrupt -> ([], 0).

    Never fatal: this is an optimisation hint, and a run that cannot read it
    must still do useful work rather than refuse to start.
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return ([tuple(p) for p in d.get("pairs", [])], int(d.get("stalls", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return ([], 0)


def write_deferred(path, pairs, stalls):
    """Persist the carry-forward set, or remove the file when there is none.

    Removing (rather than writing an empty list) keeps a clean run from leaving
    stale state that would reorder the next run for no reason.
    """
    if not pairs and not stalls:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pairs": [list(p) for p in pairs], "stalls": stalls}, f)
        f.write("\n")


def order_day_items(day_items, deferred_pairs):
    """Put previously-deferred days FIRST, preserving order otherwise.

    This is the fix for issue #58. The soft deadline always truncates the tail
    of this list, so with a stable discovery order the same days were deferred
    every run and never ran at all, while the high-water mark stayed pinned
    below them and the window grew without bound. Rotating the starved days to
    the front guarantees each one is attempted, so the mark can advance.

    Entries no longer present in this run's discovery are ignored -- a stale
    carry-forward must not resurrect a day outside the current window.
    """
    if not deferred_pairs:
        return day_items
    priority = {p: i for i, p in enumerate(deferred_pairs)}
    return sorted(day_items, key=lambda kv: (priority.get(kv[0], len(priority)),))


def next_stall_count(old_hwm, new_hwm, stalls):
    """Consecutive runs where the mark failed to move. 0 once it advances.

    A cold start (no previous mark) is not a stall.
    """
    if old_hwm is None or new_hwm > old_hwm:
        return 0
    return stalls + 1


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
    # Inter-call delay forwarded to the per-day scrapers. The default is now the
    # scrapers' own fixed --sleep (2s, i.e. 0.5 req/s). This used to hardcode
    # --random-delay (2-10s, ~6s mean): that jitter was anti-fingerprinting cover
    # from when we called the API as an unallowlisted client, NOT a rate-limit
    # requirement. Our egress IP is allowlisted now, and the sibling GrimoireLab
    # collector sustains ~1 req/s against the same API from the same cluster, so
    # a steady 2s is both well within tolerance and ~3x faster -- which matters,
    # because delay dominates runtime (a 30-question day is ~31 calls).
    # Re-enable the jitter with --random-delay when running from an
    # unallowlisted network.
    p.add_argument("--random-delay", action="store_true",
                   help="vary each scraper delay 2-10s instead of a fixed --sleep; "
                        "only needed from a non-allowlisted IP")
    p.add_argument("--sleep", type=float, default=None,
                   help="fixed delay (s) between API calls, forwarded to the "
                        "scrapers (default: the scrapers' own 2s)")
    p.add_argument("--soft-deadline", type=float, default=None,
                   help="stop starting new days after this many minutes and defer "
                        "the rest, so the run ends before a CI timeout and the "
                        "high-water mark still advances over what finished "
                        "(default: no deadline)")
    p.add_argument("--max-429-wait", type=float, default=None, dest="max_429_wait",
                   help="defer a day (retry it next run) instead of blocking when "
                        "a 429 demands longer than this many seconds; passed to "
                        "the scrapers and the discovery browser")
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

    deadline_s = args.soft_deadline * 60 if args.soft_deadline else None
    start_mono = time.monotonic()

    def maybe_pass(v):  # forward --max-429-wait to a subprocess only when set
        return ["--max-429-wait", str(v)] if v is not None else []

    # Delay flags forwarded to every scraper subprocess (see --random-delay).
    delay_args = ["--random-delay"] if args.random_delay else []
    if args.sleep is not None:
        delay_args += ["--sleep", str(args.sleep)]

    # One browser for discovery; each scrape subprocess opens its own (as in
    # run_backfill.py). Reusing a single browser across all scrapes is a future
    # optimisation that would require importable scraper functions. If discovery
    # itself is rate-limited beyond tolerance we can't learn the full change set,
    # so we defer the WHOLE run and leave the high-water mark untouched.
    try:
        with SumoBrowser(headless=True, max_429_wait_s=args.max_429_wait) as sumo:
            pairs = fud.find_updated_days(sumo, greater_than, less_than, PRODUCTS,
                                          min_day=fud.cutoff_day(args.max_age_days))
    except RateLimitDeferral as e:
        print(f"DISCOVERY DEFERRED (rate-limited): {e}", flush=True)
        print("High-water mark unchanged; next run retries this window.",
              flush=True)
        return

    labels = dict(PRODUCTS)
    day_items = list(pairs.items())  # [((slug, day), earliest_updated_dt), ...]

    # Issue #58: days starved by a previous soft deadline go to the FRONT, so a
    # stable discovery order can't keep truncating the same tail forever.
    # Explicit-range runs don't use the state file, so they don't carry forward.
    dpath = deferred_path(args.state)
    carried, stalls = read_deferred(dpath) if incremental else ([], 0)
    if carried:
        day_items = order_day_items(day_items, carried)
        prioritised = [p for p in carried if p in pairs]
        print(f"Carrying forward {len(prioritised)} previously-deferred day(s) "
              f"to the front of the queue", flush=True)

    print(f"Refreshing {len(day_items)} (product, day) pairs", flush=True)

    rebuilt = []
    deferred_floors = []  # earliest-updated floor of each day NOT completed
    deferred_pairs = []   # the (slug, day) keys, to retry first next run
    for i, ((slug, day), floor) in enumerate(day_items):
        label = labels.get(slug, slug)

        # Soft deadline: stop starting new days, defer the remainder so the run
        # ends cleanly (the mark still advances over whatever finished).
        if deadline_s is not None and (time.monotonic() - start_mono) > deadline_s:
            remaining = day_items[i:]
            print(f"\nSOFT DEADLINE reached ({args.soft_deadline:g} min); "
                  f"deferring {len(remaining)} remaining day(s)", flush=True)
            deferred_floors.extend(f for _, f in remaining)
            deferred_pairs.extend(k for k, _ in remaining)
            break

        y, m, dd = (int(x) for x in day.split("-"))
        print(f"\n=== {i+1}/{len(day_items)}: {slug} {day} ===", flush=True)

        rc = run(["uv", "run", "python", "scrape_questions.py",
                  str(y), str(m), str(dd), str(y), str(m), str(dd),
                  "--product", slug, "--headless"] + delay_args
                 + maybe_pass(args.max_429_wait))
        if rc != 0:
            kind = "DEFERRED (rate-limited)" if rc == DEFERRAL_EXIT_CODE \
                else f"FAILED (exit {rc})"
            print(f"{kind} day {slug} {day} at questions; will retry", flush=True)
            deferred_floors.append(floor); deferred_pairs.append((slug, day))
        else:
            # Same helper the scraper itself used, so the path (including any
            # AAQ_DATA_ROOT prefix) can only be derived one way.
            q = sq.default_output_path("questions", slug,
                                       datetime(y, m, dd), datetime(y, m, dd))
            if os.path.exists(q):
                rc = run(["uv", "run", "python", "scrape_answers.py",
                          "--questions", q, "--headless"] + delay_args
                         + maybe_pass(args.max_429_wait))
                if rc != 0:
                    kind = "DEFERRED (rate-limited)" if rc == DEFERRAL_EXIT_CODE \
                        else f"FAILED (exit {rc})"
                    print(f"{kind} day {slug} {day} at answers; will retry",
                          flush=True)
                    deferred_floors.append(floor); deferred_pairs.append((slug, day))
                else:
                    rebuilt.append(q)
            else:
                # scrape_questions always writes a CSV (header even for 0 rows), so
                # this is only a path mismatch; nothing to retry for the watermark.
                print(f"WARN no questions CSV at {q}", flush=True)

        if i < len(day_items) - 1:
            time.sleep(random.uniform(MIN_WAIT, MAX_WAIT))

    # Advance the high-water mark after an incremental run. Deferred/failed days
    # hold it just below their earliest change so they're retried next run; if
    # nothing was deferred it advances fully. A hard crash writes nothing, so the
    # same window is re-queried next time (idempotent).
    if incremental:
        new_hwm = compute_new_hwm(deferred_floors, less_than)
        note = "advanced" if not deferred_floors \
            else f"held below {len(deferred_floors)} deferred/failed day(s)"
        write_hwm(args.state, new_hwm)
        print(f"High-water mark -> {new_hwm.isoformat()} ({args.state}) [{note}]",
              flush=True)

        # Carry the starved days forward so the next run runs them FIRST (#58),
        # and count consecutive runs where the mark did not move. A pinned mark
        # with a growing window is the signature of the treadmill: every run
        # "succeeds", commits nothing, and makes no progress. Say so loudly --
        # it went unnoticed for 24 hours precisely because nothing complained.
        stalls = next_stall_count(hwm, new_hwm, stalls)
        write_deferred(dpath, deferred_pairs, stalls)
        if stalls >= 3:
            print(f"\n*** WARNING: high-water mark has not advanced in {stalls} "
                  f"consecutive runs. The refresh is succeeding without making "
                  f"progress -- the work no longer fits the budget. Drain the "
                  f"backlog (raise/remove --soft-deadline for one run) or raise "
                  f"throughput. See issue #58.", flush=True)
        elif stalls:
            print(f"NOTE: mark did not advance ({stalls} consecutive)", flush=True)

    print(f"\nREFRESH COMPLETE: {len(day_items)} pairs, {len(rebuilt)} rebuilt, "
          f"{len(deferred_floors)} deferred/failed", flush=True)


if __name__ == "__main__":
    main()
