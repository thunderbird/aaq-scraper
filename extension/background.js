// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Opt-in background KEEP-ALIVE fetch (aaq-scraper #46). On a schedule (default
// daily), fetch a trailing window of recent AAQ data — from inside a genuine,
// already-open support.mozilla.org tab, exactly like the popup's Fetch button —
// and download one raw-JSON bundle per product for import_json.py. This only
// reduces the manual friction of the extension stopgap (#29); it does NOT
// restore headless/CI scraping (the Fastly automated-browser block is why we're
// here) and it needs a real, awake browser with a SUMO tab open.
//
// CHROME-ONLY in practice: like the Fetch button, this drives a fetch inside a
// support.mozilla.org tab, which Firefox refuses for this add-on (see README).
// On Firefox it will report "needs attention" rather than fetch.
//
// The fetch itself runs aaqFetch (fetch-core.js) IN THE PAGE — via the declared
// content script (messaging) with a scripting.executeScript fallback — so it
// reuses the browser's genuine cookies + fingerprint, identical to the popup.

// Chrome loads only this file as the service worker, so pull in the shared
// globals (api, API_BASE, SUMO_ORIGIN, PRODUCTS, aaqFetch). Firefox loads
// common.js + fetch-core.js ahead of this via background.scripts, so they're
// already defined there and importScripts is skipped.
try {
  if (typeof aaqFetch === "undefined") {
    importScripts("common.js", "fetch-core.js");
  }
} catch (e) {
  // importScripts is absent in a Firefox event page; the scripts array already
  // loaded the dependencies, so there is nothing to do.
}

const ALARM = "aaq-keepalive";
const SETTINGS_KEY = "aaq-keepalive-settings";
const STATUS_KEY = "aaq-keepalive-status";
// LIVE = the in-progress phase of the CURRENT run (running/phase/product +
// the latest aaq-progress). Distinct from STATUS_KEY, which is the FINAL
// summary of the LAST completed run. The popup renders LIVE while a run is
// active (subscribing via storage.onChanged so it updates even if opened
// mid-run) and falls back to STATUS otherwise.
const LIVE_KEY = "aaq-keepalive-live";
const NOTIFY_ID = "aaq-keepalive";        // reused id → each notification replaces the last
const NOTIFY_ICON = "icons/icon-128.png";

// Off by default (opt-in). Runs once a day at a fixed time; window is 7 days.
// `dailyTimeUTC` holds the HH:MM time-of-day (the key keeps its historical name
// for storage back-compat); `dailyTimeZone` decides whether that HH:MM is read as
// UTC (default — unchanged behavior for existing installs) or the browser's local
// wall-clock time (#53).
const DEFAULTS = Object.freeze({
  enabled: false,
  dailyTimeUTC: "06:00",
  dailyTimeZone: "utc",     // "utc" | "local"
  windowDays: 7,
  includeAnswers: true,
  notify: true,             // desktop notifications on start/finish (needs the
                            // optional "notifications" permission; a no-op until granted)
});

async function getSettings() {
  const stored = (await api.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] || {};
  return { ...DEFAULTS, ...stored };
}
async function saveSettings(patch) {
  const next = { ...(await getSettings()), ...patch };
  await api.storage.local.set({ [SETTINGS_KEY]: next });
  return next;
}
async function getStatus() {
  return (await api.storage.local.get(STATUS_KEY))[STATUS_KEY] || null;
}
async function setStatus(status) {
  await api.storage.local.set({ [STATUS_KEY]: status });
}
async function getLive() {
  return (await api.storage.local.get(LIVE_KEY))[LIVE_KEY] || null;
}
async function setLive(live) {
  await api.storage.local.set({ [LIVE_KEY]: live });
}

// Toolbar-icon badge: an ambient indicator visible without opening the popup.
// "…" while running, "✓" ok, "!" needs-attention/error. `api.action` is absent
// on old Chrome / some Firefox builds, so guard it. Best-effort — never throws
// into the job.
function setBadge(text, color) {
  try {
    if (!api.action || !api.action.setBadgeText) return;
    api.action.setBadgeText({ text: text || "" });
    if (color && api.action.setBadgeBackgroundColor) {
      api.action.setBadgeBackgroundColor({ color });
    }
  } catch (e) { /* best-effort */ }
}

