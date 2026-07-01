# Backfill reports

Per-month status reports for the historical backfill
(`run_backfill_months.py`, newest→oldest, target floor **2023-01**).

One `YYYY-MM.md` file is written, committed, and pushed each time the backfill
finishes a month. Each report records the data commit, per-product day/row
counts, and any per-day scrape failures for that month.

`watch.sh` is the watcher that generates these (polls the backfill log; reports
at each month boundary; exits on stall/error). It is tooling, not part of the
scraper — safe to delete once the backfill completes.
