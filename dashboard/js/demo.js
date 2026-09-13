"use strict";

/* SignalGuard — demo control panel.
 *
 * Deliberately NOT a scenario runner. `scripts/run_demo.py` owns the
 * scenarios: it holds the beat sequences, the narration, and the assertions
 * that make a run a pass or a fail, and its contract is frozen. A second
 * implementation of `scenario_overdue` living in JavaScript would be two
 * sources of truth for the one thing that must not break on recording day —
 * the terminal run would keep passing while this one quietly drifted, and
 * nobody would find out until the demo.
 *
 * So this page exposes the *primitives* instead. Every button below is one
 * call to one endpoint the conductor already uses. Stepping through a
 * scenario means pressing them in order; you are the stepper. Nothing here
 * decides what happens next — the state machine and the agent do, exactly as
 * they do for run_demo.py.
 *
 * Rule 1 from app.js applies here too: nothing from the backend is ever put
 * into innerHTML. Same reason (a traveller name is attacker-controlled), and
 * this page renders the same names.
 */

const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 2000;

// The conductor's own demo identity (run_demo.py Config). Matching it means
// a trip started here looks like a trip started there on the dashboard.
const DEMO_TRAVELLER = {
  msisdn: "+962790000001",
  name: "Khalid",
  contacts: [{ name: "Omar", msisdn: "+962790000002" }],
};

const state = {
  backendUrl: localStorage.getItem("sg_backend_url") || DEFAULT_BACKEND,
  ready: false,
  zones: [],
  travellerId: "",
  authToken: "",
  zoneId: "",
  tripId: "",
  trip: null,
  busy: false,
  lastPollAt: null,
  lastPollOk: true,
};

// Session identity survives a reload — F5 in the middle of a run should not
// orphan the traveller whose trip is on the dashboard behind you.
try {
  const saved = JSON.parse(localStorage.getItem("sg_demo_session") || "null");
  if (saved && typeof saved === "object") {
    state.travellerId = saved.travellerId || "";
    state.authToken = saved.authToken || "";
    state.zoneId = saved.zoneId || "";
    state.tripId = saved.tripId || "";
  }
} catch (e) {
  /* corrupt entry is not worth failing boot over */
}

function saveSession() {
  localStorage.setItem("sg_demo_session", JSON.stringify({
    travellerId: state.travellerId,
    authToken: state.authToken,
    zoneId: state.zoneId,
    tripId: state.tripId,
  }));
}

// -- DOM ---------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setText(node, value) {
  if (node) node.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
}

const ui = {
  backendInput: $("backend-url"),
  unavailable: $("demo-unavailable"),
  main: $("demo-main"),
  zone: $("f-zone"),
  battery: $("f-battery"),
  model: $("f-model"),
  minutes: $("f-minutes"),
  advanceHint: $("advance-hint"),
  log: $("demo-log"),
  apiLog: $("api-log"),
  announcer: $("announcer"),
  lastUpdated: $("last-updated"),
  liveIndicator: $("live-indicator"),
  s: {
    traveller: $("s-traveller"),
    trip: $("s-trip"),
    stateName: $("s-state"),
    risk: $("s-risk"),
    predicted: $("s-predicted"),
    window: $("s-window"),
    deadline: $("s-deadline"),
    now: $("s-now"),
    notifications: $("s-notifications"),
  },
};

// -- fetch -------------------------------------------------------------------

async function api(path, opts) {
  const resp = await fetch(state.backendUrl + path, opts);
  const text = await resp.text();
  if (!resp.ok) {
    // FastAPI puts the useful part in {"detail": ...}; surfacing it beats
    // "500" when a button fails mid-demo.
    let detail = "";
    try {
      detail = JSON.parse(text).detail || "";
    } catch (e) { /* not JSON */ }
    const err = new Error(`${resp.status}${detail ? " " + detail : ""}`);
    err.status = resp.status;
    throw err;
  }
  return text ? JSON.parse(text) : null;
}

