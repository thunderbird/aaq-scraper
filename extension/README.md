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

**For the repeatable weekly procedure, see the runbook:**
[`../docs/manual-refresh-runbook.md`](../docs/manual-refresh-runbook.md).

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

### Firefox: the extension's Fetch button does NOT work — use the console snippet

**Firefox blocks this extension from running any script on `support.mozilla.org`.**
Verified exhaustively (2026-07-13): temporary *and* signed installs;
`scripting.executeScript` *and* a manifest-declared content script; host
permission granted (`permissions.contains(tab.url)` = true); and with
`extensions.quarantinedDomains.enabled = false` + a restart. In every case
injection is refused ("Missing host permission for the tab") and the content
script won't load ("Receiving end does not exist"). This is Firefox restricting
extensions on Mozilla-owned domains; it is not fixable from the extension side.

**So on Firefox, use the page-console fallback below** (`console-snippet.js`) —
it runs as the page's own code, so it isn't subject to the extension sandbox.
The extension UI (Fetch button) is effectively **Chrome-only**.

## Signing for Firefox (unlisted / self-distribution)

> **Note:** signing does **not** make the Fetch button work on Firefox — Firefox
> blocks the extension's scripts on `support.mozilla.org` regardless (see above).
> Kept for reference / in case Firefox's domain restrictions change. On Firefox,
> use the console snippet.

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
   pick the signed `.xpi`. It persists across restarts — but note the Fetch
   button still won't run on `support.mozilla.org` (see the Firefox note above).

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

### Multi-week backfill (`console-snippet-8weeks.js`)

For a longer catch-up (default **8 trailing weeks**), use `console-snippet-8weeks.js`
instead. Same page-console mechanism, but it fetches each week as its own 7-day
window and **downloads one bundle per week** — so if a later week fails (429,
challenge expiry, network) the finished weeks are already saved and you only
re-run the failed one(s). It also honors SUMO's long 429 `Retry-After` windows
(~10–15 min) rather than aborting, jitters the delay between API calls (2–10 s),
and pauses a random 1–4 min between weeks. A full run can take a few **hours**, so
keep the machine awake (`caffeinate -dims` on macOS) and allow the browser's
"multiple downloads?" prompt on the first save. Import each downloaded bundle with
`import_json.py` as usual. See issue #45.

## Background auto-fetch (opt-in, Chrome only)