// Desktop notification (start/finish). `notifications` is an OPTIONAL permission
// requested from the popup; until granted, api.notifications is undefined and
// this no-ops. Reuses one id so a later notification replaces the earlier one
// rather than stacking. Gated by the user's `notify` setting by the caller.
function notify(title, message) {
  try {
    if (!api.notifications || !api.notifications.create) return;
    api.notifications.create(NOTIFY_ID, {
      type: "basic", iconUrl: NOTIFY_ICON, title, message,
    });
  } catch (e) { /* best-effort */ }
}

const pad = (n) => String(n).padStart(2, "0");
const dayStr = (d) =>
  `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
// YYYY-MM-DDTHH:MM:SSZ (UTC), matching Python's strftime("%Y-%m-%dT%H:%M:%SZ").
const fmtStamp = (d) =>
  `${dayStr(d)}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
const ymd = (s) => s.split("-").map(Number);

// Trailing window of `windowDays` COMPLETED UTC days, ending YESTERDAY — today
// is still accumulating (a partial day would download incomplete). The daily
// cadence + multi-day window overlap means yesterday's day is picked up the next
// run, and any run that fails/aborts is re-covered by the next run's overlapping
// window (so there are no silent gaps — see #46).
function trailingWindow(windowDays) {
  const now = new Date();
  const todayUTC = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const endDt = new Date(todayUTC.getTime() - 86400000);                 // yesterday 00:00:00Z
  const startDt = new Date(endDt.getTime() - (windowDays - 1) * 86400000);
  return {
    startStr: dayStr(startDt),
    endStr: dayStr(endDt),
    // Window math mirrors scrape_questions.py: start 00:00:00 -1s; end +1 day.
    gt: fmtStamp(new Date(startDt.getTime() - 1000)),
    lt: fmtStamp(new Date(endDt.getTime() + 86400000)),
  };
}

// A Fastly-challenge / block response surfaces from aaqFetch as a non-JSON body.
const looksLikeChallenge = (err) =>
  /non-json|challenge|block/i.test(String(err || ""));

// Run aaqFetch (fetch-core.js) in the given SUMO tab. Prefer the declared content
// script via messaging (the path that works on Firefox — though Firefox blocks
// the whole SUMO injection anyway), fall back to programmatic injection (Chrome).
async function runInTab(tabId, cfg) {
  try {
    const r = await api.tabs.sendMessage(tabId, { type: "aaq-fetch", cfg });
    if (r !== undefined) return r;
  } catch (e) {
    // No content script in this tab (loaded before install) — fall through.
  }
  const inj = await api.scripting.executeScript({
    target: { tabId }, func: aaqFetch, args: [cfg],
  });
  return inj && inj[0] && inj[0].result;
}

// downloads.download needs a URL. A service worker has no URL.createObjectURL,
// so encode the bundle as a base64 data: URL (UTF-8 safe).
function bundleDataUrl(bundle) {
  const bytes = new TextEncoder().encode(JSON.stringify(bundle));
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return `data:application/json;base64,${btoa(bin)}`;
}

async function fetchProduct(tabId, product, win, includeAnswers) {
  const cfg = {
    apiBase: API_BASE, product, gt: win.gt, lt: win.lt, ordering: "created",
    includeAnswers, delayMs: 2000, max429WaitS: 120, max429Retries: 3,
  };
  const result = await runInTab(tabId, cfg);
  if (!result) return { product, ok: false, error: "no result from page" };
  if (result.error) return { product, ok: false, error: result.error };

  const bundle = {
    product, start: ymd(win.startStr), end: ymd(win.endStr),
    questions: result.questions,
  };
  if (result.includeAnswers) bundle.answers = result.answers;
  const dates = win.startStr === win.endStr ? win.startStr : `${win.startStr}_${win.endStr}`;
  const filename = `aaq-${product}-${dates}.json`;
  await api.downloads.download({ url: bundleDataUrl(bundle), filename, saveAs: false });
  return {
    product, ok: true, filename,
    questions: result.questions.length,
    answers: result.includeAnswers ? result.answers.length : null,
  };
}