function post(path, body, extraHeaders) {
  return api(path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": "application/json" }, extraHeaders || {}),
    body: JSON.stringify(body || {}),
  });
}

// -- log ---------------------------------------------------------------------

function stamp() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

function logLine(kind, label, detail) {
  const row = el("div", `demo-log-row ${kind}`);
  row.appendChild(el("span", "demo-log-time", stamp()));
  row.appendChild(el("span", "demo-log-label", label));
  if (detail) row.appendChild(el("span", "demo-log-detail", detail));
  ui.log.insertBefore(row, ui.log.firstChild);
  while (ui.log.childNodes.length > 60) ui.log.removeChild(ui.log.lastChild);
}

function announce(message) {
  if (ui.announcer) ui.announcer.textContent = message;
}

// -- action wrapper ----------------------------------------------------------

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll(".demo-btn").forEach((b) => {
    b.disabled = busy || !state.ready;
  });
}

/** Run one control action. Buttons lock while it is in flight — a demo
 *  operator double-tapping "Enter gate" should not open two trips. */
async function run(label, fn) {
  if (state.busy || !state.ready) return;
  setBusy(true);
  try {
    const detail = await fn();
    logLine("ok", label, detail || "ok");
    announce(`${label} — ok`);
  } catch (err) {
    logLine("fail", label, String(err.message || err));
    announce(`${label} failed`);
  } finally {
    setBusy(false);
    refresh();
  }
}

function requireTraveller() {
  if (!state.travellerId) throw new Error("no traveller — press Reset & arm first");
  return state.travellerId;
}

function requireTrip() {
  if (!state.tripId) throw new Error("no active trip — cross the entry gate first");
  return state.tripId;
}

// -- actions -----------------------------------------------------------------

async function armEverything() {
  const zoneId = ui.zone.value;
  const battery = Number(ui.battery.value) || 82;
  const modelEnabled = ui.model.checked;

  await post("/demo/reset");
  await post("/demo/agent/model", { enabled: modelEnabled });
  const t = await post("/demo/travellers", DEMO_TRAVELLER);
  state.travellerId = t.traveller_id;
  state.authToken = t.auth_token || "";
  state.zoneId = zoneId;
  state.tripId = "";
  state.trip = null;
  const subs = await post(`/demo/zones/${encodeURIComponent(zoneId)}/arm`, {
    traveller_id: state.travellerId,
  });
  await post("/demo/battery", { traveller_id: state.travellerId, level: battery });
  saveSession();
  return `${zoneId} · gates ${subs.entry_subscription_id} / ${subs.exit_subscription_id} · battery ${battery}% · gemini ${modelEnabled ? "on" : "OFF"}`;
}

async function gate(which) {
  await post("/demo/simulate-gate-event", {
    traveller_id: requireTraveller(),
    zone_id: state.zoneId || ui.zone.value,
    gate: which,
  });
  return `synthetic ${which} event injected`;
}

async function reachability(reachable) {
  await post("/demo/simulate-reachability", {
    traveller_id: requireTraveller(),
    reachable,
  });
  return reachable ? "device reachable" : "device dark";
}

async function advance(minutes) {
  const mins = Math.max(1, Math.min(600, Math.round(minutes)));
  const res = await post("/demo/clock/advance", { seconds: mins * 60 });
  return `+${mins} min → ${(res.now || "").replace("T", " ").slice(0, 19)}`;
}

/** Advance just past the window the agent actually set for THIS trip.
 *  Still one clock call — the number comes from the trip, not from a
 *  hardcoded scenario. */
function minutesPastWindow() {
  const w = state.trip && state.trip.monitoring_window_min;
  if (!w) throw new Error("no monitoring window yet — wait for the agent run");
  return Number(w) + 5;
}

async function tier0Safe() {
  requireTraveller();
  if (!state.authToken) throw new Error("no auth token — re-run Reset & arm");
  const trip = await post("/travellers/me/tier0-response", { answer: "safe" },
                          { Authorization: `Bearer ${state.authToken}` });
  return trip ? `trip now ${trip.state}` : "no trip was waiting on a Tier 0 answer";
}

