# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Scrape the Mozilla SUMO (support.mozilla.org) "Ask a Question" API for Thunderbird
Desktop and Thunderbird Android, producing CSVs compatible with the legacy Ruby
reports in `thunderbird/github-action-thunderbird-aaq`. Since ~June 2026 the API
sits behind a JavaScript challenge that blocks headless HTTP (issue
thunderbird/github-action-thunderbird-aaq#34); a real-browser (Playwright/
Chromium) workaround passed the challenge for a while but is now itself
fingerprinted and blocked (issue #28), so the scraper instead calls the API
over a plain **`httpx`** HTTP client, and is moving to a Kubernetes CronJob
deployment with a stable, allowlistable egress IP (issue #27).

## Core architecture & crucial decisions

- **HTTP-client approach** (`sumo.py`): `SumoBrowser` (name kept for
  compatibility) drives a plain `httpx` client; historically it launched
  Chromium via Playwright to pass the JS challenge, but that is now
  fingerprinted/blocked (#28).
- **Stack: Python + `httpx`, managed with `uv`** — use `uv sync` / `uv run`,
  never pip or raw venv. Deps in `pyproject.toml`. (`poc.py` still uses Playwright,
  which is no longer a project dependency — install it separately to run the PoC.)
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
- **`updated`-based refresh** (`find_updated_days.py` + `run_refresh.py`): the
  `created`-based scrape never re-fetches past days, so edits / new answers /
  solved-flips on old questions are lost. The refresh queries the `updated`
  window to find what *changed*, maps each back to its `created` day, and re-runs
  the **existing, unchanged** scrapers for just those `(product, created-day)`
  pairs (deterministic rebuilds → only real changes show in git). **Two passes,
  unioned:** (1) `question/?updated__*` per product; (2) `answer/?updated__*`
  mapped via each answer's parent question (detail-fetched for product+created).
  **Pass 2 is mandatory: a question's `updated` does NOT bump when a new answer
  is posted** (verified live — ~60% of changed answers had a parent question
  absent from the question-`updated` set). **Age cutoff:** never refresh a
  created day older than 1 year (`DEFAULT_MAX_AGE_DAYS=365`, `--max-age-days 0`
  disables). Default window is yesterday..today UTC (2-day overlap for the
  midnight boundary). **API filter gotcha:** the API *silently ignores* unknown
  query params (`id__in`, a `product` filter on answers, etc. are dropped and you
  get the unfiltered list) — only whitelisted fields filter, so verify filters
  empirically.
- **429 deferral + durable partial high-water mark** (guards the refresh against
  the SUMO API's aggressive rate-limiting, which returns HTTP 429 with
  `Retry-After` windows of ~10-15 min). `SumoBrowser(max_429_wait_s=…)` +
  `fetch_json` raise **`sumo.RateLimitDeferral`** instead of sleeping when a 429
  demands longer than the threshold; the scrapers exit **`DEFERRAL_EXIT_CODE`
  (75)** *without touching their CSV* (writes are atomic: tmp file + `os.replace`).
  `run_refresh.py` treats a deferred day as *not completed* and advances the
  high-water mark only to just **below the earliest `updated` change of any
  deferred/failed day** (`compute_new_hwm`; fully to `now` if nothing deferred).
  For that, `find_updated_days` returns **`{(slug, day): earliest_updated_dt}`**
  (not just pairs). A **`--soft-deadline`** (minutes) stops taking new days near a
  CI timeout and defers the rest. Net effect: a slow/rate-limited/cut-off run
  advances the mark over what finished and retries only the rest next run — so a
  single slow run can **never** freeze the mark and trigger the growing-window
  death spiral (incident 2026-07-08). Deferral is **off by default**
  (`max_429_wait_s=None`, no soft deadline) so manual backfills / explicit-range
  refreshes still wait in full; the **hourly workflow opts in** with
  `--soft-deadline 40 --max-429-wait 120`.

## CSV columns

- **Questions** (`scrape_questions.py`): the original Ruby leading columns +
  remaining API keys, with the original flattening (`tags`→`;`-joined slugs;
  `metadata`→`;name:value`; `creator`/`updated_by`/`solved_by`→username; newlines
  stripped from `content`), PLUS clean columns: `operating_system` (from
  `metadata` entry `os`), `thunderbird_version` (from `metadata` entry
  `tb_version`), `firefox_version` (from `metadata` entry `ff_version`), and
  `taken_by` (top-level, username; usually empty). Note `metadata` is a **list
  of `{name, value}`**. **Metadata names are optional user-generated content** —
  any given question may or may not carry `ff_version`, `troubleshooting`, `os`,
  `solver_id`, etc., so the derived columns are frequently blank and their
  absence is normal (not drift). `firefox_version` was added going forward (see
  issue #18); old CSVs were **not** back-filled. The big `troubleshooting`
  metadata blob (the full Firefox about:support JSON, can be ~200KB) is left in
  the flattened `metadata` column only — not promoted to its own column.
  Because these names are sparse, `check_schema.py` treats metadata drift as
  **additive-only** (reports new names, never flags a missing one as "removed").
- **Answers** (`scrape_answers.py`): `id, question_id (<-question), created,
  updated, content, creator (username), is_spam, num_helpful
  (<-num_helpful_votes), num_unhelpful (<-num_unhelpful_votes)`.
- **Formula-injection escaping** (`escape_formula` in both scrapers): SUMO
  content is untrusted user input, so any string cell starting with `= + - @`
  (or tab/CR) gets a leading `'` so spreadsheets treat it as text, not a formula.
  `csv` quoting already prevents field/row breakout; this guards the
  open-in-Excel/Sheets case. Benign values (incl. ISO dates, which start with a
  digit) are untouched, so normal output stays byte-identical. **Note:** the
  escaping only applies when a day is (re)scraped, so pre-existing CSVs aren't
  retroactively normalised until their day next changes (or a full re-backfill).
  `normalize_csv_escaping.py` back-fills the same transform onto committed CSVs
  in place (API-free, idempotent); the one-time normalization is audited in
  `docs/escaped-cells.md` (171 cells, all `@`-prefixed usernames).

## Commands

```sh
uv sync                                            # httpx client; no browser install needed

# Questions (single day = same date twice). Add --product thunderbird-android for Android.
uv run python scrape_questions.py 2026 6 10 2026 6 10 --headless
# Answers (defaults output to the matching answers-... path)
uv run python scrape_answers.py --questions 2026/questions-thunderbird-desktop-2026-06-10.csv --headless
# Backfill a range, one day at a time, random 2-10 min between days
uv run python run_backfill.py 2026-06-01 2026-06-24
# Refresh only the day-CSVs that CHANGED
uv run python find_updated_days.py --headless          # list (product, created-day) pairs (yesterday..today)
uv run python run_refresh.py                           # incremental: changes since last run (high-water mark)
uv run python run_refresh.py 2026-06-01 2026-06-26     # explicit whole-day range (manual; ignores state)
# Schema drift check (manual-bump baseline)
uv run python check_schema.py --headless                 # exit 1 on drift
uv run python check_schema.py --headless --update-baseline
```

Delay between API calls: fixed `--sleep 2` by default, or `--random-delay` to
vary 2–10s (`--min-delay`/`--max-delay`). Use `--headless` for CI parity.

## Automation (GitHub Actions)

- `.github/workflows/scrape.yml` — **hourly** (`0 * * * *`) + manual; runs
  `run_refresh.py` in **incremental** mode and commits changed CSVs under
  `<year>/` (message `Hourly refresh <ts>`). The `updated`-driven refresh also
  covers newly-created questions (their `updated` >= creation), so it **replaces**
  the old daily created-based scrape; `run_backfill.py` + the per-day scrapers
  remain for manual backfills. The high-water-mark state file `.refresh-hwm` is
  **gitignored** and persisted between runs via the **Actions cache** (rolling
  key `refresh-hwm-<run_id>` + `restore-keys: refresh-hwm-`); on a cache miss
  `run_refresh.py` falls back to its `--lookback-hours` window (default 26h).
  `workflow_dispatch` can pass an explicit `start_date`/`end_date` (whole-day
  range, bypasses state).
  - **Where the high-water mark lives:** GitHub-managed **cache storage** (not in
    the repo/git, not on the runner after the job). During a run `.refresh-hwm`
    sits in the workspace; `actions/cache/save` uploads it, `actions/cache/restore`
    fetches it next run. Inspect with `gh cache list --repo thunderbird/aaq-scraper`
    (or repo → Actions → Management → Caches).
  - **Eviction:** 10 GB/repo, LRU, and any entry **untouched for 7 days is
    deleted**. The hourly cron keeps our (few-byte) entry warm; if it's ever
    evicted the 26h-lookback fallback makes the miss self-healing — no data lost.
  - **Branch scoping:** caches are readable by the creating branch, its child
    PRs, and the **default branch is readable by all**. The cron runs on `main`,
    so once merged each hourly run reads the prior run's cache cleanly.
  - The cache is **best-effort, not a durable datastore**; that's acceptable here
    only because of the lookback fallback. To make the mark guaranteed-durable
    instead, commit it to the repo (cost: a tiny state-file change every active
    run).
- `.github/workflows/schema-check.yml` — daily 06:30 UTC; runs `check_schema.py`
  and opens/comments a labelled `schema-change` issue on drift (de-duped by
  label). Baseline `schema/expected-fields.json` is **only** updated manually via
  `--update-baseline`; if a field is *removed*, update the scrapers too.
  `check_schema.py` exits **2 (not 1)** when the API is **blocked by the Fastly
  JS/WAF challenge** (a persistent 200-but-HTML, surfaced as `sumo.ChallengeError`);
  the workflow then opens/comments a separate `api-blocked` issue instead of a
  spurious `schema-change` one. See `docs/js-challenge-edge-waf.md`.
- `.github/workflows/kitsune-api-watch.yml` — **weekly** (Mondays 07:00 UTC) +
  manual; **early-warning for UPSTREAM `mozilla/kitsune` commits** that touch the
  code serving/gating our APIs. Queries the GitHub API for new commits (rolling
  window, default 8 days; de-duped by SHA against the open issue) on **core paths**
  (`questions/api.py`, `wiki/api.py`, `questions/models.py`, `wiki/models.py`,
  `questions/config.py` — all non-merge commits) plus **cross-cutting paths**
  (`settings.py`, `sumo/middleware.py` — only when the message hints at
  throttle/pagination/DRF/CSP/ratelimit, since they churn for unrelated reasons),
  and opens/comments a labelled `kitsune-api-change` issue. This **complements, not
  replaces**, `schema-check.yml`: the schema check is the empirical safety net
  (catches response changes regardless of origin, incl. edge/WAF), while this is
  code-level early warning. **The Fastly challenge is edge infra, not a kitsune
  commit, so it never appears here** — that's caught by the schema check /
  `run_refresh` instead.
- Actions are pinned to Node-24 versions: `actions/checkout@v5`,
  `astral-sh/setup-uv@v8.2.0`.
- **k8s CronJob deployment (manifests merged-pending; job ships suspended):**
  the scraper is moving off GitHub Actions onto an ArgoCD-managed **Kubernetes
  CronJob** on the workloads EKS cluster, because that cluster's NAT egress IPs
  are **already allowlisted** by Mozilla while Actions runners (shared, rotating
  IPs) are not. Verified 2026-07-27 from a pod in each AZ with the scraper's own
  httpx client: `3.67.52.124` (eu-central-1a) and `63.182.70.185` (eu-central-1b)
  both return 200 + JSON; the same request from a non-allowlisted network returns
  the challenge HTML. **So the API is NOT blocked from the cluster** — only from
  Actions and from developer workstations, which is why a local run still raises
  `ChallengeError` and is not evidence of an outage. The k8s manifests/Pulumi/
  ArgoCD app live in the
  separate `platform-infrastructure` repo; this repo only builds the image
  (`Dockerfile`, `.github/workflows/aaq-scraper-image.yml` → shared ECR via
  OIDC) and ships `deploy/entrypoint.sh` (clone → `run_refresh.py` → commit).
  Two prerequisites already landed here: (1) `sumo.py` **dropped Playwright**
  entirely — the Chromium challenge-bypass is itself now fingerprinted and
  blocked (issue #28) — and drives the same `SumoBrowser`/`fetch_json` public
  API over a plain **`httpx`** client instead — Playwright is kept only as an
  **optional dependency group** (`uv sync --group playwright`) so `poc.py` still
  runs; it is not installed by default and is absent from the image; (2)
  **`.refresh-hwm` is now
  tracked in git** (removed from `.gitignore`, no longer the Actions cache)
  since the pod is stateless and the repo is the durable state. The pod pushes
  its commits authenticated with a **fine-grained GitHub PAT** sourced from AWS
  Secrets Manager (synced in by External Secrets Operator) via a **git
  credential helper** — the token is read from the environment at call time and
  never appears in argv or a URL. The CronJob ships **suspended** with a
  placeholder image tag — the only remaining gate is mechanical (`pulumi up` for
  the ECR repo + OIDC role, create the Secrets Manager PAT, merge so the image
  builds, then pin the tag and unsuspend). Full design:
  `docs/superpowers/specs/2026-07-13-k8s-argocd-scraper-deployment-design.md`
  and `docs/superpowers/plans/2026-07-13-k8s-argocd-scraper-deployment.md`.
- **What is actually producing data right now: the browser extension**, not any
  GitHub workflow. As of 2026-07-27 **all three workflows are `disabled_manually`**
  (`scrape.yml` last ran 2026-07-10) — do not describe `scrape.yml` as the live
  refresh. The `extension/` add-on runs a scheduled auto-fetch from a real
  browser and its JSON bundles are imported by `import_json.py`, which reuses the
  scrapers' own writers (`build_fieldnames`, `flatten_answer`, `COLUMNS`), so
  extension-imported and `run_refresh.py`-generated CSVs are format-compatible.
  **Open decision:** whether the k8s CronJob *replaces* the extension or runs
  alongside it — both would commit the same day-CSVs on `main`, so running both
  duplicates work (the pod's rebase-retry copes with the race, but it is not a
  plan). Decide before unsuspending.

## Notes

- Output is **deterministic**: re-running a day yields byte-identical CSVs, which
  the scrape workflow relies on for its "no changes to commit" path.
- The legacy Ruby data had a **7-hour timezone bug** (UTC times mislabelled
  `-0700`, days shifted) and occasionally dropped questions; this scraper is
  UTC-correct. Verified against the live website for 2026-06-10 (see the
  `website-verification-*.json` fixtures under `2026/`).

## License & contribution

MPL-2.0. **All source files carry the MPL header by default** (Python after the
shebang, YAML at the top); add it to every new file. Participants must follow the
[Mozilla Community Participation Guidelines](https://www.mozilla.org/about/governance/policies/participation/).