// Re-entrancy guard. Two triggers can arrive close together — the daily alarm
// plus a manual "Run now" — and without coalescing them each launches a full
// concurrent job, so every product's bundle downloads 2-3× (Chrome auto-suffixes
// the fixed filename `(1)`, `(2)`; issue #51). `jobInFlight` is set synchronously
// before any await, so a second trigger while a run is active returns the SAME
// in-flight promise instead of starting a parallel run. A storage-based lease was
// considered for the cross-worker-restart case but dropped: nothing re-invokes
// runJob on a bare worker restart (the daily alarm can't re-fire same-day; a
// manual click is a fresh user action), and a lease that isn't cleared by a
// crashed run would silently no-op every later run until it expired.
let jobInFlight = null;
// The current run's live object (null between runs). The aaq-progress listener
// mutates `liveState` in place and persists it (throttled), so the popup's
// storage.onChanged subscription shows page/answer counts as they arrive. Runs
// are serialized by `jobInFlight` and this is nulled at run end, so the
// listener's `if (liveState)` guard alone prevents cross-run contamination.
let liveState = null;
let lastProgressWriteMs = 0;
async function runJob(trigger) {
  if (jobInFlight) return jobInFlight;
  jobInFlight = runJobInner(trigger);
  try {
    return await jobInFlight;
  } finally {
    jobInFlight = null;
  }
}

// The scheduled job (also invoked by the popup's "Run now").
async function runJobInner(trigger) {
  const s = await getSettings();
  const notifyOn = s.notify !== false;
  const startedAt = new Date().toISOString();
  const win = trailingWindow(s.windowDays);
  const winStr = `${win.startStr}..${win.endStr}`;

  // Mark the run live and light the badge so the phase is visible without the
  // popup open.
  const live = {
    running: true, trigger, at: startedAt, window: winStr,
    phase: "Starting…", product: null, progress: null,
  };
  liveState = live;
  await setLive(live);
  setBadge("…", "#1a73e8");
  if (trigger !== "manual" && notifyOn) {
    notify("SUMO AAQ fetcher", trigger === "catchup"
      ? `Catching up a missed run — fetching ${winStr}…`
      : `Alarm fired — fetching ${winStr}…`);
  }

  const finishLive = () => { liveState = null; return setLive({ ...live, running: false }); };

  // Find an already-open SUMO tab to fetch from (host permission lets us read
  // and filter these tabs' URLs without the broad "tabs" permission).
  const tabs = await api.tabs.query({ url: SUMO_ORIGIN });
  if (!tabs.length) {
    await finishLive();
    setBadge("!", "#b60205");
    if (notifyOn) {
      notify("SUMO AAQ fetcher — needs attention",
        "No support.mozilla.org tab open. Keep one open and browse it once, then "
        + "it retries next run.");
    }
    await setStatus({
      at: startedAt, trigger, outcome: "needs-attention", window: winStr,
      message: "No support.mozilla.org tab open. Keep one open (and browse it "
        + "once so the challenge clears); the next run will fetch.",
      products: [],
    });
    return;
  }

  const products = [];
  let needsAttention = false, anyError = false;
  for (const p of PRODUCTS) {
    live.phase = `Fetching ${p.label}…`;
    live.product = p.slug;
    live.progress = null;
    await setLive(live);
    let r;
    try {
      r = await fetchProduct(tabs[0].id, p.slug, win, s.includeAnswers);
    } catch (e) {
      r = { product: p.slug, ok: false, error: String((e && e.message) || e) };
    }
    if (!r.ok) {
      anyError = true;
      if (looksLikeChallenge(r.error)) needsAttention = true;
    }
    products.push(r);
  }

  const outcome = needsAttention ? "needs-attention" : (anyError ? "error" : "ok");
  const message = needsAttention
    ? "Fastly challenge/block hit — open a support.mozilla.org tab and browse it "
      + "normally, then it retries on the next run (nothing is lost; the window "
      + "overlaps)."
    : anyError
      ? "One or more products failed; the next run's overlapping window retries them."
      : "Downloaded. Import each bundle:  uv run python import_json.py ~/Downloads/<file>";
  await finishLive();
  setBadge(outcome === "ok" ? "✓" : "!", outcome === "ok" ? "#0e8a16" : "#b60205");
  if (notifyOn) {
    const ok = products.filter((p) => p.ok);
    const summary = ok.map((p) => `${p.questions} q${p.answers != null ? `/${p.answers} a` : ""}`).join(", ");
    notify(
      outcome === "ok" ? "SUMO AAQ fetcher — done"
        : outcome === "needs-attention" ? "SUMO AAQ fetcher — needs attention"
          : "SUMO AAQ fetcher — error",
      outcome === "ok" ? `Fetched ${summary || "0"}. Import the bundle(s) with import_json.py.` : message,
    );
  }
  await setStatus({
    at: startedAt, trigger, outcome, window: winStr, message, products,
  });
}

