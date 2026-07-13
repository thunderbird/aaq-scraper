// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// PAGE-CONSOLE fallback for the SUMO AAQ fetcher (aaq-scraper #29).
//
// Use this when the extension's in-tab injection is refused (notably Firefox
// temporary add-ons, which don't put host permissions into force for scripting
// / CORS). Because this runs in the page's OWN context it needs no extension
// and no permissions — it's the same same-origin fetch the site itself makes,
// so it rides your real session past the Fastly challenge.
//
// HOW TO RUN
//   1. Open a support.mozilla.org tab and make sure it browses normally
//      (the Fastly challenge has passed for your session).
//   2. Open DevTools → Console (F12). Firefox blocks pasting into the console
//      the first time: type `allow pasting` and press Enter when prompted.
//   3. Edit the CONFIG block below, paste the whole file, press Enter.
//   4. It downloads aaq-<product>-<dates>.json. Then, in the repo:
//        uv run python import_json.py ~/Downloads/aaq-<product>-<dates>.json
(async () => {
  // ===== CONFIG — edit these =====
  const product = "thunderbird";       // "thunderbird" (Desktop) or "thunderbird-android"
  const start = "2026-07-01";          // window start, UTC, YYYY-MM-DD
  const end = "2026-07-01";            // window end (same as start = one day)
  const includeAnswers = true;         // also fetch each question's answers
  // ===============================

  const API = "https://support.mozilla.org/api/2/";
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (d) =>
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
  const ymd = (s) => s.split("-").map(Number);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Window math mirrors scrape_questions.py: start 00:00:00 -1s; end +1 day.
  const sDt = new Date(`${start}T00:00:00Z`);
  const eDt = new Date(`${end}T00:00:00Z`);
  const gt = fmt(new Date(sDt.getTime() - 1000));
  const lt = fmt(new Date(eDt.getTime() + 86400000));
  const lessThan = new Date(lt);

  const getJson = async (url) => {
    const res = await fetch(url, { headers: { Accept: "application/json" }, credentials: "include" });
    const text = await res.text();
    let json = null; try { json = JSON.parse(text); } catch (e) { /* */ }
    if (res.status === 429) throw new Error(`HTTP 429 (Retry-After ${res.headers.get("retry-after")})`);
    if (json === null) throw new Error(`HTTP ${res.status} non-JSON (challenge/block?): ${text.slice(0, 120)}`);
    return json;
  };

  const questions = [];
  let stop = false;
  let url = `${API}question/?${new URLSearchParams({ format: "json", product, created__gt: gt, created__lt: lt, ordering: "created" })}`;
  while (url && !stop) {
    const d = await getJson(url);
    for (const q of (d.results || [])) {
      const c = q.created ? new Date(q.created) : null;
      if (c && c >= lessThan) { stop = true; break; }
      questions.push(q);
    }
    if (stop) break;
    url = d.next;
    if (url) await sleep(2000);
  }
  console.log(`questions: ${questions.length}`);

  const answers = [];
  if (includeAnswers) {
    for (const q of questions) {
      let a = `${API}answer/?${new URLSearchParams({ format: "json", question: String(q.id), ordering: "created" })}`;
      while (a) {
        const d = await getJson(a);
        for (const x of (d.results || [])) answers.push(x);
        a = d.next;
        if (a) await sleep(2000);
      }
      await sleep(2000);
    }
    console.log(`answers: ${answers.length}`);
  }

  const bundle = { product, start: ymd(start), end: ymd(end), questions };
  if (includeAnswers) bundle.answers = answers;

  const dates = start === end ? start : `${start}_${end}`;
  const name = `aaq-${product}-${dates}.json`;
  const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  console.log(`downloaded ${name}`);
})();
