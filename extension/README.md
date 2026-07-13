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

**Firefox — signed install (recommended; the in-tab fetch works):**
See "Signing for Firefox" below. A *temporarily*-loaded MV3 add-on does **not**
get its host permission enforced for `scripting`/CORS (the in-tab fetch fails
with "Missing host permission for the tab" even though the popup shows access
granted). A **signed, installed** add-on does — so on Firefox, sign it once and
install the `.xpi`.

**Firefox — temporary load (dev only; use the page-console fallback to fetch):**
1. `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → select
   `extension/manifest.json`. Unloads on restart.
2. Because of the limitation above, Fetch won't work here — use the
   **page-console fallback** below.

## Signing for Firefox (unlisted / self-distribution)

Firefox release only runs signed add-ons, but Mozilla signs **unlisted** add-ons
automatically (no public listing, no review wait). One-time setup:

1. Create an add-on developer account, then generate API credentials
   (JWT issuer + secret) at
   <https://addons.mozilla.org/developers/addon/api/key/>.
2. From this `extension/` directory (uses `web-ext-config.cjs`, which keeps
   non-runtime files out of the package), with the credentials in your env:
   ```sh
   export AMO_JWT_ISSUER=user:xxxxx:yyy
   export AMO_JWT_SECRET=your-secret         # do NOT commit this
   npx web-ext sign --channel=unlisted \
       --api-key="$AMO_JWT_ISSUER" --api-secret="$AMO_JWT_SECRET"
   ```
   Mozilla signs it and drops the `.xpi` in `extension/web-ext-artifacts/`.
3. Install it: Firefox `about:addons` → gear ⚙ → **Install Add-on From File…** →
   pick the signed `.xpi`. It persists across restarts and its host permission is
   enforced, so **Fetch works**.

### Re-signing (checklist + gotchas)

1. Bump `version` in `manifest.json` — AMO rejects a duplicate version, and a
   half-finished upload "consumes" that version (error *"This upload has already
   been submitted"*). If a prior attempt left `extension/.amo-upload-uuid`, delete
   it so the new version uploads fresh.
2. `npx web-ext lint` — must report **0 errors** before signing.
3. **Export the credentials as their own statements** (as shown above). Do **not**
   inline them on the command line like
   `AMO_JWT_ISSUER=… AMO_JWT_SECRET=… npx web-ext sign --api-key="$AMO_JWT_ISSUER" …`
   — the shell expands `"$AMO_JWT_ISSUER"` against the *current* shell (empty)
   *before* the inline assignment applies, so web-ext gets an empty `--api-key`
   and AMO fails with the misleading *"Unknown JWT iss (issuer)"* (the credential
   is fine). Either `export` first, or pass the literal values to the flags.
4. Signing polls AMO for validation/approval and can take a couple of minutes;
   let it finish. The signed `.xpi` lands in `extension/web-ext-artifacts/`
   (gitignored). Install it via `about:addons` → ⚙ → Install Add-on From File.

## Page-console fallback (`console-snippet.js`) — no extension needed

Works in any browser with zero permissions, because it runs in the page's own
context: open a support.mozilla.org tab, open DevTools → Console (in Firefox,
type `allow pasting` first), edit the CONFIG block in `console-snippet.js`, paste
the whole file, and press Enter. It downloads the same `aaq-<product>-<dates>.json`
bundle for `import_json.py`.

## Use

1. Open a `https://support.mozilla.org/` tab and make sure you can browse it
   normally (i.e. the Fastly challenge has passed for your session).
2. Click the extension's toolbar icon.
3. Pick the **product**, the **start/end** date window (UTC; same day twice = one
   day), and whether to **fetch answers too**.
4. Host access to `support.mozilla.org` is required. **Chrome** grants it at
   install (nothing to do). **Firefox** does not, so click **Grant
   support.mozilla.org access** and allow the prompt (or toggle it on under
   `about:addons` → SUMO AAQ fetcher → Permissions), then reload the
   support.mozilla.org tab once.
5. Click **Fetch & download**. It downloads `aaq-<product>-<dates>.json` to your
   Downloads folder. (If in-tab injection is blocked, it automatically retries the
   fetch from the extension itself.)
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

- `manifest.json` — MV3, host permission for `support.mozilla.org`, `scripting`
  + `downloads`.
- `common.js` — `browser`/`chrome` namespace shim + `API_BASE` + product slugs.
- `popup.html` / `popup.js` — the UI and the injected fetch loop.
- `icons/` — the 🕷 toolbar/listing icons; regenerate with
  `uv run python make-icons.py`.

[#26]: https://github.com/thunderbird/aaq-scraper/issues/26
[#29]: https://github.com/thunderbird/aaq-scraper/issues/29
