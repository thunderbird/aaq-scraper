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

// Storage keys + alarm name shared with background.js (for the live-status
// subscription and the next-run countdown).
const LIVE_KEY = "aaq-keepalive-live";
const STATUS_KEY = "aaq-keepalive-status";
const ALARM = "aaq-keepalive";

function setStatus(msg, cls) {
  const el = statusEl();
  el.textContent = msg;
  el.className = cls || "";
}

function pad(n) { return String(n).padStart(2, "0"); }

// Normalize a "HH:MM" 24-hour UTC time string, falling back to "06:00" if it's
// malformed or out of range. Returns zero-padded "HH:MM".
function normalizeTime(s) {
  const m = /^(\d{1,2}):(\d{2})$/.exec((s || "").trim());
  if (!m) return "06:00";
  const h = parseInt(m[1], 10), min = parseInt(m[2], 10);
  if (h > 23 || min > 59) return "06:00";
  return `${pad(h)}:${pad(min)}`;
}

// Read the selected schedule zone, defaulting to "utc" for anything unexpected.
function scheduleZone() {
  return $("ka-zone").value === "local" ? "local" : "utc";
}

// Show the scheduled time alongside its equivalent in the other zone, so "06:00
// local" vs "06:00 UTC" is never ambiguous. Computed for today's date (the
// UTC⇄local offset can shift by an hour across a DST boundary).
function updateTimeHint() {
  const hhmm = normalizeTime($("ka-time").value);
  const [h, min] = hhmm.split(":").map(Number);
  const now = new Date();
  let hint;
  if (scheduleZone() === "local") {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, min);
    hint = `Runs ${hhmm} local (= ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC).`;
  } else {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, min));
    hint = `Runs ${hhmm} UTC (≈ ${pad(d.getHours())}:${pad(d.getMinutes())} your time).`;
  }
  $("ka-time-hint").textContent = hint;
}

// Render an aaq-progress event (emitted by aaqFetch in the page, fetch-core.js)
// into a human status line.
function fmtProgress(p) {
  switch (p.kind) {
    case "questions":
      return `Fetching questions… page ${p.page} (${p.count} so far)`;
    case "answers":
      return `Fetching answers… question ${p.index}/${p.total}`;
    case "ratelimit":
      return `Rate limited (HTTP 429) — waiting ${p.waitS}s, retry `
        + `${p.attempt}/${p.retries}…`;
    default:
      return "Fetching in the page…";
  }
}

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
  $("ka-enabled").addEventListener("change", onToggleKeepalive);
  $("ka-answers").addEventListener("change", applyKeepalive);
  $("ka-notify").addEventListener("change", onToggleNotify);
  $("ka-time").addEventListener("change", applyKeepalive);
  $("ka-time").addEventListener("input", updateTimeHint);   // live hint as you type
  $("ka-zone").addEventListener("change", () => { updateTimeHint(); applyKeepalive(); });
  $("ka-window").addEventListener("change", applyKeepalive);
  $("ka-run").addEventListener("click", runKeepaliveNow);
  showDiag();
  loadKeepalive();

  // Live status: background.js writes phase/progress to storage as a run
  // proceeds, so re-render the status line on change — this updates the popup
  // even if it was opened mid-run. Only the status line is touched (not the
  // form fields), so it can't clobber what the user is editing.
  api.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && (changes[LIVE_KEY] || changes[STATUS_KEY])) refreshStatusLine();
  });
  // "Next run in Xh Ym" countdown while idle. Refresh a few times a minute so it
  // stays roughly current; the interval dies with the popup.
  updateNextRun();
  setInterval(updateNextRun, 5000);
}

// Human-readable one-liner for a background keep-alive status record (set by
// background.js). `null` = never run.
function fmtKeepaliveStatus(st) {
  if (!st) return "Background fetch has not run yet.";
  const when = st.at ? new Date(st.at).toLocaleString() : "?";
  const tag = { ok: "OK", "needs-attention": "Needs attention", error: "Error" }[st.outcome] || st.outcome;
  const lines = [`Last run (${when}) — ${tag}. Window ${st.window || "?"}.`];
  for (const p of (st.products || [])) {
    lines.push(p.ok
      ? `  ${p.product}: ${p.questions} q${p.answers != null ? `, ${p.answers} a` : ""} → ${p.filename}`
      : `  ${p.product}: FAILED — ${p.error}`);
  }
  if (st.message) lines.push(st.message);
  return lines.join("\n");
}

