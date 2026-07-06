<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this
   - file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# The SUMO JS/WAF bot challenge is edge infrastructure, not a kitsune commit

**Question that led here:** *Can we find the kitsune commit that added the JS
challenge blocking the API?*

**Answer: no — because it is not in kitsune.** The challenge that returns an HTML
bot-challenge page instead of JSON is a **Fastly-edge WAF / bot-management layer in
front of `support.mozilla.org`**, deployed in Mozilla's private infrastructure. It
never appears as a commit in the public
[mozilla/kitsune](https://github.com/mozilla/kitsune) application repo.

## How this was confirmed

Two independent lines of evidence agree:

1. **Exhaustive search of `mozilla/kitsune` (2026-07-05)** found no
   challenge/bot-mitigation feature: whole-tree `grep` for
   `challenge|captcha|turnstile|bot-detect|proof-of-work|deflect|waf`; `git log -S`
   pickaxe across **all branches** (May–Jul 2026) for `js_challenge`, `Turnstile`,
   `proof_of_work`, etc.; `gh search prs` since 2026-01-01; and a new-file / June
   middleware+settings diff scan. Only unrelated hits. The June kitsune commits do
   confirm the *edge* layer exists — `e770a57eb` "Switch GeoIP to Fastly"
   (2026-05-18), `525698c38` "Allow webservices.mozgcp.net" (2026-06-24) — but the
   challenge rule lives in that edge config, not in app code.

2. **Thunderbird + Mozilla issue trail** (both filed by @aatchison):
   - Upstream: **[mozilla/sumo#3124](https://github.com/mozilla/sumo/issues/3124)**
     — *"API: /api/2/question/ intermittently returns HTTP 500 with
     ordering=updated + updated__gt"* (opened 2026-06-12, **CLOSED**). It began as
     the intermittent-500 report (fixed upstream by the read-only profile
     serialization, kitsune `b877d17e` / `29401be3`), then was updated with the
     bot-challenge discovery.
   - Downstream/ops: **[thunderbird/bitergia-deploy#50](https://github.com/thunderbird/bitergia-deploy/issues/50)**
     — *"SUMO/Kitsune ingestion blocked: API returns HTML bot/WAF challenge (needs
     Mozilla allowlist)"* (opened 2026-06-15, **OPEN**).

## What the issues document

**The symptom.** `GET /api/2/question/` returns an **HTML JS bot-challenge with
`HTTP 200`, not JSON** — for *every* query, including an unfiltered
`?ordering=-updated&page=1`. Observed response headers (from sumo#3124):

```
HTTP/2 200
content-type: text/html; charset=utf-8
set-cookie: _fs_ch_st_FSBmUei20MqUiJb9=…; Max-Age=10; HttpOnly; Path=/
x-served-by: cache-fra-…-FRA, cache-fra-…-FRA
x-cache: MISS
```

**The signature.** The body is a JS challenge that loads bot-management assets
under **`/_fs-ch-…/`** and sets **`_fs_ch_*`** cookies — i.e. a bot/WAF challenge
at the **Fastly edge** (`x-served-by: cache-fra…` = Fastly Frankfurt POP). The
`_fs_ch` / `fs-ch` naming is Fastly's challenge/bot-management surface.

**The collector failure.** A server-side collector (GrimoireLab/perceval) can't
execute JS, so it can't parse the HTML as JSON:

```
perceval...kitsune ERROR - Expecting value: line 1 column 1 (char 0)
grimoire_elk ERROR - Bad JSON format for mozilla_questions: <!DOCTYPE html> ...
```

**When it started.** ~**2026-04-29** — exactly where the Bitergia-collected SUMO
data stops — consistent with bot protection being enabled on the API around then.
Reproduces from the collector's **AWS eu-central-1** egress with **both** a browser
and an `Accept: application/json` client, and from a second network.

**Why it's unfixable client-side (for a server-side collector).** No client change
works, because a headless HTTP client cannot pass a JS challenge. The prior
mitigations (rate-limit throttle #44, 500-retry #46, study-crash fix #48) were
still necessary but cannot reach the data; the non-incremental backfill
(mozilla/kitsune#40) was closed as not-viable.

**What's actually needed.** Engage Mozilla/SUMO to let the collector through —
**allowlist the collector's static egress IP(s)** for `/api/2/` (exempt from the
challenge), or issue **API-token auth** that bypasses it. Epic:
thunderbird/platform-infrastructure#84.

## Why *this* scraper is not blocked (the relevant contrast)

The GrimoireLab collector is a **server-side HTTP client** and therefore cannot
pass a JS challenge. **This repo (`aaq-scraper`) deliberately takes the opposite
approach** and is unaffected by the same root cause (tracked for us as
thunderbird/github-action-thunderbird-aaq#34):

- `SumoBrowser` (`sumo.py`) drives a **real Chromium via Playwright**, loads the
  site once to acquire the Fastly challenge cookies (`_fs_ch_*`), then does an
  **in-page `fetch()`** (`page.evaluate`) so the API request runs inside the
  browser's authenticated, challenge-passed context. Headless works locally **and
  in GitHub Actions**.

So the same Fastly edge protection that blocks the headless collector (#50 /
sumo#3124) is exactly what our browser-passes-challenge design is built to survive.
Watch both issues: if Mozilla grants the collector an IP allowlist / API token, or
tightens the challenge to behavioral checks a scripted browser can't pass, **both**
the Bitergia collector and this scraper are affected.

## Related docs in this repo

- [`kitsune-api-commits-2026.md`](kitsune-api-commits-2026.md) — direct commits to
  the Q&A and KB API code since 2026-01-01.
- [`troubleshooting-metadata-history.md`](troubleshooting-metadata-history.md) —
  history of the `troubleshooting` metadata field.

---

*Sources: [mozilla/sumo#3124](https://github.com/mozilla/sumo/issues/3124) and
[thunderbird/bitergia-deploy#50](https://github.com/thunderbird/bitergia-deploy/issues/50)
(read 2026-07-05); search of a `mozilla/kitsune` clone on 2026-07-05.*
