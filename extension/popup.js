// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Driver for the popup. Runs the fetch (aaqFetch, fetch-core.js) inside the real
// support.mozilla.org page — via the declared content script (messaging) with a
// scripting.executeScript fallback — so it reuses the genuine browser's Fastly
// challenge cookies and fingerprint. `api`, `API_BASE`, `SUMO_ORIGIN`,
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
  $("grant").addEventListener("click", grant);
  $("test").addEventListener("click", testAccess);
  showDiag();
}

// Diagnostic: report the active tab's URL, whether the granted permission
// actually covers it (by pattern and by exact URL), and the precise result /
// error of a trivial script injection. Surfaces the real state without devtools.
async function testAccess() {
  try {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    if (!tab) { $("diag").textContent = "no active tab"; return; }
    const url = tab.url || "(url hidden — no permission)";
    let byPattern = "?", byUrl = "?";
    try { byPattern = String(await api.permissions.contains({ origins: [SUMO_ORIGIN] })); } catch (e) { byPattern = `err:${e.message}`; }
    if (tab.url) {
      try { byUrl = String(await api.permissions.contains({ origins: [tab.url] })); } catch (e) { byUrl = `err:${e.message}`; }
    }
    // Content-script path (the one that should work on Firefox): ping it.
    let cs;
    try {
      const r = await api.tabs.sendMessage(tab.id, { type: "aaq-ping" });
      cs = r && r.href ? `OK -> ${r.href}` : `no reply (${JSON.stringify(r)})`;
    } catch (e) { cs = `ERROR -> ${(e && e.message) || e}`; }
    // Programmatic-injection path (works on Chrome; Firefox tends to refuse).
    let inj;
    try {
      const r = await api.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => location.href,
      });
      inj = `OK -> ${r && r[0] && r[0].result}`;
    } catch (e) { inj = `ERROR -> ${(e && e.message) || e}`; }
    $("diag").textContent =
      `tab.id=${tab.id}\ntab.url=${url}\ncontains(pattern)=${byPattern}\n` +
      `contains(tab.url)=${byUrl}\ncontentScript=${cs}\ninject=${inj}`;
  } catch (e) {
    $("diag").textContent = `test error: ${(e && e.message) || e}`;
  }
}

// Self-diagnostics shown in the popup (so debugging needs no devtools): which
// build is loaded, which permission key the manifest carries, and whether
// support.mozilla.org access is currently granted.
async function showDiag() {
  try {
    const m = api.runtime.getManifest();
    // Check array length, not truthiness: Firefox returns an (empty) array for
    // the unused key, which is truthy and would mislabel it.
    const hp = (m.host_permissions || []).length;
    const ohp = (m.optional_host_permissions || []).length;
    const key = hp ? "host_permissions" : (ohp ? "optional_host_permissions" : "(none)");
    let has = false;
    try { has = await api.permissions.contains({ origins: [SUMO_ORIGIN] }); } catch (e) { /* */ }
    $("diag").textContent =
      `build v${m.version} · ${key} · access granted: ${has ? "yes" : "no"}`;
  } catch (e) {
    $("diag").textContent = `diag error: ${(e && e.message) || e}`;
  }
}

// Dedicated grant button: its handler's first statement is the request, so the
// user gesture is intact and Firefox will show the permission prompt. Requires
// the origin in optional_host_permissions (manifest).
async function grant() {
  try {
    const granted = await api.permissions.request({ origins: [SUMO_ORIGIN] });
    setStatus(granted ? "Access granted." : "Access was not granted.",
      granted ? "ok" : "err");
  } catch (e) {
    setStatus(`permissions.request() failed: ${(e && e.message) || e}`, "err");
  }
  showDiag();
}

// Run aaqFetch (fetch-core.js) inside the support.mozilla.org page. Prefer the
// declared content script via messaging — the path that works on Firefox, where
// scripting.executeScript is refused even with host permission — and fall back
// to programmatic injection (works on Chrome / wherever it's allowed). Throws if
// neither can reach the tab (e.g. the tab predates the install → no content
// script yet → reload it).
async function runInPage(tabId, cfg) {
  try {
    const r = await api.tabs.sendMessage(tabId, { type: "aaq-fetch", cfg });
    if (r !== undefined) return r;      // content script handled it
  } catch (e) {
    // No content script in this tab; fall through to executeScript.
  }
  const inj = await api.scripting.executeScript({
    target: { tabId }, func: aaqFetch, args: [cfg],
  });
  return inj && inj[0] && inj[0].result;
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

    const cfg = { apiBase: API_BASE, product, gt, lt, ordering: "created",
      includeAnswers, delayMs: 2000, max429WaitS: 120, max429Retries: 3 };
    setStatus("Fetching in the page… (this can take a while with answers)");
    let result;
    try {
      result = await runInPage(tab.id, cfg);
    } catch (e) {
      setStatus("Couldn't run in the support.mozilla.org tab. Reload that tab "
        + "(the content script loads on page load), then click Fetch again. "
        + `[${(e && e.message) || e}]`, "err");
      return;
    }
    if (!result) {
      setStatus("No result from the page — reload the support.mozilla.org tab "
        + "and try again.", "err");
      return;
    }
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
