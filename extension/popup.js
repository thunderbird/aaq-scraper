// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Driver for the popup. Mirrors the fetch windows / pagination / early-stop of
// scrape_questions.py + scrape_answers.py, but runs the actual fetch inside the
// real support.mozilla.org page (see fetchInPage) so it reuses the genuine
// browser's Fastly challenge cookies and fingerprint. `api`, `API_BASE`,
// `PRODUCTS` come from common.js.

const $ = (id) => document.getElementById(id);
const statusEl = () => $("status");

function setStatus(msg, cls) {
  const el = statusEl();
  el.textContent = msg;
  el.className = cls || "";
}

function pad(n) { return String(n).padStart(2, "0"); }

// Format a Date as YYYY-MM-DDTHH:MM:SSZ (UTC), matching Python's
// strftime("%Y-%m-%dT%H:%M:%SZ").
function fmtStamp(d) {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
}

// "YYYY-MM-DD" -> [year, month, day] ints.
function ymd(s) {
  const [y, m, d] = s.split("-").map(Number);
  return [y, m, d];
}

// Parse a strict "YYYY-MM-DD" string to a UTC Date, or null if malformed /
// not a real calendar date (e.g. 2026-13-40, 2026-02-30).
function parseDay(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const [y, m, d] = ymd(s);
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 ||
      dt.getUTCDate() !== d) return null;   // rolled over -> invalid date
  return dt;
}

function today() {
  const n = new Date();
  return `${n.getUTCFullYear()}-${pad(n.getUTCMonth() + 1)}-${pad(n.getUTCDate())}`;
}

// Populate product dropdown and default both dates to today (UTC).
function init() {
  const sel = $("product");
  for (const p of PRODUCTS) {
    const o = document.createElement("option");
    o.value = p.slug;
    o.textContent = p.label;
    sel.appendChild(o);
  }
  const t = today();
  $("start").value = t;
  $("end").value = t;
  $("fetch").addEventListener("click", run);
}

// The injected function. Runs in the page's MAIN world (same origin as the
// site's own JS), so its fetch() is indistinguishable from normal site usage.
// Fully self-contained: no closures over popup scope. Returns raw, unflattened
// API objects; all CSV shaping happens later in Python.
async function fetchInPage(cfg) {
  const { apiBase, product, gt, lt, ordering, includeAnswers, delayMs } = cfg;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function getJson(url) {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    const text = await res.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { /* non-JSON */ }
    if (res.status === 429) {
      const ra = res.headers.get("retry-after");
      throw new Error("HTTP 429 rate-limited" + (ra ? ` (Retry-After: ${ra})` : ""));
    }
    if (json === null) {
      throw new Error(
        `HTTP ${res.status} non-JSON response (Fastly challenge / block?): ` +
        text.slice(0, 120));
    }
    return json;
  }

  try {
    const lessThan = new Date(lt);
    const qParams = new URLSearchParams({
      format: "json", product, created__gt: gt, created__lt: lt, ordering,
    });
    let url = `${apiBase}question/?${qParams.toString()}`;
    const questions = [];
    let stop = false;
    while (url && !stop) {
      const data = await getJson(url);
      for (const q of (data.results || [])) {
        const created = q.created ? new Date(q.created) : null;
        // ascending early-stop: once we pass the window, later rows are newer.
        if (created && created >= lessThan) { stop = true; break; }
        questions.push(q);
      }
      if (stop) break;
      url = data.next;
      if (url) await sleep(delayMs);
    }

    const answers = [];
    if (includeAnswers) {
      for (const q of questions) {
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

async function run() {
  const btn = $("fetch");
  btn.disabled = true;
  try {
    const product = $("product").value;
    const startStr = $("start").value.trim();
    const endStr = $("end").value.trim();
    const includeAnswers = $("answers").checked;

    const startDt = parseDay(startStr);
    const endDt = parseDay(endStr);
    if (!startDt || !endDt) {
      setStatus("Enter dates as YYYY-MM-DD (e.g. 2026-07-01).", "err");
      return;
    }
    if (endDt < startDt) { setStatus("End date is before start date.", "err"); return; }

    // Firefox does not grant host access to temporary add-ons at install, and
    // scripting.executeScript is refused ("Missing host permission for the tab")
    // until it is granted. permissions.request() can only grant origins declared
    // in optional_host_permissions (see manifest), so we request it here. This
    // MUST be the first await in the click handler — Firefox invalidates the
    // request once any other async call has run. Chrome prompts/grants the same.
    const origins = ["https://support.mozilla.org/*"];
    let granted = false;
    try {
      granted = await api.permissions.request({ origins });
    } catch (e) {
      setStatus(`Could not request host permission: ${(e && e.message) || e}`, "err");
      return;
    }
    if (!granted) {
      setStatus("Access to support.mozilla.org is required. Grant it when prompted " +
        "(or via about:addons → SUMO AAQ fetcher → Permissions), then retry.", "err");
      return;
    }

    // Window math mirrors scrape_questions.py: start-day 00:00:00 minus 1s;
    // end-day 00:00:00 plus 1 day (both days inclusive).
    const gt = fmtStamp(new Date(startDt.getTime() - 1000));
    const lt = fmtStamp(new Date(endDt.getTime() + 86400000));

    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url || !tab.url.startsWith("https://support.mozilla.org")) {
      setStatus("Open a support.mozilla.org tab first, then click Fetch.", "err");
      return;
    }

    setStatus("Fetching in the page… (this can take a while with answers)");
    // Inject into the default ISOLATED world (not MAIN): Firefox gates MAIN-world
    // injection behind a fully-granted host permission and rejects activeTab for
    // it ("Missing host permission for the tab"), whereas activeTab (granted on
    // the toolbar click) DOES allow ISOLATED injection. The fetch is unchanged on
    // the wire — a same-origin request from the tab still carries the browser's
    // cookies, including the httpOnly Fastly challenge cookie — so the bypass is
    // identical; it just isn't blocked.
    const injection = await api.scripting.executeScript({
      target: { tabId: tab.id },
      func: fetchInPage,
      args: [{ apiBase: API_BASE, product, gt, lt, ordering: "created", includeAnswers, delayMs: 2000 }],
    });
    const result = injection && injection[0] && injection[0].result;
    if (!result) { setStatus("No result from the page (injection failed).", "err"); return; }
    if (result.error) { setStatus(`Fetch failed: ${result.error}`, "err"); return; }

    const bundle = {
      product,
      start: ymd(startStr),
      end: ymd(endStr),
      questions: result.questions,
    };
    if (result.includeAnswers) bundle.answers = result.answers;

    const dates = startStr === endStr ? startStr : `${startStr}_${endStr}`;
    const filename = `aaq-${product}-${dates}.json`;
    const blob = new Blob([JSON.stringify(bundle)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    await api.downloads.download({ url, filename, saveAs: false });

    const nA = result.includeAnswers ? `, ${result.answers.length} answers` : "";
    setStatus(`Done: ${result.questions.length} questions${nA}\nSaved ${filename}\n` +
      `Now run:  uv run python import_json.py ${filename}`, "ok");
  } catch (e) {
    setStatus(`Error: ${(e && e.message) || e}`, "err");
  } finally {
    btn.disabled = false;
  }
}

init();