Instead of clicking Fetch by hand, the extension can fetch on a **schedule** and
download a bundle per product automatically (issue #46). It's **off by default**;
enable it in the popup under **Auto-fetch on a schedule**:

- **Every day at** + **Time zone** — the time of day it runs, as `HH:MM`
  (24-hour, default **06:00**), interpreted in either **UTC** (default) or your
  browser's **Local** wall-clock time (#53). It runs once a day at that time; the
  popup shows the resolved equivalent in the other zone so it's unambiguous. If
  the machine is asleep at that moment, Chrome fires the run shortly after it next
  wakes. **Local** mode stays pinned to the same wall-clock time across daylight-
  saving changes (the fire is recomputed and re-armed after each run), so its
  instant in UTC terms shifts by an hour across a DST boundary — that's expected.
  The zone only changes *when* the run happens, never *which* days are fetched
  (that's always completed UTC days — see Window below).
- **Window (days)** — trailing window of **completed** UTC days it fetches each
  run (default **7**). It ends at *yesterday* (today is still accumulating), and
  the window overlaps run-to-run, so a run that fails/aborts (429, challenge
  lapse, closed popup, service-worker eviction) is simply re-covered next run —
  no silent gaps.
- **Run background fetch now** triggers a run immediately and shows the result.
- **Desktop notifications** — when ticked, the extension posts an OS notification
  when a scheduled run **starts** (*"Alarm fired — fetching…"*) and **finishes**
  (*"Fetched 162 q / 262 a…"*, or a needs-attention / error message), so you see
  what happened without opening the popup. Optional; see permissions below.

**Seeing the status (v0.12.0).** A scheduled run happens with the popup closed,
so status is surfaced three ways:

- **Toolbar icon badge** — `…` while a run is in progress, `✓` on success, `!`
  when it needs attention or errored. Always visible, no popup needed.
- **Popup live line** — while a run is active the Auto-fetch status line shows the
  current phase live (*"🔄 Running (alarm) — Fetching Thunderbird Desktop… question
  42/262"*), updating in real time whether the popup was open before the run or you
  open it mid-run. When idle it shows the **last run** summary.
- **Next-run countdown** — when idle and enabled, the popup shows *"⏰ Waiting for
  alarm — next run in 2h 14m (at 23:28)."*
- **Desktop notifications** — the start/finish toasts described above (opt-in).

Ticking **Auto-fetch** requests the **`alarms`** permission the first time, and
ticking **Desktop notifications** requests **`notifications`** — both are
*optional* permissions the extension doesn't hold unless you turn the respective
feature on (un-ticking removes them again). The badge and popup live line need no
permission. See [#46] and [#47].

It drives the **same in-page fetch** as the button, so the same caveats apply and
then some:

- **Chrome only.** It injects into a support.mozilla.org tab, which Firefox
  refuses for this add-on (see the Firefox note above). On Firefox it reports
  *needs attention* instead of fetching — use the console snippet there.
- **Keep a support.mozilla.org tab open** and browse it once so the Fastly
  challenge clears; the background run fetches from that tab. If none is open, or
  the challenge has lapsed, the popup's status line says so and the next run
  retries.
- **Keep the machine awake** (`caffeinate -dims` on macOS). It does **not**
  restore headless/CI scraping — the durable fix is still an allowlisted server
  ([#26]).
- It downloads bundles to your Downloads folder; you still run `import_json.py`
  on them (and commit) as below. A later iteration could auto-import.

## Use

1. Open a `https://support.mozilla.org/` tab and make sure you can browse it
   normally (i.e. the Fastly challenge has passed for your session).
2. Click the extension's toolbar icon.
3. Pick the **product**, the **start/end** date window (UTC; same day twice = one
   day, or a **range** with End after Start), and whether to **fetch answers too**.
   A range is fetched in one pass; keep it to about a week — wider windows are
   slow (a paginated answers call per question) and likelier to hit rate limits.
   On an HTTP 429 the fetch **honors `Retry-After` and retries**, but bounded
   (≤120s per wait, 3 retries) since this is an attended popup; if SUMO demands a
   longer wait it stops with a message so you can retry a smaller window later.
4. Host access to `support.mozilla.org` is required. **Chrome** grants it at
   install (nothing to do). **Firefox** does not, so click **Grant
   support.mozilla.org access** and allow the prompt (or toggle it on under
   `about:addons` → SUMO AAQ fetcher → Permissions), then reload the
   support.mozilla.org tab once.
5. Click **Fetch & download**. The status line updates **live** as it runs —
   question paging (`page N (M so far)`), answer progress (`question X/Y`), and
   any 429 waits (`waiting Ns, retry k/3`) — then it downloads
   `aaq-<product>-<dates>.json` to your Downloads folder. (If in-tab injection is
   blocked, it automatically retries the fetch from the extension itself.)

   > **Keep the popup focused while it runs — don't click the page, switch
   > windows, or change apps until you see "Done: … Saved …".** The browser
   > dismisses an extension popup the moment it loses focus, and the popup is
   > what awaits the fetch result; if it closes mid-fetch the run is dropped and
   > nothing downloads (this is browser popup behavior, not a bug). Keep windows
   > small (about a week, answers included) so each run finishes quickly. **For
   > long runs or anything you want to walk away from, use the console snippet
   > below instead** — it runs as the page's own code, so losing window focus
   > does not stop it.
6. Import it into CSVs:
   ```sh
   uv run python import_json.py ~/Downloads/aaq-thunderbird-2026-06-10.json
   ```
   This writes `2026/questions-thunderbird-desktop-2026-06-10.csv` (and the
   matching `answers-…` CSV if answers were fetched), then you commit as usual.
   A **multi-day** bundle is split automatically into one `…-YYYY-MM-DD.csv` per
   day (questions bucketed by their `created` day, answers by their parent
   question's day) — each byte-identical to a single-day fetch, so it drops
   straight into the tracked per-day layout. `--questions-out`/`--answers-out`
   apply only to a single-day bundle.

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
  + `downloads` + `storage`; `alarms` and `notifications` are **optional**
  permissions requested when background auto-fetch (#47) and desktop
  notifications (v0.12.0) are respectively enabled.
- `common.js` — `browser`/`chrome` namespace shim + `API_BASE` + product slugs.
- `fetch-core.js` — the shared in-page fetch loop (`aaqFetch`), used by the
  content script, the popup, and the background worker.
- `popup.html` / `popup.js` — the UI, the manual fetch, and the background
  auto-fetch controls.
- `background.js` — the opt-in scheduled keep-alive worker (`alarms`); drives the
  same `aaqFetch` in an open SUMO tab and downloads a bundle per product (#46).
  Writes live run status to storage and drives the toolbar badge + optional
  desktop notifications (v0.12.0).
- `icons/` — the 🕷 toolbar/listing icons; regenerate with
  `uv run python make-icons.py`.

[#26]: https://github.com/thunderbird/aaq-scraper/issues/26
[#29]: https://github.com/thunderbird/aaq-scraper/issues/29
[#46]: https://github.com/thunderbird/aaq-scraper/issues/46
[#47]: https://github.com/thunderbird/aaq-scraper/issues/47
