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

## One-time setup

- **Chrome:** `chrome://extensions` → Developer mode → **Load unpacked** →
  `extension/`. (Host permission is auto-granted; nothing else to do.)
- **Firefox:** install the **signed** `.xpi` (see the "Signing for Firefox"
  section of `extension/README.md`) via `about:addons` → ⚙ → Install Add-on From
  File. A temporarily-loaded add-on will *not* work (host permission isn't
  enforced) — use the signed install, or the page-console fallback.
- `uv sync` in the repo (for `import_json.py`).

## Weekly procedure

Do this for **both products** — the API slugs are `thunderbird` (Desktop) and
`thunderbird-android`. Pick a window of **about a week** (wider is slow and more
likely to hit rate limits).

1. **Warm the session.** Open a `https://support.mozilla.org/` tab and browse a
   page so the Fastly challenge clears.
2. **Fetch (per product).** Click the 🕷 toolbar icon → choose the product, set
   **Start** and **End** (UTC, e.g. `2026-07-01` … `2026-07-07`), leave **Fetch
   answers too** checked → **Fetch & download**. You get
   `aaq-<product>-<start>_<end>.json` in Downloads.
   - *Firefox first run:* click **Grant support.mozilla.org access**, then reload
     the SUMO tab once, then Fetch.
   - *Rate limiting:* a 429 is auto-retried honoring `Retry-After` (bounded
     ≤120s × 3). If it stops asking for a longer wait, retry a smaller window
     later.
   - *No extension?* Use `extension/console-snippet.js`: edit `product`/`start`/
     `end` in its CONFIG block, paste into the SUMO tab's DevTools console
     (Firefox: type `allow pasting` first).
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
