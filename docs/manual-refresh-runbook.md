<!-- This Source Code Form is subject to the terms of the Mozilla Public
     License, v. 2.0. If a copy of the MPL was not distributed with this
     file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# Manual refresh runbook — a week of AAQ data via the browser extension

While the Playwright scraper is blocked by Fastly's automated-browser
fingerprinting (issue #26; the hourly `scrape.yml` is disabled), keep the tracked
data current by fetching from a **genuine browser** with the extension (or the
page-console fallback) and importing to the usual per-day CSVs. See
[`../extension/README.md`](../extension/README.md) for install/signing details;
this is the repeatable weekly procedure.

**It's attended and manual** — it does not restore the hourly automation. The
durable fix is still moving the scraper to an allowlisted constant-IP server.

> **Less clicking (Chrome):** the extension has an opt-in **background auto-fetch**
> that runs this fetch on a schedule (default: every 24h, trailing 7-day window)
> and downloads the bundles for you — you still `import_json.py` + commit. It
> needs a support.mozilla.org tab open and the machine awake, and it's Chrome
> only. See [`../extension/README.md`](../extension/README.md#background-auto-fetch-opt-in-chrome-only)
> (issue #46). The manual steps below still apply for Firefox and for backfills.

## One-time setup

- **Chrome:** `chrome://extensions` → Developer mode → **Load unpacked** →
  `extension/`. (Host permission is auto-granted; nothing else to do.)
- **Firefox:** the extension's Fetch button does **not** work — Firefox blocks
  extension scripts on `support.mozilla.org` (verified across signed/temp,
  executeScript + content script, permission granted, quarantine off). **Use the
  page-console fallback** (`extension/console-snippet.js`) on Firefox instead.
- `uv sync` in the repo (for `import_json.py`).

## Weekly procedure

Do this for **both products** — the API slugs are `thunderbird` (Desktop) and
`thunderbird-android`. Pick a window of **about a week** (wider is slow and more
likely to hit rate limits).

1. **Warm the session.** Open a `https://support.mozilla.org/` tab and browse a
   page so the Fastly challenge clears.
2. **Fetch (per product).**
   - **Chrome (extension):** click the 🕷 toolbar icon → choose the product, set
     **Start** and **End** (UTC, e.g. `2026-07-01` … `2026-07-07`), leave **Fetch
     answers too** checked → **Fetch & download**. You get
     `aaq-<product>-<start>_<end>.json` in Downloads. A 429 is auto-retried
     honoring `Retry-After` (bounded ≤120s × 3); if it needs a longer wait, retry
     a smaller window later.
   - **Firefox (console snippet — the extension button doesn't work here):** open
     `extension/console-snippet.js`, edit `product`/`start`/`end` in its CONFIG
     block, and paste it into the SUMO tab's DevTools console (type
     `allow pasting` first). It downloads the same bundle.
3. **Import (per bundle).** From the repo root:
   ```sh
   uv run python import_json.py ~/Downloads/aaq-thunderbird-2026-07-01_2026-07-07.json
   uv run python import_json.py ~/Downloads/aaq-thunderbird-android-2026-07-01_2026-07-07.json
   ```
   A multi-day bundle is **split automatically** into one
   `<year>/questions-<label>-YYYY-MM-DD.csv` (and matching `answers-…`) **per
   day** — questions bucketed by `created` day, answers by their parent
   question's day. Each per-day file is byte-identical to a single-day scrape.
4. **Review & commit.** `git status` / `git diff` — expect real changes only
   (new/edited questions & answers). A benign **column-order** shift can appear
   because SUMO changed its API field order since the 2026-07-09 freeze (same
   fields; `build_fieldnames` uses first-seen key order). Then:
   ```sh
   git add 2026/ && git commit -m "Manual refresh <window> via extension" && git push
   ```

## Verifying (optional)

To confirm a fetch matches a known day, import a single-day bundle to a temp path
and diff against the committed file:
```sh
uv run python import_json.py ~/Downloads/aaq-thunderbird-2026-07-01.json \
  --questions-out /tmp/q.csv --answers-out /tmp/a.csv
diff /tmp/q.csv 2026/questions-thunderbird-desktop-2026-07-01.csv
```
(`--questions-out`/`--answers-out` are single-day only.)

## Gotchas

- **Keep the Chrome popup focused while it fetches** — the browser closes an
  extension popup the instant it loses focus (clicking the page, switching
  windows/apps, minimizing), and the popup is what awaits the fetch result, so a
  mid-fetch close **drops the run** and nothing downloads. This is browser popup
  behavior, not a bug. Keep windows to ~a week so runs finish quickly; for long
  runs or anything you want to walk away from, use the **console snippet**
  (runs as the page's own code, so focus loss doesn't stop it).
- **Firefox needs the signed install** — temporary add-ons fail with "Missing
  host permission for the tab" even when the popup shows access granted.
- **Re-signing:** bump `manifest.json` `version`, delete any stale
  `extension/.amo-upload-uuid`, and **`export` the AMO creds as separate
  statements** before `web-ext sign` (inline `VAR=x cmd "$VAR"` sends an empty
  key → misleading "Unknown JWT iss"). Full checklist in `extension/README.md`.
- **Both products** — the en-US website shows only en-US, but the API returns all
  locales; that's expected (see `CLAUDE.md`).
- The Playwright pipeline (`sumo.py`, `scrape_*.py`, `run_refresh.py`) is
  untouched and ready for when the constant-IP server lands.