// Epoch ms of the next occurrence of "HH:MM" in the given zone ("utc" | "local")
// — today if it's still ahead, otherwise the next calendar day. Falls back to
// 06:00 on a malformed value.
//
// Local mode advances by a CALENDAR day (Date(y, m, d+1, h, min)), not a fixed
// +24h, so the fire stays pinned to the same local wall-clock time across DST
// transitions; because the alarm is re-armed after every fire (see onAlarm) this
// recomputes daily, so a DST shift self-corrects on the next day rather than
// drifting. UTC has no DST, so its next-day is a plain +24h.
function nextDailyMs(hhmm, zone) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  let h = 6, min = 0;
  if (m) {
    const hh = parseInt(m[1], 10), mm = parseInt(m[2], 10);
    if (hh <= 23 && mm <= 59) { h = hh; min = mm; }
  }
  const now = new Date();
  if (zone === "local") {
    let next = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, min, 0, 0).getTime();
    if (next <= now.getTime()) {
      next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, h, min, 0, 0).getTime();
    }
    return next;
  }
  let next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, min, 0, 0);
  if (next <= now.getTime()) next += 86400000;   // already passed today → tomorrow
  return next;
}

// Epoch ms of the MOST RECENT occurrence of "HH:MM" at or before now, in the
// given zone — the mirror of nextDailyMs, used by the catch-up check to decide
// whether a scheduled run was missed. Same calendar-day arithmetic so it's
// DST-correct in local mode. Falls back to 06:00 on a malformed value.
function prevDailyMs(hhmm, zone) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ""));
  let h = 6, min = 0;
  if (m) {
    const hh = parseInt(m[1], 10), mm = parseInt(m[2], 10);
    if (hh <= 23 && mm <= 59) { h = hh; min = mm; }
  }
  const now = new Date();
  if (zone === "local") {
    let prev = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, min, 0, 0).getTime();
    if (prev > now.getTime()) {
      prev = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, h, min, 0, 0).getTime();
    }
    return prev;
  }
  let prev = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h, min, 0, 0);
  if (prev > now.getTime()) prev -= 86400000;    // not yet today → yesterday's
  return prev;
}

// Catch up a run the one-shot alarm dropped. Chrome does not deliver an alarm
// whose time passed while the machine was asleep / the browser was closed; worse,
// our onStartup reconcile re-arms it to the NEXT occurrence, cancelling Chrome's
// own would-be catch-up. So on every worker (re)start, if auto-fetch is enabled
// and the last recorded run predates the most recent scheduled occurrence, run
// once now. Guards: needs `alarms` (else nothing is scheduled); skips when a run
// is already in flight; and requires a PRIOR run to exist, so first-enable waits
// for the real alarm instead of firing immediately. runJob's jobInFlight guard
// coalesces this with any genuine alarm Chrome does deliver at startup (no double
// fetch); the overlapping trailing window means an at-most-once catch-up loses
// no data even if it lands on a no-tab / challenge miss.
async function maybeCatchUp() {
  if (!api.alarms || jobInFlight) return;
  const s = await getSettings();
  if (!s.enabled) return;
  const st = await getStatus();
  const lastRunMs = st && st.at ? Date.parse(st.at) : NaN;
  if (!Number.isFinite(lastRunMs)) return;       // never ran → let the alarm handle first run
  if (lastRunMs < prevDailyMs(s.dailyTimeUTC, s.dailyTimeZone)) {
    runJob("catchup");                           // fire-and-forget; don't block startup
  }
}

// Create/clear the daily alarm to match the current settings. `alarms` is an
// OPTIONAL permission (#47) requested when the user enables auto-fetch: if it
// isn't granted, api.alarms is absent and there is simply nothing to schedule.
// `when` anchors the fire to the next occurrence of the chosen time in the chosen
// zone. It's a ONE-SHOT alarm (no `periodInMinutes`): onAlarm re-arms the next
// occurrence after each fire, so a local-time schedule recomputes its wall-clock
// instant every day and stays correct across DST (a fixed 1440-min repeat would
// drift by an hour). Recreating with the same settings is idempotent — `create`
// replaces the existing alarm and `when` recomputes to the same next occurrence.
async function reconcileAlarm() {
  if (!api.alarms) return;
  const s = await getSettings();
  if (s.enabled) {
    api.alarms.create(ALARM, { when: nextDailyMs(s.dailyTimeUTC, s.dailyTimeZone) });
  } else {
    await api.alarms.clear(ALARM);
  }
}