async function contactSafe() {
  const trip = await post("/demo/contact-reply", { trip_id: requireTrip(), reply: "safe" });
  return `trip now ${trip.state}`;
}

// -- status ------------------------------------------------------------------

function fmtClock(iso) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function renderStatus() {
  const t = state.trip;
  setText(ui.s.traveller, state.travellerId ? `${DEMO_TRAVELLER.name} · ${state.travellerId.slice(0, 8)}` : null);
  setText(ui.s.trip, state.tripId ? state.tripId.slice(0, 8) : null);
  setText(ui.s.stateName, t && t.state);
  setText(ui.s.risk, t && t.risk);
  setText(ui.s.predicted, t && t.predicted_crossing_min ? `${t.predicted_crossing_min} min` : null);
  setText(ui.s.window, t && t.monitoring_window_min ? `${t.monitoring_window_min} min` : null);
  setText(ui.s.deadline, t ? fmtClock(t.window_deadline) : null);
  setText(ui.s.now, t ? fmtClock(t.server_now) : null);

  const notes = (t && t.notifications) || [];
  setText(ui.s.notifications, notes.length ? notes.join(", ") : "none");

  if (ui.s.stateName) {
    ui.s.stateName.className = "state-" + String((t && t.state) || "").toLowerCase();
  }

  const w = t && t.monitoring_window_min;
  setText(ui.advanceHint, w
    ? `This trip's window is ${w} min — "past the monitoring window" advances ${Number(w) + 5}.`
    : "Window appears once the agent has run.");
}

function renderApiLog(rows) {
  ui.apiLog.textContent = "";
  if (!rows || !rows.length) {
    ui.apiLog.appendChild(el("p", "demo-note", "No calls yet."));
    return;
  }
  rows.forEach((r) => {
    const row = el("div", "demo-api-row");
    row.appendChild(el("span", "demo-api-time", fmtClock(r.ts)));
    row.appendChild(el("span", "demo-api-name", r.api));
    // Three outcomes, and only one of them is a fault. A negative status
    // is the marker for a call that was never attempted at all — today
    // that is a contact message with no SMS gateway configured (see
    // whatsapp_client.NOT_ATTEMPTED) — and printing a raw "-1" says
    // nothing while colouring it red says the wrong thing.
    const code = Number(r.status);
    const cls = code < 0 ? "skip" : code >= 400 ? "bad" : "good";
    row.appendChild(
      el("span", `demo-api-status ${cls}`, code < 0 ? "SKIP" : r.status)
    );
    row.appendChild(el("span", "demo-api-latency", `${Math.round(r.latency_ms)} ms`));
    ui.apiLog.appendChild(row);
  });
}

function markPoll(ok) {
  state.lastPollOk = ok;
  state.lastPollAt = Date.now();
  if (ui.liveIndicator) ui.liveIndicator.classList.toggle("stale", !ok);
  setText(ui.lastUpdated, ok ? "updated just now" : "backend unreachable");
}

async function refresh() {
  if (!state.ready) return;
  try {
    if (state.travellerId) {
      const active = await api(`/demo/trips/active?traveller_id=${encodeURIComponent(state.travellerId)}`);
      if (active && active.trip_id) {
        if (active.trip_id !== state.tripId) {
          state.tripId = active.trip_id;
          saveSession();
        }
      }
    }
    if (state.tripId) {
      try {
        state.trip = await api(`/trips/${encodeURIComponent(state.tripId)}`);
      } catch (err) {
        // A trip remembered in localStorage from before a reset no longer
        // exists. The backend answered, so this is not "unreachable" —
        // forget the trip and keep polling.
        if (err.status !== 404) throw err;
        state.tripId = "";
        state.trip = null;
        saveSession();
      }
    }
    renderStatus();
    renderApiLog(await api("/demo/api-log?limit=8"));
    markPoll(true);
  } catch (err) {
    markPoll(false);
  }
}

// -- boot --------------------------------------------------------------------

