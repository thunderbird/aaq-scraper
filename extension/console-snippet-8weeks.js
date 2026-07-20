// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// MULTI-WEEK PAGE-CONSOLE backfill for the SUMO AAQ fetcher (aaq-scraper #29).
//
// Like console-snippet.js, but fetches the last N weeks (default 8) as N
// separate 7-day windows and downloads ONE bundle PER WEEK — so if a later week
// fails (429, challenge expiry, network) the weeks already done are safe on disk
// and you only re-run the failed week(s). It also honors SUMO's long 429
// Retry-After windows (~10-15 min) instead of aborting, jitters the delay
// between API calls, and pauses a random 1-4 min between weeks.
//
// WHY per-week + long 429 wait: a single monolithic multi-hour run over ~1500
// calls almost always trips a 429 whose Retry-After exceeds a short cap, and a
// throw there loses the whole batch. Per-week downloads + a generous 429 cap
// remove the "lose everything" failure mode (see #43 discussion / issue).
//
// HOW TO RUN
//   1. Open a support.mozilla.org tab and make sure it browses normally
//      (the Fastly challenge has passed for your session).
//   2. Keep the machine awake for the whole run (it can take a few hours):
//        macOS:  run `caffeinate -dims` in a terminal first.
//   3. Open DevTools -> Console (F12). Firefox blocks pasting into the console
//      the first time: type `allow pasting` and press Enter when prompted.
//   4. Edit the CONFIG block below, paste the whole file, press Enter. The first
//      download may trigger a browser "allow multiple downloads?" prompt -> allow.
//   5. It downloads one aaq-<product>-<start>_<end>.json per week. Then, per file:
//        uv run python import_json.py ~/Downloads/aaq-<product>-<start>_<end>.json
//      (import_json.py splits each multi-day bundle into per-day CSVs.)
(async () => {
  // ===== CONFIG - edit these =====
  const product = "thunderbird";       // "thunderbird" (Desktop) or "thunderbird-android"
  const weeks = 8;                     // how many trailing 7-day windows to fetch
  const endDate = "";                  // "" = today (UTC); else YYYY-MM-DD = last day of the most recent week
  const includeAnswers = true;         // also fetch each question's answers
  const minDelayS = 2, maxDelayS = 10; // jitter between API calls (seconds)
  const minWeekPauseMin = 1, maxWeekPauseMin = 4; // pause between weeks (minutes)
  const max429WaitS = 900;             // honor a 429 Retry-After up to this (SUMO's windows are ~10-15 min)
  const max429Retries = 5;             // retries before giving up on a 429
  // ===============================

  const API = "https://support.mozilla.org/api/2/";
  const pad = (n) => String(n).padStart(2, "0");
  const dayStr = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const fmt = (d) =>
    `${dayStr(d)}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
  const ymd = (s) => s.split("-").map(Number);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Inclusive-integer random in [lo, hi].
  const rand = (lo, hi) => lo + Math.floor(Math.random() * (hi - lo + 1));
  const callDelayMs = () => rand(minDelayS, maxDelayS) * 1000;

  const retryAfterSeconds = (v) => {
    if (!v) return null;
    const n = Number(v);
    if (Number.isFinite(n)) return Math.max(0, n);
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : Math.max(0, (t - Date.now()) / 1000);
  };
  const getJson = async (url) => {
    for (let attempt = 0; ; attempt++) {
      const res = await fetch(url, { headers: { Accept: "application/json" }, credentials: "include" });
      const text = await res.text();
      let json = null; try { json = JSON.parse(text); } catch (e) { /* */ }
      if (res.status === 429) {
        const raHdr = res.headers.get("retry-after");
        const waitS = retryAfterSeconds(raHdr);
        if (attempt >= max429Retries)
          throw new Error(`HTTP 429; gave up after ${max429Retries} retries (Retry-After ${raHdr})`);
        if (waitS !== null && waitS > max429WaitS)
          throw new Error(`HTTP 429: asked to wait ${Math.round(waitS)}s (> ${max429WaitS}s cap) - retry this week later.`);
        const w = waitS !== null ? waitS : Math.min(max429WaitS, 5 * (attempt + 1));
        console.log(`[aaq] HTTP 429 - waiting ${Math.round(w)}s, retry ${attempt + 1}/${max429Retries}`);
        await sleep(w * 1000);
        continue;
      }
      if (json === null) throw new Error(`HTTP ${res.status} non-JSON (challenge/block?): ${text.slice(0, 120)}`);
      return json;
    }
  };

  // Fetch one [start, end] window (YYYY-MM-DD) and download its bundle.
  const fetchWeek = async (start, end) => {
    // Window math mirrors scrape_questions.py: start 00:00:00 -1s; end +1 day.
    const sDt = new Date(`${start}T00:00:00Z`);
    const eDt = new Date(`${end}T00:00:00Z`);
    const gt = fmt(new Date(sDt.getTime() - 1000));
    const lt = fmt(new Date(eDt.getTime() + 86400000));
    const lessThan = new Date(lt);

    const questions = [];
    let stop = false, page = 0;
    let url = `${API}question/?${new URLSearchParams({ format: "json", product, created__gt: gt, created__lt: lt, ordering: "created" })}`;
    while (url && !stop) {
      const d = await getJson(url);
      page++;
      for (const q of (d.results || [])) {
        const c = q.created ? new Date(q.created) : null;
        if (c && c >= lessThan) { stop = true; break; }
        questions.push(q);
      }
      console.log(`[aaq]   questions page ${page} (${questions.length} so far)`);
      if (stop) break;
      url = d.next;
      if (url) await sleep(callDelayMs());
    }

    const answers = [];
    if (includeAnswers) {
      for (let i = 0; i < questions.length; i++) {
        const q = questions[i];
        console.log(`[aaq]   answers: question ${i + 1}/${questions.length}`);
        let a = `${API}answer/?${new URLSearchParams({ format: "json", question: String(q.id), ordering: "created" })}`;
        while (a) {
          const d = await getJson(a);
          for (const x of (d.results || [])) answers.push(x);
          a = d.next;
          if (a) await sleep(callDelayMs());
        }
        await sleep(callDelayMs());
      }
    }

    const bundle = { product, start: ymd(start), end: ymd(end), questions };
    if (includeAnswers) bundle.answers = answers;
    const name = `aaq-${product}-${start}_${end}.json`;
    const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    console.log(`[aaq]   downloaded ${name} (${questions.length} questions, ${answers.length} answers)`);
    return { questions: questions.length, answers: answers.length, name };
  };

  // Build N trailing 7-day windows, oldest first. Week i ends `end` - i*7 days.
  const end0 = endDate ? new Date(`${endDate}T00:00:00Z`) : new Date(`${dayStr(new Date())}T00:00:00Z`);
  const windows = [];
  for (let w = weeks - 1; w >= 0; w--) {
    const wEnd = new Date(end0.getTime() - w * 7 * 86400000);
    const wStart = new Date(wEnd.getTime() - 6 * 86400000);
    windows.push([dayStr(wStart), dayStr(wEnd)]);
  }

  console.log(`[aaq] ${product}: ${weeks} weeks, ${windows[0][0]} .. ${windows[windows.length - 1][1]}`);
  const done = [], failed = [];
  for (let i = 0; i < windows.length; i++) {
    const [start, end] = windows[i];
    console.log(`[aaq] week ${i + 1}/${windows.length}: ${start} .. ${end}`);
    try {
      const r = await fetchWeek(start, end);
      done.push({ start, end, ...r });
    } catch (e) {
      console.error(`[aaq] week ${i + 1} (${start}..${end}) FAILED: ${(e && e.message) || e}`);
      failed.push({ start, end, error: String((e && e.message) || e) });
    }
    // Random 1-4 min pause between weeks (not after the last).
    if (i < windows.length - 1) {
      const pauseMs = rand(minWeekPauseMin * 60, maxWeekPauseMin * 60) * 1000;
      console.log(`[aaq] pausing ${Math.round(pauseMs / 1000)}s before next week…`);
      await sleep(pauseMs);
    }
  }

  console.log(`[aaq] DONE. ${done.length}/${windows.length} weeks downloaded.`);
  if (failed.length) {
    console.warn(`[aaq] ${failed.length} week(s) FAILED - re-run just these (set endDate + weeks, or use console-snippet.js per week):`);
    for (const f of failed) console.warn(`[aaq]   ${f.start}..${f.end}: ${f.error}`);
  }
})();
