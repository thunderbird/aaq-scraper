# aaq-scraper

Until we get an official API that has a way to prevent DOS-ing, we scrape the SUMO
(support.mozilla.org) API. Since ~June 2026 the API sits behind a JavaScript
challenge that blocks headless HTTP (see
[thunderbird/github-action-thunderbird-aaq#34](https://github.com/thunderbird/github-action-thunderbird-aaq/issues/34)).
A real-browser (Playwright/Chromium) workaround passed the challenge for a
while but is now itself fingerprinted and blocked (issue #28), so the scraper
is a plain **`httpx`** client instead, and is being redeployed as a
**Kubernetes CronJob** on a cluster with a stable egress IP that Mozilla can
allowlist (issue #27) — see the design and plan docs under
[`docs/superpowers/specs/2026-07-13-k8s-argocd-scraper-deployment-design.md`](docs/superpowers/specs/2026-07-13-k8s-argocd-scraper-deployment-design.md)
and
[`docs/superpowers/plans/2026-07-13-k8s-argocd-scraper-deployment.md`](docs/superpowers/plans/2026-07-13-k8s-argocd-scraper-deployment.md).
Until that IP is allowlisted, live API calls remain blocked and the hourly
GitHub Actions refresh (`scrape.yml`) stays the production path.

## Proof of concept (Bucket 0)

```sh
uv sync
uv run playwright install chromium
uv run python poc.py            # headed — most likely to pass the challenge
uv run python poc.py --headless # try headless (closer to CI)
uv run python poc.py --dump     # also write the raw first API page to poc-sample.json
```

Success = the script prints a non-zero `count` and real question records (not
challenge HTML), and reports where `taken_by` / `operating_system` /
`thunderbird_version` live in the API response.

## Scraping questions and answers

```sh
# Questions for a UTC date window (single day = same date twice).
uv run python scrape_questions.py 2026 6 10 2026 6 10 --headless

# Answers for those questions (defaults to the matching answers-... filename).
uv run python scrape_answers.py \
    --questions 2026/questions-thunderbird-desktop-2026-06-10.csv --headless

# Thunderbird for Android: same tools, --product thunderbird-android.
uv run python scrape_questions.py 2026 6 10 2026 6 10 \
    --product thunderbird-android --headless
```

Output is written to `<year>/<questions|answers>-<product>-<dates>.csv`, e.g.
`2026/questions-thunderbird-desktop-2026-06-10.csv`. CSVs are sorted by ascending
`id`. Both scrapers add a polite delay between API calls — a fixed `--sleep 2`
seconds by default, or `--random-delay` to vary it between `--min-delay` and
`--max-delay` (2–10s).

Questions keep the original columns/flattening plus `operating_system`,
`thunderbird_version`, and `taken_by`. Answers use the original columns:
`id, question_id, created, updated, content, creator, is_spam, num_helpful,
num_unhelpful`.

The `2026/` directory holds committed fixtures from a verification run against
2026-06-10 (a pre-challenge day), reconciled against the public website.

## Schema drift check

`check_schema.py` samples the live API and compares its JSON fields against the
committed baseline `schema/expected-fields.json`:

```sh
uv run python check_schema.py --headless                  # exit 1 on drift
uv run python check_schema.py --headless --update-baseline  # manual bump
```

A daily workflow (`.github/workflows/schema-check.yml`) runs the check and opens
(or comments on) a `schema-change` issue when fields are added or removed. The
baseline is **only** updated manually: when the API legitimately changes, review
the drift, re-run with `--update-baseline`, and commit — and if a field was
*removed*, update the scrapers so the affected CSV columns don't silently blank.

* We require all those who participate in this repo to agree and adhere to the [Mozilla Community Participation Guidelines](https://www.mozilla.org/about/governance/policies/participation/)