// Human one-liner for the LIVE (in-progress) state background.js writes while a
// run is active. Returns null when nothing is running (caller falls back to the
// last-run summary). `fmtProgress` renders the page/answer/429 detail.
function fmtLive(live) {
  if (!live || !live.running) return null;
  const label = live.trigger === "alarm" ? "alarm"
    : live.trigger === "catchup" ? "catch-up" : "manual";
  const lines = [`🔄 Running (${label}) — window ${live.window || "?"}`];
  if (live.phase) lines.push(`  ${live.phase}`);
  if (live.progress) lines.push(`  ${fmtProgress(live.progress)}`);
  return lines.join("\n");
}

function renderKeepalive(res) {
  const s = (res && res.settings) || {};
  $("ka-enabled").checked = !!s.enabled;
  $("ka-answers").checked = s.includeAnswers !== false;
  $("ka-notify").checked = s.notify !== false;
  $("ka-time").value = normalizeTime(s.dailyTimeUTC);
  $("ka-zone").value = s.dailyTimeZone === "local" ? "local" : "utc";
  $("ka-window").value = String(s.windowDays ?? 7);
  updateTimeHint();
  $("ka-status").textContent = fmtLive(res && res.live) || fmtKeepaliveStatus(res && res.status);
}

// Update ONLY the status line from storage (used by the storage.onChanged
// subscription) — no form-field writes, so it never clobbers user edits.
async function refreshStatusLine() {
  try {
    const got = await api.storage.local.get([LIVE_KEY, STATUS_KEY]);
    $("ka-status").textContent =
      fmtLive(got[LIVE_KEY]) || fmtKeepaliveStatus(got[STATUS_KEY] || null);
  } catch (e) { /* ignore transient storage errors */ }
}

// "Waiting for alarm — next run in Xh Ym (at HH:MM)". Needs the `alarms`
// permission (held whenever auto-fetch is enabled); guarded so it silently
// clears when off / not granted.
async function updateNextRun() {
  const el = $("ka-next");
  if (!el) return;
  try {
    if (!$("ka-enabled").checked || !api.alarms) { el.textContent = ""; return; }
    const a = await api.alarms.get(ALARM);
    if (!a) { el.textContent = "No alarm scheduled yet."; return; }
    const ms = a.scheduledTime - Date.now();
    if (ms <= 0) { el.textContent = "⏰ Next run: due now…"; return; }
    const totalMin = Math.floor(ms / 60000);
    const h = Math.floor(totalMin / 60), m = totalMin % 60;
    const rel = h > 0 ? `${h}h ${m}m` : `${m}m`;
    const when = new Date(a.scheduledTime)
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    el.textContent = `⏰ Waiting for alarm — next run in ${rel} (at ${when}).`;
  } catch (e) { el.textContent = ""; }
}

async function loadKeepalive() {
  try {
    renderKeepalive(await api.runtime.sendMessage({ type: "aaq-keepalive-get" }));
    // Reflect the ACTUAL notification permission: the setting may say "on" but
    // the optional permission can be absent (e.g. removed in about:addons).
    try {
      const has = api.permissions
        && await api.permissions.contains({ permissions: ["notifications"] });
      if (!has) $("ka-notify").checked = false;
    } catch (e) { /* permissions API quirk — leave the setting value */ }
    updateNextRun();
  } catch (e) {
    $("ka-status").textContent = `background unavailable: ${(e && e.message) || e}`;
  }
}

