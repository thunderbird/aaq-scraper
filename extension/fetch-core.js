// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Shared fetch loop, run in the support.mozilla.org page context. Paginates
// questions in the [gt, lt] window (ascending early-stop) and, optionally,
// each question's answers; honors 429 Retry-After (bounded). Same-origin
// fetch reuses the browser's cookies (incl. the httpOnly Fastly challenge
// cookie), so it's indistinguishable from the site's own requests.
//
// Loaded in TWO contexts: the declared content script (content.js) and, as a
// fallback, injected via scripting.executeScript from the popup. So it must be
// fully self-contained — no closures over popup scope, no `browser`/`chrome`.
// Returns { questions, answers, includeAnswers } or { error }.
async function aaqFetch(cfg) {
  const { apiBase, product, gt, lt, ordering, includeAnswers, delayMs,
          max429WaitS = 120, max429Retries = 3 } = cfg;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Live progress → popup. `rt` is present in the content-script and
  // executeScript worlds but ABSENT in the page's own context
  // (console-snippet), where report() silently no-ops. Wrapped in try/catch so a
  // closed popup (no receiver) can never throw into the fetch loop.
  const rt = (globalThis.browser ?? globalThis.chrome)?.runtime;
  const report = (info) => {
    try { if (rt) rt.sendMessage({ type: "aaq-progress", ...info }); }
    catch (e) { /* popup closed / no receiver */ }
  };

  // Parse a Retry-After header (delta-seconds or an HTTP-date) into seconds.
  function retryAfterSeconds(v) {
    if (!v) return null;
    const n = Number(v);
    if (Number.isFinite(n)) return Math.max(0, n);
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : Math.max(0, (t - Date.now()) / 1000);
  }

  async function getJson(url) {
    for (let attempt = 0; ; attempt++) {
      const res = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "include",
      });
      const text = await res.text();
      let json = null;
      try { json = JSON.parse(text); } catch (e) { /* non-JSON */ }
      if (res.status === 429) {
        // Honor Retry-After, bounded (attended popup — a long sleep risks the
        // popup closing and dropping the fetch). Beyond the cap/retries, abort.
        const raHdr = res.headers.get("retry-after");
        const waitS = retryAfterSeconds(raHdr);
        if (attempt >= max429Retries) {
          throw new Error(`HTTP 429 rate-limited; gave up after ${max429Retries} `
            + `retries` + (raHdr ? ` (Retry-After: ${raHdr})` : ""));
        }
        if (waitS !== null && waitS > max429WaitS) {
          throw new Error(`HTTP 429: SUMO asked to wait ${Math.round(waitS)}s `
            + `(> ${max429WaitS}s cap) — retry a smaller window later.`);
        }
        const w = waitS !== null ? waitS : Math.min(max429WaitS, 5 * (attempt + 1));
        console.log(`[aaq] HTTP 429 — waiting ${Math.round(w)}s, retry `
          + `${attempt + 1}/${max429Retries}`);
        report({ kind: "ratelimit", waitS: Math.round(w),
          attempt: attempt + 1, retries: max429Retries });
        await sleep(w * 1000);
        continue;
      }
      if (json === null) {
        throw new Error(
          `HTTP ${res.status} non-JSON response (Fastly challenge / block?): `
          + text.slice(0, 120));
      }
      return json;
    }
  }

  try {
    const lessThan = new Date(lt);
    const qParams = new URLSearchParams({
      format: "json", product, created__gt: gt, created__lt: lt, ordering,
    });
    let url = `${apiBase}question/?${qParams.toString()}`;
    const questions = [];
    let stop = false;
    let page = 0;
    while (url && !stop) {
      const data = await getJson(url);
      page++;
      for (const q of (data.results || [])) {
        const created = q.created ? new Date(q.created) : null;
        // ascending early-stop: once we pass the window, later rows are newer.
        if (created && created >= lessThan) { stop = true; break; }
        questions.push(q);
      }
      report({ kind: "questions", page, count: questions.length });
      if (stop) break;
      url = data.next;
      if (url) await sleep(delayMs);
    }

    const answers = [];
    if (includeAnswers) {
      for (let i = 0; i < questions.length; i++) {
        const q = questions[i];
        report({ kind: "answers", index: i + 1, total: questions.length });
        const aParams = new URLSearchParams({
          format: "json", question: String(q.id), ordering,
        });
        let aurl = `${apiBase}answer/?${aParams.toString()}`;
        while (aurl) {
          const data = await getJson(aurl);
          for (const a of (data.results || [])) answers.push(a);
          aurl = data.next;
          if (aurl) await sleep(delayMs);
        }
        await sleep(delayMs);
      }
    }
    return { questions, answers, includeAnswers };
  } catch (e) {
    return { error: String((e && e.message) || e) };
  }
}