// On worker (re)start, heal any state a mid-run eviction left behind: a stale
// LIVE record still flagged `running:true` would otherwise wedge the popup on
// "🔄 Running" forever, since the run that would have cleared it is gone. Guard
// on `!jobInFlight` so we never clobber a run that started this same tick (e.g.
// an alarm that fired at startup). Also reflect the last outcome on the badge,
// which resets to empty across a browser restart.
async function initStatus() {
  const live = await getLive();
  if (live && live.running && !jobInFlight) {
    await setLive({ ...live, running: false });
  }
  const st = await getStatus();
  if (st && !jobInFlight) {
    setBadge(st.outcome === "ok" ? "✓" : "!", st.outcome === "ok" ? "#0e8a16" : "#b60205");
  }
}

// Register the onAlarm listener (idempotent — Chrome dedups identical refs) and
// reconcile. Called at startup when `alarms` is already held, and again from
// permissions.onAdded when the user grants it at enable-time in an already-running
// worker (where api.alarms didn't exist at top-level evaluation).
function initAlarms() {
  if (!api.alarms) return;
  api.alarms.onAlarm.addListener(onAlarm);
  reconcileAlarm();
}
function onAlarm(a) {
  if (a.name !== ALARM) return;
  // Re-arm the NEXT occurrence first (the alarm is one-shot), before running —
  // so a failing job can never break the schedule, and local-time runs recompute
  // their wall-clock instant daily (DST-correct). At fire time `now` ~= the
  // scheduled instant, so nextDailyMs returns tomorrow's occurrence.
  reconcileAlarm();
  runJob("alarm");
}

// Worker startup: arm the alarm, heal stale live/badge state, then catch up a
// run the one-shot alarm dropped while asleep/closed. This runs on browser
// start (onStartup), install/update (onInstalled), and every bare worker
// re-evaluation (the top-level call) — so a machine that wakes after sleeping
// through the scheduled time gets its missed run on the next worker tick.
async function bootstrap() {
  initAlarms();
  await initStatus();
  await maybeCatchUp();
}
api.runtime.onInstalled.addListener(bootstrap);
api.runtime.onStartup.addListener(bootstrap);
// Grant/revoke of the optional `alarms` permission at runtime.
api.permissions.onAdded.addListener((p) => {
  if (p && (p.permissions || []).includes("alarms")) initAlarms();
});
api.permissions.onRemoved.addListener((p) => {
  if (p && (p.permissions || []).includes("alarms")) reconcileAlarm();
});
// Cover the case where the worker (re)starts with the permission already held
// but neither onInstalled nor onStartup fires (e.g. wake from an event).
bootstrap();

// Popup ↔ worker control channel. (aaq-fetch/aaq-ping are handled by the content
// script via tabs.sendMessage and never reach here.) aaq-progress from the page
// (fetch-core.js) DOES reach here during a background run — we fold it into the
// live status so the popup can show page/answer counts as they arrive.
api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string") return undefined;
  if (msg.type === "aaq-progress") {
    // Only meaningful during our own run; ignore otherwise. Throttle storage
    // writes (progress can fire per page / per answer) but always let the phase
    // KIND change (questions→answers) or a rate-limit event through immediately.
    if (liveState) {
      const now = Date.now();
      const kindChanged = !liveState.progress || liveState.progress.kind !== msg.kind;
      const isRatelimit = msg.kind === "ratelimit";
      if (kindChanged || isRatelimit || now - lastProgressWriteMs >= 400) {
        lastProgressWriteMs = now;
        const { type, ...progress } = msg;
        liveState.progress = progress;
        setLive(liveState);
      }
    }
    return undefined;   // no response expected
  }
  if (msg.type === "aaq-keepalive-get") {
    Promise.all([getSettings(), getStatus(), getLive()])
      .then(([settings, status, live]) => sendResponse({ settings, status, live }));
    return true;
  }
  if (msg.type === "aaq-keepalive-apply") {
    saveSettings(msg.settings || {})
      .then(() => initAlarms())   // register onAlarm + reconcile (idempotent)
      .then(getSettings)
      .then((settings) => sendResponse({ settings }));
    return true;
  }
  if (msg.type === "aaq-keepalive-run-now") {
    // Always send a response (even on error) so the popup re-enables its button;
    // a hung sendResponse would leave "Run background fetch now" grayed out.
    runJob("manual")
      .catch((e) => setStatus({
        at: new Date().toISOString(), trigger: "manual", outcome: "error",
        message: `run failed: ${(e && e.message) || e}`, products: [],
      }))
      .then(getStatus)
      .then((status) => sendResponse({ status }))
      .catch(() => sendResponse({ status: null }));
    return true;
  }
  return undefined;
});
