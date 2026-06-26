# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Scrape the Mozilla SUMO (support.mozilla.org) "Ask a Question" API for Thunderbird
Desktop and Thunderbird Android, producing CSVs compatible with the legacy Ruby
reports in `thunderbird/github-action-thunderbird-aaq`. Since ~June 2026 the API
sits behind a JavaScript challenge that blocks headless HTTP (issue
thunderbird/github-action-thunderbird-aaq#34), so we drive a **real browser** to
pass the challenge and call the JSON API from inside the browser's authenticated
context.

## Core architecture & crucial decisions

- **Browser-passes-challenge approach** (`sumo.py`): `SumoBrowser` launches
  Chromium (Playwright), loads the site once to acquire challenge cookies, then
  `fetch_json()` does an **in-page `fetch()`** (`page.evaluate`) so the request
  reuses the page's cookies/origin. Headless works (locally **and in GitHub
  Actions** — this is what resolves #34).
- **Stack: Python + Playwright, managed with `uv`** — use `uv sync` / `uv run`,
  never pip or raw venv. Deps in `pyproject.toml`.
- **`fetch_json` retries** transient failures with exponential backoff: HTTP 429
  (honours `Retry-After`), 5xx, and a 200-but-non-JSON challenge hiccup.
  Non-retryable 4xx fail fast.
- **Output convention:** `<year>/<questions|answers>-<product-label>-<dates>.csv`,
  e.g. `2026/questions-thunderbird-desktop-2026-06-10.csv`. The API product slug
  `thunderbird` maps to the filename label `thunderbird-desktop`;
  `thunderbird-android` is unchanged (`PRODUCT_LABELS` in `scrape_questions.py`).
  The `<year>/` data dir is **tracked in git**. All CSVs are **sorted ascending
  by `id`**.
- **Date window (matches the Ruby original):** `created__gt` = start day 00:00:00
  UTC **minus 1s**; `created__lt` = end day 00:00:00 UTC **plus 1 day** (so both
  days are inclusive). `ordering=created` (ascending) with early-stop. Pass the
  same date twice for a single day.
- **The API filters by `product`, NOT locale** — it returns all locales. The
  public en-US website list shows only en-US, so API counts legitimately exceed
  the en-US page (the rest are other locales). Cross-check against
  `/<locale>/questions/<product>` when reconciling.
- **Answers** are fetched per question via `/api/2/answer/?question=<id>`.

## CSV columns

- **Questions** (`scrape_questions.py`): the original Ruby leading columns +
  remaining API keys, with the original flattening (`tags`→`;`-joined slugs;
  `metadata`→`;name:value`; `creator`/`updated_by`/`solved_by`→username; newlines
  stripped from `content`), PLUS three clean columns: `operating_system` (from
  `metadata` entry `os`), `thunderbird_version` (from `metadata` entry
  `tb_version`), and `taken_by` (top-level, username; usually empty). Note
  `metadata` is a **list of `{name, value}`**.
- **Answers** (`scrape_answers.py`): `id, question_id (<-question), created,
  updated, content, creator (username), is_spam, num_helpful
  (<-num_helpful_votes), num_unhelpful (<-num_unhelpful_votes)`.

## Commands

```sh
uv sync
uv run playwright install chromium                 # one-time

# Questions (single day = same date twice). Add --product thunderbird-android for Android.
uv run python scrape_questions.py 2026 6 10 2026 6 10 --headless
# Answers (defaults output to the matching answers-... path)
uv run python scrape_answers.py --questions 2026/questions-thunderbird-desktop-2026-06-10.csv --headless
# Backfill a range, one day at a time, random 2-10 min between days
uv run python run_backfill.py 2026-06-01 2026-06-24
# Schema drift check (manual-bump baseline)
uv run python check_schema.py --headless                 # exit 1 on drift
uv run python check_schema.py --headless --update-baseline
```

Delay between API calls: fixed `--sleep 2` by default, or `--random-delay` to
vary 2–10s (`--min-delay`/`--max-delay`). Use `--headless` for CI parity.

## Automation (GitHub Actions)

- `.github/workflows/scrape.yml` — daily 06:00 UTC + manual; scrapes desktop +
  Android questions/answers headless for a window (default: yesterday UTC) and
  commits new CSVs under `<year>/`.
- `.github/workflows/schema-check.yml` — daily 06:30 UTC; runs `check_schema.py`
  and opens/comments a labelled `schema-change` issue on drift (de-duped by
  label). Baseline `schema/expected-fields.json` is **only** updated manually via
  `--update-baseline`; if a field is *removed*, update the scrapers too.
- Actions are pinned to Node-24 versions: `actions/checkout@v5`,
  `astral-sh/setup-uv@v8.2.0`.

## Notes

- Output is **deterministic**: re-running a day yields byte-identical CSVs, which
  the scrape workflow relies on for its "no changes to commit" path.
- The legacy Ruby data had a **7-hour timezone bug** (UTC times mislabelled
  `-0700`, days shifted) and occasionally dropped questions; this scraper is
  UTC-correct. Verified against the live website for 2026-06-10 (see the
  `website-verification-*.json` fixtures under `2026/`).

## License & contribution

MPL-2.0 (new source files should carry the header). Participants must follow the
[Mozilla Community Participation Guidelines](https://www.mozilla.org/about/governance/policies/participation/).
