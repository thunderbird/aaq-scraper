#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Find which (product, created-day) CSVs need refreshing.

The daily `created`-based scrape (scrape_questions.py) never re-fetches past
days, so edits / new answers / "solved" flips on older questions are lost. This
script queries the SUMO API's `updated` filter to find questions MODIFIED in a
window (across all locales), then maps each back to its `created` day -- the day
whose CSV (`<year>/questions-<label>-<day>.csv`) needs rebuilding.

Output is the unique, sorted set of (product-slug, created-day) pairs. Diagnostics
go to stderr; the pairs go to stdout (line-oriented "<slug> <day>", or JSON with
--json) so it pipes cleanly into run_refresh.py.

    uv run python find_updated_days.py --headless                 # today + yesterday
    uv run python find_updated_days.py 2026 6 1 2026 6 26 --headless
"""

import argparse
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sumo import API_BASE, SumoBrowser

# API product slug -> human label used in output filenames (matches
# scrape_questions.PRODUCT_LABELS / run_backfill.PRODUCTS).
PRODUCTS = [("thunderbird", "thunderbird-desktop"),
            ("thunderbird-android", "thunderbird-android")]


def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _window_strs(start_dt, end_dt):
    """(greater_than, less_than) datetimes + their API strings, matching
    scrape_questions.py: start day minus 1s .. end day plus 1 day (inclusive)."""
    greater_than = start_dt - timedelta(seconds=1)
    less_than = end_dt + timedelta(days=1)
    return (greater_than, less_than,
            greater_than.strftime("%Y-%m-%dT%H:%M:%SZ"),
            less_than.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _questions_updated(sumo, slug, gt_str, lt_str, less_than, delay, seen,
                       min_day=None):
    """Yield (slug, created_day) for questions of `slug` updated in the window.

    Records each question id -> (slug, created_day) in `seen` (regardless of age)
    so the answer pass can skip questions already resolved here. Questions whose
    created day is older than `min_day` (a 'YYYY-MM-DD' string) are recorded but
    NOT yielded. ordering=updated (ascending) lets us early-stop once
    updated >= less_than.
    """
    params = {
        "format": "json",
        "product": slug,
        "updated__gt": gt_str,
        "updated__lt": lt_str,
        "ordering": "updated",
    }
    url = f"{API_BASE}question/?{urlencode(params)}"
    print(f"[q:{slug}] First URL: {url}", file=sys.stderr)

    page_num = found = 0
    stop = False
    while url and not stop:
        data = sumo.fetch_json(url)
        page_num += 1
        results = data.get("results", [])
        for q in results:
            updated = parse_dt(q.get("updated"))
            if updated is not None and updated >= less_than:
                stop = True
                break
            created = parse_dt(q.get("created"))
            if created is None:
                print(f"[q:{slug}] skip q{q.get('id')}: no created date",
                      file=sys.stderr)
                continue
            day = created.strftime("%Y-%m-%d")
            seen[q.get("id")] = (slug, day)
            if min_day is not None and day < min_day:
                continue  # too old to refresh
            found += 1
            yield (slug, day)
        print(f"[q:{slug}] page {page_num}: +{len(results)} (kept {found})",
              file=sys.stderr)
        if stop:
            break
        url = data.get("next")
        if url:
            time.sleep(delay())


def _answer_question_ids(sumo, gt_str, lt_str, less_than, delay):
    """Return the set of distinct question ids whose ANSWERS changed in the
    window. The answer endpoint has no product filter, so this spans all
    products; the caller resolves product per question. ordering=updated
    (ascending) lets us early-stop once updated >= less_than."""
    params = {
        "format": "json",
        "updated__gt": gt_str,
        "updated__lt": lt_str,
        "ordering": "updated",
    }
    url = f"{API_BASE}answer/?{urlencode(params)}"
    print(f"[a] First URL: {url}", file=sys.stderr)

    qids = set()
    page_num = 0
    stop = False
    while url and not stop:
        data = sumo.fetch_json(url)
        page_num += 1
        results = data.get("results", [])
        for a in results:
            updated = parse_dt(a.get("updated"))
            if updated is not None and updated >= less_than:
                stop = True
                break
            qid = a.get("question")
            if qid is not None:
                qids.add(qid)
        print(f"[a] page {page_num}: +{len(results)} (distinct questions {len(qids)})",
              file=sys.stderr)
        if stop:
            break
        url = data.get("next")
        if url:
            time.sleep(delay())
    return qids


def _resolve_question(sumo, qid, slugs):
    """Fetch a question's detail; return (slug, created_day) if its product is in
    `slugs`, else None (other products / errors are skipped)."""
    url = f"{API_BASE}question/{qid}/?format=json"
    try:
        q = sumo.fetch_json(url)
    except Exception as e:  # noqa: BLE001 - a single bad id shouldn't kill the run
        print(f"[a] skip q{qid}: fetch failed ({e})", file=sys.stderr)
        return None
    slug = q.get("product")
    if slug not in slugs:
        return None
    created = parse_dt(q.get("created"))
    if created is None:
        print(f"[a] skip q{qid}: no created date", file=sys.stderr)
        return None
    return (slug, created.strftime("%Y-%m-%d"))


def find_updated_days(sumo, start_dt, end_dt, products=PRODUCTS, delay=lambda: 2.0,
                      include_answers=True, min_day=None):
    """Query the `updated` window on an already-open SumoBrowser (caller owns it).

    Returns a sorted list of (slug, 'YYYY-MM-DD') pairs, where the day is each
    modified question's `created` date (UTC) -- i.e. which day-CSV to rebuild.

    Two passes (their union):
      1. Questions of each product updated in the window (catches question edits,
         solved flips, etc.).
      2. ANSWERS updated in the window, mapped back to their parent question's
         product + created day. This pass is REQUIRED because a question's
         `updated` does NOT bump when a new answer is posted (verified against the
         live API), so answer-only changes are invisible to pass 1.

    `min_day` ('YYYY-MM-DD' or None) drops pairs whose created day is older --
    we don't refresh questions/answers created before it (default cutoff: 1 year,
    set by the callers). start_dt/end_dt are tz-aware UTC datetimes; only their
    date matters. The window matches scrape_questions.py: start day 00:00:00 minus
    1s .. end day plus 1 day (both days inclusive). include_answers=False -> pass 1
    only.
    """
    greater_than, less_than, gt_str, lt_str = _window_strs(start_dt, end_dt)
    slugs = {slug for slug, _ in products}

    print(f"Window: {greater_than.isoformat()} < updated < {less_than.isoformat()}",
          file=sys.stderr)
    if min_day:
        print(f"Age cutoff: skip created day < {min_day}", file=sys.stderr)

    pairs = set()
    seen_q = {}  # question id -> (slug, day) resolved in the question pass
    for slug, _label in products:
        pairs.update(_questions_updated(sumo, slug, gt_str, lt_str, less_than,
                                        delay, seen_q, min_day))

    if include_answers:
        qids = _answer_question_ids(sumo, gt_str, lt_str, less_than, delay)
        todo = [qid for qid in qids if qid not in seen_q]
        print(f"[a] {len(qids)} questions had answer changes; "
              f"{len(seen_q)} already known, resolving {len(todo)} by detail fetch",
              file=sys.stderr)
        too_old = 0
        for n, qid in enumerate(todo, 1):
            resolved = _resolve_question(sumo, qid, slugs)
            if resolved:
                if min_day is not None and resolved[1] < min_day:
                    too_old += 1
                else:
                    pairs.add(resolved)
            if n % 25 == 0 or n == len(todo):
                print(f"[a] resolved {n}/{len(todo)} "
                      f"(pairs {len(pairs)}, too old {too_old})", file=sys.stderr)
            if n < len(todo):
                time.sleep(delay())

    return sorted(pairs)


def find_updated_days_standalone(start_dt, end_dt, products=PRODUCTS,
                                 headless=True, delay=lambda: 2.0,
                                 include_answers=True, min_day=None):
    with SumoBrowser(headless=headless) as sumo:
        return find_updated_days(sumo, start_dt, end_dt, products, delay,
                                 include_answers, min_day)


# We never refresh questions/answers created more than this many days ago.
DEFAULT_MAX_AGE_DAYS = 365


def cutoff_day(max_age_days=DEFAULT_MAX_AGE_DAYS):
    """'YYYY-MM-DD' for the oldest created day we still refresh (today minus
    max_age_days, UTC), or None to disable the age cutoff."""
    if not max_age_days:
        return None
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_age_days)
    return cutoff.strftime("%Y-%m-%d")


def default_window():
    """Default window = yesterday .. today (UTC), a 2-day overlap that avoids
    missing late edits near the UTC-midnight boundary between hourly runs."""
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    start = datetime(yesterday.year, yesterday.month, yesterday.day,
                     tzinfo=timezone.utc)
    end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return start, end


def main():
    p = argparse.ArgumentParser(
        description="Find (product, created-day) pairs whose CSVs need refreshing")
    p.add_argument("dates", nargs="*", type=int,
                   help="optional SY SM SD EY EM ED; default: yesterday..today UTC")
    p.add_argument("--product", action="append", dest="products",
                   help="API product slug (repeatable; default: both)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON [[slug, day], ...] instead of lines")
    p.add_argument("--no-answers", action="store_true",
                   help="skip the answer-updated pass (questions only; faster but "
                        "misses days whose only change is a new/edited answer)")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                   help="don't refresh created days older than this many days "
                        f"(default {DEFAULT_MAX_AGE_DAYS}; 0 disables the cutoff)")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="fixed delay (s) between API calls (default 2)")
    p.add_argument("--random-delay", action="store_true",
                   help="randomly vary each delay between --min-delay and --max-delay")
    p.add_argument("--min-delay", type=float, default=2.0)
    p.add_argument("--max-delay", type=float, default=10.0)
    args = p.parse_args()

    if not args.dates:
        start_dt, end_dt = default_window()
    elif len(args.dates) == 6:
        sy, sm, sd, ey, em, ed = args.dates
        start_dt = datetime(sy, sm, sd, tzinfo=timezone.utc)
        end_dt = datetime(ey, em, ed, tzinfo=timezone.utc)
    else:
        p.error("dates must be exactly 6 ints (SY SM SD EY EM ED) or omitted")

    if args.products:
        labels = dict(PRODUCTS)
        products = [(s, labels.get(s, s)) for s in args.products]
    else:
        products = PRODUCTS

    def delay():
        return random.uniform(args.min_delay, args.max_delay) if args.random_delay \
            else args.sleep

    pairs = find_updated_days_standalone(start_dt, end_dt, products,
                                         headless=args.headless, delay=delay,
                                         include_answers=not args.no_answers,
                                         min_day=cutoff_day(args.max_age_days))

    print(f"Found {len(pairs)} (product, day) pairs to refresh", file=sys.stderr)
    if args.json:
        import json
        print(json.dumps([[s, d] for s, d in pairs]))
    else:
        for slug, day in pairs:
            print(f"{slug} {day}")


if __name__ == "__main__":
    main()
