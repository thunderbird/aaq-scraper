<!-- This Source Code Form is subject to the terms of the Mozilla Public
     License, v. 2.0. If a copy of the MPL was not distributed with this
     file, You can obtain one at https://mozilla.org/MPL/2.0/.

     (This MPL notice also covers manifest.json, which as strict JSON cannot
     carry an inline comment.) -->

# SUMO AAQ fetcher — browser-extension stopgap

A tiny WebExtension that fetches the Thunderbird "Ask a Question" data from
`support.mozilla.org` **from inside a genuine browser**, then downloads a
raw-JSON bundle. A companion script, [`../import_json.py`](../import_json.py),
turns that bundle into the same CSVs the Playwright scrapers produce —
**byte-for-byte identical**, because it reuses the existing
`flatten_question` / `flatten_answer` / `build_fieldnames` helpers unchanged.

## Why this exists

Since ~June 2026 the SUMO API sits behind a Fastly JS/WAF challenge, and as of
2026-07-09 Fastly **fingerprints the automated browser itself** — headless *and*
headed Playwright are blocked ([#26]). This extension runs the same same-origin
`fetch()` the scraper does, but from a **real Firefox/Chrome** that already holds
the challenge cookies and carries a normal browser fingerprint — which Fastly is
not blocking. See [#29].

### It's a stopgap, not a replacement

- **Attended.** You open SUMO, click a button. It cannot run in GitHub Actions
  (no real-browser fingerprint there — that's the whole problem).
- **Doesn't solve the constant-IP need.** It runs on whatever machine's browser
  you use. It bridges the gap until the scraper moves to an allowlisted server.
- The Playwright pipeline (`sumo.py`, `scrape_*.py`, `run_refresh.py`) is left
  **completely untouched** and stays ready for when the server lands.

## Install (load unpacked)

**Chromium (Chrome / Edge / Brave):**
1. Go to `chrome://extensions`.
2. Toggle **Developer mode** (top-right).
3. **Load unpacked** → select this `extension/` folder.
4. It stays installed across restarts.

**Firefox:**
1. Go to `about:debugging#/runtime/this-firefox`.
2. **Load Temporary Add-on…** → select `extension/manifest.json`.
3. Note: a *temporary* add-on **unloads when Firefox restarts** — just re-load
   it. (Permanent install needs AMO signing; out of scope for a stopgap.)

## Use

1. Open a `https://support.mozilla.org/` tab and make sure you can browse it
   normally (i.e. the Fastly challenge has passed for your session).
2. Click the extension's toolbar icon.
3. Pick the **product**, the **start/end** date window (UTC; same day twice = one
   day), and whether to **fetch answers too**.
4. The **first** click prompts for access to `support.mozilla.org` — allow it.
   Host access is an *optional* permission (Firefox temporary add-ons and Chrome
   both grant it on request, not at install), and it's required for the in-page
   fetch. If you ever deny it, re-grant via `about:addons` → SUMO AAQ fetcher →
   Permissions.
5. Click **Fetch & download**. It downloads `aaq-<product>-<dates>.json` to your
   Downloads folder.
6. Import it into CSVs:
   ```sh
   uv run python import_json.py ~/Downloads/aaq-thunderbird-2026-06-10.json
   ```
   This writes `2026/questions-thunderbird-desktop-2026-06-10.csv` (and the
   matching `answers-…` CSV if answers were fetched), then you commit as usual.

## How it maps to the scraper

| Scraper step | Extension equivalent |
|---|---|
| `SumoBrowser` passes the challenge | your real browsing session already did |
| `page.evaluate` in-page `fetch()` | `scripting.executeScript` (ISOLATED world) in the tab (`popup.js` → `fetchInPage`); same-origin fetch reuses the browser's cookies |
| `created__gt/lt` window + ascending early-stop | same math in `popup.js` (`fmtStamp`, `fetchInPage`) |
| `/api/2/question/`, `/api/2/answer/` pagination | same, following `next` |
| `flatten_*` / `build_fieldnames` / atomic write | `import_json.py` (imported unchanged) |

## Files

- `manifest.json` — MV3, *optional* host permission for `support.mozilla.org`
  (requested on first fetch), `scripting`
  + `downloads`.
- `common.js` — `browser`/`chrome` namespace shim + `API_BASE` + product slugs.
- `popup.html` / `popup.js` — the UI and the injected fetch loop.

[#26]: https://github.com/thunderbird/aaq-scraper/issues/26
[#29]: https://github.com/thunderbird/aaq-scraper/issues/29