function disablePanel(message) {
  state.ready = false;
  setBusy(false);
  ui.main.hidden = true;
  ui.unavailable.hidden = false;
  ui.unavailable.textContent = message;
}

async function bootstrap() {
  ui.unavailable.hidden = true;
  ui.main.hidden = false;
  let health;
  try {
    health = await api("/healthz");
  } catch (err) {
    disablePanel(
      `No demo backend at ${state.backendUrl}. These controls only exist when the ` +
      `backend runs with SIGNALGUARD_DEMO_MODE=true — in a production build the ` +
      `/demo endpoints are not mounted at all.`
    );
    return;
  }
  if (!health || health.demo !== true) {
    disablePanel(
      `${state.backendUrl} is not running in demo mode. Synthetic triggers are ` +
      `unavailable, by design.`
    );
    return;
  }

  state.ready = true;
  setBusy(false);

  try {
    state.zones = await api("/zones");
  } catch (err) {
    state.zones = [];
  }
  ui.zone.textContent = "";
  state.zones.forEach((z) => {
    const opt = el("option", null, `${z.zone_id} · ${z.corridor_km} km`);
    opt.value = z.zone_id;
    ui.zone.appendChild(opt);
  });
  if (state.zoneId && state.zones.some((z) => z.zone_id === state.zoneId)) {
    ui.zone.value = state.zoneId;
  } else if (state.zones.length) {
    state.zoneId = state.zones[0].zone_id;
  }

  ui.model.checked = !!(health.agent && health.agent.enabled);
  logLine("ok", "connected", `${state.backendUrl} · agent ${(health.agent && health.agent.model) || "—"} ${health.agent && health.agent.enabled ? "enabled" : "DISABLED"}`);

  await refresh();
}

// -- wiring ------------------------------------------------------------------

ui.backendInput.value = state.backendUrl;
ui.backendInput.addEventListener("change", () => {
  const v = ui.backendInput.value.trim().replace(/\/$/, "");
  if (!v) return;
  state.backendUrl = v;
  localStorage.setItem("sg_backend_url", v);
  bootstrap();
});

$("b-arm").addEventListener("click", () => run("reset & arm", armEverything));
$("b-battery").addEventListener("click", () => run("battery", async () => {
  const level = Number(ui.battery.value) || 82;
  await post("/demo/battery", { traveller_id: requireTraveller(), level });
  return `${level}%`;
}));
$("b-model").addEventListener("click", () => run("agent model", async () => {
  const res = await post("/demo/agent/model", { enabled: ui.model.checked });
  return res.enabled ? "gemini enabled" : "gemini DISABLED — deterministic fallback only";
}));

$("b-enter").addEventListener("click", () => run("entry gate", () => gate("entry")));
$("b-exit").addEventListener("click", () => run("exit gate", () => gate("exit")));
$("b-dark").addEventListener("click", () => run("go dark", () => reachability(false)));
$("b-reconnect").addEventListener("click", () => run("reconnect", () => reachability(true)));

document.querySelectorAll("[data-advance]").forEach((btn) => {
  const mins = Number(btn.getAttribute("data-advance"));
  btn.addEventListener("click", () => run(`clock +${mins}m`, () => advance(mins)));
});
$("b-advance-custom").addEventListener("click", () => {
  const mins = Number(ui.minutes.value) || 30;
  run(`clock +${mins}m`, () => advance(mins));
});
$("b-past-window").addEventListener("click", () =>
  run("past window", () => advance(minutesPastWindow())));
$("b-past-grace").addEventListener("click", () =>
  run("past tier 1 grace", () => advance(20)));

$("b-tier0-safe").addEventListener("click", () => run("tier 0 answer", tier0Safe));
$("b-contact-safe").addEventListener("click", () => run("contact stand-down", contactSafe));

// Polling pauses on a hidden tab, same as the operator console — a demo
// panel left open in a background tab should not keep calling tick().
setInterval(() => {
  if (!document.hidden && !state.busy) refresh();
}, POLL_MS);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});

bootstrap();