// Read the keep-alive controls into a settings patch, normalizing the time and
// clamping the window.
function readKeepaliveSettings() {
  const dailyTimeUTC = normalizeTime($("ka-time").value);
  const windowDays = Math.max(1, parseInt($("ka-window").value, 10) || 7);
  return {
    enabled: $("ka-enabled").checked,
    includeAnswers: $("ka-answers").checked,
    notify: $("ka-notify").checked,
    dailyTimeUTC,
    dailyTimeZone: scheduleZone(),
    windowDays,
  };
}

// Desktop-notifications toggle. `notifications` is an OPTIONAL permission —
// request it as the FIRST statement when checking on (Firefox invalidates the
// user gesture after any other await), remove it when unchecking. Mirrors
// onToggleKeepalive's alarms handling.
async function onToggleNotify() {
  const cb = $("ka-notify");
  if (cb.checked) {
    let granted = false;
    try {
      granted = await api.permissions.request({ permissions: ["notifications"] });
    } catch (e) {
      granted = false;
    }
    if (!granted) {
      cb.checked = false;
      $("ka-status").textContent = "Notifications need the \"notifications\" "
        + "permission — not granted. Status still shows here and on the icon.";
      return;
    }
    await applyKeepalive();
  } else {
    await applyKeepalive();               // persist notify:false first
    try { await api.permissions.remove({ permissions: ["notifications"] }); } catch (e) { /* */ }
  }
}

// Enable/disable toggle. `alarms` is an optional permission (#47): request it as
// the FIRST statement here (Firefox invalidates the user gesture after any other
// await) when enabling, and remove it when disabling.
async function onToggleKeepalive() {
  const cb = $("ka-enabled");
  if (cb.checked) {
    let granted = false;
    try {
      granted = await api.permissions.request({ permissions: ["alarms"] });
    } catch (e) {
      granted = false;
    }
    if (!granted) {
      cb.checked = false;
      $("ka-status").textContent = "Scheduling needs the \"alarms\" permission — "
        + "not granted. Auto-fetch stays off.";
      return;
    }
    await applyKeepalive();
  } else {
    await applyKeepalive();               // persist enabled:false first
    try { await api.permissions.remove({ permissions: ["alarms"] }); } catch (e) { /* */ }
  }
}

async function applyKeepalive() {
  try {
    const res = await api.runtime.sendMessage({
      type: "aaq-keepalive-apply", settings: readKeepaliveSettings(),
    });
    // Reflect the clamped/stored values back into the fields.
    if (res && res.settings) {
      $("ka-time").value = normalizeTime(res.settings.dailyTimeUTC);
      $("ka-zone").value = res.settings.dailyTimeZone === "local" ? "local" : "utc";
      $("ka-window").value = String(res.settings.windowDays);
      updateTimeHint();
    }
  } catch (e) {
    $("ka-status").textContent = `could not save: ${(e && e.message) || e}`;
  }
}

async function runKeepaliveNow() {
  const btn = $("ka-run");
  btn.disabled = true;
  $("ka-status").textContent = "Running background fetch now…";
  try {
    await applyKeepalive();   // persist current control values first
    const res = await api.runtime.sendMessage({ type: "aaq-keepalive-run-now" });
    $("ka-status").textContent = fmtKeepaliveStatus(res && res.status);
  } catch (e) {
    $("ka-status").textContent = `run failed: ${(e && e.message) || e}`;
  } finally {
    btn.disabled = false;
  }
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
    // Live progress: aaqFetch (in the page) emits aaq-progress via
    // runtime.sendMessage as it pages questions/answers and honors 429s. Listen
    // while the fetch runs, then detach — this is UI-only, the result still
    // arrives via runInPage's return value.
    const onProgress = (msg) => {
      if (msg && msg.type === "aaq-progress") setStatus(fmtProgress(msg));
    };
    api.runtime.onMessage.addListener(onProgress);
    let result;
    try {
      result = await runInPage(tab.id, cfg);
    } catch (e) {
      setStatus("Couldn't run in the support.mozilla.org tab. Reload that tab "
        + "(the content script loads on page load), then click Fetch again. "
        + `[${(e && e.message) || e}]`, "err");
      return;
    } finally {
      api.runtime.onMessage.removeListener(onProgress);
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
