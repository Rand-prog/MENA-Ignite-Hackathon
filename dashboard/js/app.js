"use strict";

/* SignalGuard — Emergency Centre dashboard.
 * Vanilla HTML/CSS/JS, no build step — matches the "hackathon prototype,
 * not a product" scope: no auth, no multi-tenancy, no settings beyond the
 * backend URL. See docs/SignalGuard_User_Flow's Emergency Center Flow.
 *
 * Two rules this file follows without exception, both learned the hard way:
 *
 *  1. NOTHING from the backend is ever put into innerHTML. Traveller names
 *     come from a public registration form; a name of
 *     `<img src=x onerror=...>` used to execute in the dispatcher's browser
 *     on every poll. Backend-derived text goes through textContent, always.
 *     `el()` below exists to make that the path of least resistance.
 *
 *  2. The trip list is updated in place, never rebuilt. A 3-second
 *     innerHTML wipe destroys focus and scroll position, which means a
 *     keyboard user cannot hold a row long enough to press it, and a
 *     screen reader re-reads the whole queue every three seconds.
 */

const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 3000;

const state = {
  backendUrl: localStorage.getItem("sg_backend_url") || DEFAULT_BACKEND,
  zones: [],
  zonesById: {},
  corridorMeta: null,
  trips: [],
  history: [],
  zoneStats: null,
  selectedTripId: null,
  lastPollOk: true,
  lastPollAt: null,
  view: "live", // "live" | "history"
  // Trip ids that were already red last poll, so a trip that *becomes* red
  // announces itself once rather than every three seconds forever.
  redSeen: new Set(),
  alertsMuted: localStorage.getItem("sg_alerts_muted") === "1",
  bootstrapped: false,
  // Zone the map and queue are scoped to. null = every zone.
  activeZoneId: null,
  // Snapshot of each trip's state taken when the dispatcher last had this
  // tab in front of them, so "what moved while I was away" is answerable.
  // See markSeen() / changeSince().
  seen: new Map(),
  awayChanges: [],
  focusedIndex: -1,
};

// -- DOM helpers -------------------------------------------------------------

/** Build an element. Text is set with textContent, never innerHTML — see
 *  rule 1 at the top of this file. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setText(node, value) {
  if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
}

// -- Web Mercator projection — must match app/tool/fetch_corridor_map.py
// and the Flutter app's CorridorMap.project() exactly, or markers land in
// the wrong place relative to the stitched tile image.
function mercatorY(latDeg) {
  const latRad = (latDeg * Math.PI) / 180;
  const mercN = Math.log(Math.tan(Math.PI / 4 + latRad / 2));
  return 0.5 - mercN / (2 * Math.PI);
}

function projectFrac(lat, lon, bounds) {
  const xFrac = (lon - bounds.west) / (bounds.east - bounds.west);
  const yTop = mercatorY(bounds.north);
  const yBottom = mercatorY(bounds.south);
  const yFrac = (mercatorY(lat) - yTop) / (yBottom - yTop);
  return { xFrac, yFrac };
}

/** Point at `progress` (0..1) along a zone's entry -> exit line. */
function alongCorridor(zone, progress) {
  const t = Math.max(0, Math.min(1, progress));
  return {
    lat: zone.entry_gate.lat + (zone.exit_gate.lat - zone.entry_gate.lat) * t,
    lon: zone.entry_gate.lon + (zone.exit_gate.lon - zone.entry_gate.lon) * t,
  };
}

/** The zone a given trip is actually in.
 *
 *  This used to resolve every trip against `trips[0]`'s zone, which plotted
 *  the whole queue onto whichever corridor happened to be first in the
 *  list. Invisible with one zone configured; wrong the instant there are
 *  two, and wrong in the specific way that puts a traveller on a map
 *  somewhere they have never been. */
function zoneFor(trip) {
  return state.zonesById[trip.zone_id] || state.zones[0] || null;
}

/// Trips belonging to the zone currently selected, or all of them.
function inActiveZone(trips) {
  if (!state.activeZoneId) return trips;
  return trips.filter((t) => t.zone_id === state.activeZoneId);
}

/// Does the selected zone have a bundled map pack?
///
/// Only the surveyed corridor ships one. A zone promoted from a coverage
/// candidate has real gates and real trips but no tiles, and pretending
/// otherwise — drawing its trips over Highway 15's terrain — would put a
/// traveller on a map somewhere they have never been. So the map frame is
/// hidden and says why.
function activeZoneHasMap() {
  const meta = state.corridorMeta;
  const zone = state.activeZoneId
    ? state.zonesById[state.activeZoneId]
    : state.zones[0];
  if (!meta || !zone) return false;
  const midLat = (zone.entry_gate.lat + zone.exit_gate.lat) / 2;
  const midLon = (zone.entry_gate.lon + zone.exit_gate.lon) / 2;
  return (
    midLat <= meta.north && midLat >= meta.south &&
    midLon >= meta.west && midLon <= meta.east
  );
}

// -- classification ----------------------------------------------------------

function classify(trip) {
  if (["OVERDUE", "TIER1_ALERTED", "TIER2_ESCALATED"].includes(trip.state)) {
    return "red";
  }
  // Tier 0 is amber, never red: the window has expired but no human has
  // been told anything yet, and colouring it red would put a dispatcher on
  // alert for something the system is still resolving by itself.
  if (trip.state === "BUFFER" || trip.state === "TIER0_CHECKING") return "amber";
  if (
    trip.state === "ACTIVE" &&
    trip.elapsed_min != null &&
    trip.monitoring_window_min
  ) {
    if (trip.elapsed_min >= trip.monitoring_window_min * 0.8) return "amber";
  }
  return "green";
}

const SEVERITY_ORDER = { red: 0, amber: 1, green: 2 };

function stateLabel(trip) {
  const labels = {
    BUFFER: "Preparing",
    ACTIVE: "In transit",
    TIER0_CHECKING: "Asking traveller",
    OVERDUE: "Overdue",
    TIER1_ALERTED: "Contact alerted",
    TIER2_ESCALATED: "Action needed",
    EXITED: "Completed",
    RESOLVED: "Resolved",
  };
  return labels[trip.state] || trip.state;
}

// -- DOM refs ----------------------------------------------------------------

const el_ = (id) => document.getElementById(id);
const ui = {
  backendInput: el_("backend-url"),
  liveIndicator: el_("live-indicator"),
  lastUpdated: el_("last-updated"),
  muteToggle: el_("mute-toggle"),
  announcer: el_("announcer"),
  zoneLabel: el_("zone-label"),
  mapFrame: el_("map-frame"),
  mapImage: el_("map-image"),
  mapOverlay: el_("map-overlay"),
  mapAttribution: el_("map-attribution"),
  coneLayer: el_("cone-layer"),
  tripList: el_("trip-list"),
  tripCount: el_("trip-count"),
  emptyState: el_("empty-state"),
  tabLive: el_("tab-live"),
  tabHistory: el_("tab-history"),
  statsPanel: el_("stats-panel"),
  statsBody: el_("stats-body"),
  zoneSelect: el_("zone-select"),
  zoneMapNote: el_("zone-map-note"),
  changeBanner: el_("change-banner"),
  changeBannerBody: el_("change-banner-body"),
  changeBannerDismiss: el_("change-banner-dismiss"),
  shortcutHint: el_("shortcut-hint"),
  shortcutToggle: el_("shortcut-toggle"),
  detailHandoff: el_("detail-handoff"),
  detailPanel: el_("detail-panel"),
  detailClose: el_("detail-close"),
  detailStatus: el_("detail-status"),
  detailName: el_("detail-name"),
  detailMsisdn: el_("detail-msisdn"),
  detailEntry: el_("detail-entry"),
  detailLastKnown: el_("detail-lastknown"),
  detailPredicted: el_("detail-predicted"),
  detailPosition: el_("detail-position"),
  detailBattery: el_("detail-battery"),
  detailCongestion: el_("detail-congestion"),
  detailRisk: el_("detail-risk"),
  detailAlert: el_("detail-alert"),
  detailConvoy: el_("detail-convoy"),
  detailRecord: el_("detail-record"),
  detailEscalationBlock: el_("detail-escalation-block"),
  detailEscalation: el_("detail-escalation"),
  detailResolve: el_("detail-resolve"),
  detailResolveConfirm: el_("detail-resolve-confirm"),
  scrim: el_("scrim"),
};

// -- backend URL field -------------------------------------------------------

ui.backendInput.value = state.backendUrl;
ui.backendInput.addEventListener("change", () => {
  const v = ui.backendInput.value.trim().replace(/\/$/, "");
  if (!v) return;
  state.backendUrl = v;
  localStorage.setItem("sg_backend_url", v);
  bootstrap();
});

// -- fetch helpers -----------------------------------------------------------

async function api(path, opts) {
  const resp = await fetch(state.backendUrl + path, opts);
  if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

// -- escalation alerting -----------------------------------------------------
//
// An emergency console where a trip escalates in silence is an operational
// hole, not a polish item: the dispatcher is not necessarily looking at
// this tab, and until now nothing about a Tier 2 escalation reached them
// except a colour change on a list they might not be able to see.

let audioCtx = null;

/** Two-tone alert, synthesised rather than shipped as an audio file so the
 *  dashboard keeps its no-assets, no-build-step property. */
function playAlert() {
  if (state.alertsMuted) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    // Browsers suspend an AudioContext created before any user gesture.
    if (audioCtx.state === "suspended") audioCtx.resume();
    const now = audioCtx.currentTime;
    [880, 660].forEach((freq, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.22);
      gain.gain.exponentialRampToValueAtTime(0.22, now + i * 0.22 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.22 + 0.2);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(now + i * 0.22);
      osc.stop(now + i * 0.22 + 0.22);
    });
  } catch (e) {
    // Audio is an enhancement — a browser that blocks it must not take the
    // rest of the alert (title, announcement, colour) down with it.
    console.warn("alert tone unavailable", e);
  }
}

let titleFlashTimer = null;
const BASE_TITLE = document.title;

function flashTitle(count) {
  clearInterval(titleFlashTimer);
  let on = false;
  let left = 12;
  titleFlashTimer = setInterval(() => {
    document.title = on ? BASE_TITLE : `⚠ ${count} NEED ACTION`;
    on = !on;
    if (--left <= 0) {
      clearInterval(titleFlashTimer);
      document.title = BASE_TITLE;
    }
  }, 700);
}

/** Announce to assistive tech.
 *
 *  Deliberately a dedicated node rather than aria-live on the trip list.
 *  A live region wrapped around a list that changes every 3 seconds makes
 *  a screen reader re-read the entire queue on every poll — worse than no
 *  live region at all. This announces only transitions. */
function announce(message, assertive) {
  if (!ui.announcer) return;
  ui.announcer.setAttribute("aria-live", assertive ? "assertive" : "polite");
  // Clearing first forces a re-announcement when the text repeats.
  ui.announcer.textContent = "";
  window.setTimeout(() => {
    ui.announcer.textContent = message;
  }, 60);
}

function checkForNewEscalations(trips) {
  const nowRed = trips.filter((t) => classify(t) === "red");
  const fresh = nowRed.filter((t) => !state.redSeen.has(t.trip_id));

  state.redSeen = new Set(nowRed.map((t) => t.trip_id));
  if (fresh.length === 0) return;

  playAlert();
  flashTitle(nowRed.length);
  const names = fresh.map((t) => t.traveller_name || "A traveller").join(", ");
  announce(
    `${fresh.length === 1 ? "Escalation" : "Escalations"}: ${names} — ${
      fresh.length === 1 ? stateLabel(fresh[0]) : "action needed"
    }.`,
    true
  );
}

ui.muteToggle.addEventListener("click", () => {
  state.alertsMuted = !state.alertsMuted;
  localStorage.setItem("sg_alerts_muted", state.alertsMuted ? "1" : "0");
  renderMuteToggle();
  if (!state.alertsMuted) playAlert(); // confirm it works, and unlock audio
});

function renderMuteToggle() {
  ui.muteToggle.textContent = state.alertsMuted ? "🔇 Alerts off" : "🔔 Alerts on";
  ui.muteToggle.setAttribute("aria-pressed", state.alertsMuted ? "true" : "false");
  ui.muteToggle.title = state.alertsMuted
    ? "Escalation tone is off — click to enable"
    : "Escalation tone is on — click to mute";
}

// -- map rendering -----------------------------------------------------------

async function loadCorridor() {
  const [zones, meta] = await Promise.all([
    api("/zones"),
    fetch("assets/map/corridor.json").then((r) => r.json()),
  ]);
  state.zones = zones;
  state.zonesById = Object.fromEntries(zones.map((z) => [z.zone_id, z]));
  state.corridorMeta = meta;

  renderZoneSelect();
  const zone = state.activeZoneId ? state.zonesById[state.activeZoneId] : zones[0];
  setText(ui.zoneLabel, zone ? zone.label : "No zone configured");
  setText(ui.mapAttribution, meta.attribution);
  ui.mapImage.width = meta.width_px;
  ui.mapImage.height = meta.height_px;
  ui.mapImage.src = "assets/map/corridor.png";
  ui.mapFrame.style.aspectRatio = `${meta.width_px} / ${meta.height_px}`;

  renderGateMarkers();
  applyZoneMapVisibility();
  if (zone) loadZoneStats(zone.zone_id);
}

/// The zone picker. Hidden entirely while there is only one zone — a
/// dropdown with a single option is furniture, not a control.
function renderZoneSelect() {
  const multi = state.zones.length > 1;
  ui.zoneSelect.hidden = !multi;
  if (!multi) {
    state.activeZoneId = state.zones[0] ? state.zones[0].zone_id : null;
    return;
  }
  while (ui.zoneSelect.firstChild) ui.zoneSelect.removeChild(ui.zoneSelect.firstChild);
  const all = el("option", null, "All corridors");
  all.value = "";
  ui.zoneSelect.appendChild(all);
  for (const z of state.zones) {
    const opt = el("option", null, z.label);
    opt.value = z.zone_id;
    ui.zoneSelect.appendChild(opt);
  }
  ui.zoneSelect.value = state.activeZoneId || "";
}

function applyZoneMapVisibility() {
  const ok = activeZoneHasMap();
  ui.mapFrame.hidden = !ok;
  ui.zoneMapNote.hidden = ok;
}

function renderGateMarkers() {
  ui.mapOverlay.querySelectorAll(".gate-marker").forEach((n) => n.remove());
  const zone = state.activeZoneId
    ? state.zonesById[state.activeZoneId]
    : state.zones[0];
  const meta = state.corridorMeta;
  if (!zone || !meta || !activeZoneHasMap()) return;

  for (const [lat, lon, label] of [
    [zone.entry_gate.lat, zone.entry_gate.lon, "ENTRY"],
    [zone.exit_gate.lat, zone.exit_gate.lon, "EXIT"],
  ]) {
    const { xFrac, yFrac } = projectFrac(lat, lon, meta);
    const marker = el("div", "map-marker gate-marker");
    marker.style.left = `${xFrac * 100}%`;
    marker.style.top = `${yFrac * 100}%`;
    marker.appendChild(el("span", "dot"));
    marker.appendChild(el("span", "label", label));
    ui.mapOverlay.appendChild(marker);
  }
}

/** Draw each live trip as an uncertainty arc plus a best-guess marker.
 *
 *  The old marker was a single dot at an interpolated position, visually
 *  identical to a real GPS fix. It is not one: the network hands this
 *  system exactly one real location — the snapshot taken at the entry
 *  gate — and cannot see inside the dead zone afterwards any more than the
 *  phone can. Everything after that point is dead reckoning against the
 *  clock.
 *
 *  So what gets drawn is the range the backend can actually defend
 *  (uncertainty.py), widening with time. Beyond the honesty, this is what
 *  a Tier 2 handoff actually needs: a search area. A point is not a search
 *  area — it is a false one, and a team sent to a false point has spent
 *  the only resource that matters. */
const markerNodes = new Map(); // trip_id -> {wrap, dot, line}

function renderTripMarkers() {
  const meta = state.corridorMeta;
  if (!meta) return;

  const trips =
    state.view === "live" && activeZoneHasMap() ? inActiveZone(state.trips) : [];
  const seen = new Set();

  trips.forEach((trip, i) => {
    const zone = zoneFor(trip);
    if (!zone) return;
    seen.add(trip.trip_id);
    const est = trip.position_estimate;
    const sev = classify(trip);

    // Nodes are reused across polls, keyed by trip id — same reason as the
    // list rows. These dots are focusable buttons, and rebuilding them
    // every 3 seconds meant a keyboard user could never keep one.
    let node = markerNodes.get(trip.trip_id);
    if (!node) {
      const wrap = el("div", "map-marker trip-marker-wrap");
      const dot = el("button", "trip-marker");
      dot.type = "button";
      dot.addEventListener("click", () => openDetail(trip.trip_id));
      wrap.appendChild(dot);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      node = { wrap, dot, line };
      markerNodes.set(trip.trip_id, node);
    }
    if (node.wrap.parentNode !== ui.mapOverlay) ui.mapOverlay.appendChild(node.wrap);

    // Convoy trips share a corridor and enter together, so their cones sit
    // on the same line and merged into one indistinguishable bar. Fan them
    // out perpendicular to the corridor — the offset is presentational and
    // says nothing about position, so it stays small enough to read as
    // "these overlap" rather than as separate routes.
    const lane = (i % 5) - 2;
    const laneOffsetPct = lane * 1.6;

    if (est) {
      const a = alongCorridor(zone, est.progress_min);
      const b = alongCorridor(zone, est.progress_max);
      const pa = projectFrac(a.lat, a.lon, meta);
      const pb = projectFrac(b.lat, b.lon, meta);
      node.line.setAttribute("x1", `${pa.xFrac * 100 + laneOffsetPct}%`);
      node.line.setAttribute("y1", `${pa.yFrac * 100}%`);
      node.line.setAttribute("x2", `${pb.xFrac * 100 + laneOffsetPct}%`);
      node.line.setAttribute("y2", `${pb.yFrac * 100}%`);
      node.line.setAttribute("class", `cone cone-${sev}`);
      if (node.line.parentNode !== ui.coneLayer) ui.coneLayer.appendChild(node.line);
    } else if (node.line.parentNode) {
      node.line.remove();
    }

    const centre = est
      ? alongCorridor(zone, est.best_guess_meaningful ? est.progress : (est.progress_min + est.progress_max) / 2)
      : alongCorridor(zone, 0);
    const { xFrac, yFrac } = projectFrac(centre.lat, centre.lon, meta);
    node.wrap.style.left = `${xFrac * 100 + laneOffsetPct}%`;
    node.wrap.style.top = `${yFrac * 100}%`;

    // A best guess that has outlived its own prediction is not a guess
    // about a moving vehicle any more, and stops being drawn as one.
    node.dot.className =
      `trip-marker state-${sev}` +
      (est && !est.best_guess_meaningful ? " is-estimate-only" : "") +
      (trip.trip_id === state.selectedTripId ? " selected" : "");
    const spread = est ? ` — position known to within ${est.spread_km} km` : "";
    const label = `${trip.traveller_name || "Traveller"} — ${stateLabel(trip)}${spread}`;
    node.dot.title = label;
    node.dot.setAttribute("aria-label", label);
  });

  for (const [id, node] of markerNodes) {
    if (!seen.has(id)) {
      node.wrap.remove();
      node.line.remove();
      markerNodes.delete(id);
    }
  }
}

// -- trip list ---------------------------------------------------------------
//
// Updated in place. Rows are keyed by trip id and reused across polls, so
// focus, scroll position and the browser's own "which button am I on"
// state all survive — which they did not when this cleared innerHTML and
// rebuilt every row on a 3-second timer.

const rowNodes = new Map(); // trip_id -> {row, dot, name, meta, label}

function buildRow(trip) {
  const row = el("button", "trip-row");
  row.type = "button";
  row.dataset.tripId = trip.trip_id;

  const dot = el("span", "status-dot");
  const main = el("span", "trip-main");
  const name = el("div", "trip-name");
  const meta = el("div", "trip-meta");
  main.append(name, meta);
  const label = el("span", "trip-state-label");

  row.append(dot, main, label);
  row.addEventListener("click", () => openDetail(trip.trip_id));
  return { row, dot, name, meta, label };
}

function rowText(trip) {
  const elapsedStr =
    trip.elapsed_min != null ? `${Math.round(trip.elapsed_min)} min elapsed` : "—";
  const windowStr = trip.monitoring_window_min
    ? ` / ${trip.monitoring_window_min} min window`
    : "";
  const stopStr = trip.planned_stop_min
    ? ` · +${trip.planned_stop_min} min declared stop`
    : "";
  return `${elapsedStr}${windowStr}${stopStr} · ${trip.congestion_tier || "—"} traffic`;
}

function renderList() {
  const trips =
    state.view === "live" ? inActiveZone(state.trips) : inActiveZone(state.history);

  setText(
    ui.tripCount,
    state.view === "live"
      ? `${trips.length} monitored`
      : `${trips.length} completed`
  );
  ui.emptyState.classList.toggle("visible", trips.length === 0);
  ui.emptyState.querySelector("[data-empty-live]").hidden = state.view !== "live";
  ui.emptyState.querySelector("[data-empty-history]").hidden = state.view === "live";

  const sorted = [...trips].sort((a, b) => {
    if (state.view === "history") {
      return String(b.entered_at || "").localeCompare(String(a.entered_at || ""));
    }
    const sevDiff = SEVERITY_ORDER[classify(a)] - SEVERITY_ORDER[classify(b)];
    if (sevDiff !== 0) return sevDiff;
    return (b.elapsed_min || 0) - (a.elapsed_min || 0);
  });

  const seen = new Set();
  sorted.forEach((trip, i) => {
    seen.add(trip.trip_id);
    let node = rowNodes.get(trip.trip_id);
    if (!node) {
      node = buildRow(trip);
      rowNodes.set(trip.trip_id, node);
    }
    const sev = classify(trip);
    node.row.className = `trip-row state-${sev}${
      trip.trip_id === state.selectedTripId ? " selected" : ""
    }`;
    setText(node.name, trip.traveller_name || "Unknown traveller");
    setText(node.meta, rowText(trip));
    setText(node.label, stateLabel(trip));
    // Marks rows that moved while the dispatcher was not looking — see
    // markSeen(). Cleared as soon as the banner is dismissed.
    node.row.classList.toggle(
      "changed-while-away",
      state.awayChanges.some((c) => c.trip_id === trip.trip_id)
    );

    // Move into position only if it isn't already there — reordering a node
    // that is already correct would still blur it in some browsers.
    const current = ui.tripList.children[i];
    if (current !== node.row) ui.tripList.insertBefore(node.row, current || null);
  });

  for (const [id, node] of rowNodes) {
    if (!seen.has(id)) {
      node.row.remove();
      rowNodes.delete(id);
    }
  }
}

// -- "what changed while I was away" -----------------------------------------
//
// A dispatcher looks away — another screen, a phone call, a coffee. On
// return the queue is a list of rows that all look equally current, and
// nothing distinguishes the trip that escalated two minutes ago from the
// six that have been quietly in transit for an hour. At twenty trips that
// is unusable; at three it is still a worse experience than it needs to be.
//
// So the state of every trip is snapshotted when the tab loses focus, and
// diffed against on return.

function snapshotTrips() {
  const map = new Map();
  for (const t of state.trips) map.set(t.trip_id, t.state);
  return map;
}

function markSeen() {
  state.seen = snapshotTrips();
  state.awayChanges = [];
  ui.changeBanner.hidden = true;
  renderList();
}

function changeSince() {
  const changes = [];
  const current = new Map(state.trips.map((t) => [t.trip_id, t]));

  for (const [id, trip] of current) {
    const before = state.seen.get(id);
    if (before === undefined) {
      changes.push({ trip_id: id, kind: "new", trip });
    } else if (before !== trip.state) {
      changes.push({ trip_id: id, kind: "moved", from: before, trip });
    }
  }
  for (const [id] of state.seen) {
    if (!current.has(id)) changes.push({ trip_id: id, kind: "closed" });
  }
  return changes;
}

function renderChangeBanner() {
  const changes = state.awayChanges;
  if (changes.length === 0) {
    ui.changeBanner.hidden = true;
    return;
  }
  while (ui.changeBannerBody.firstChild) {
    ui.changeBannerBody.removeChild(ui.changeBannerBody.firstChild);
  }
  for (const c of changes.slice(0, 6)) {
    const name = c.trip ? c.trip.traveller_name || "A traveller" : "A trip";
    const text =
      c.kind === "new"
        ? `${name} entered the corridor`
        : c.kind === "closed"
          ? "A trip closed and left the queue"
          : `${name}: ${c.from} → ${c.trip.state}`;
    ui.changeBannerBody.appendChild(el("li", null, text));
  }
  if (changes.length > 6) {
    ui.changeBannerBody.appendChild(
      el("li", "muted", `…and ${changes.length - 6} more`)
    );
  }
  ui.changeBanner.hidden = false;
  announce(
    `${changes.length} change${changes.length === 1 ? "" : "s"} while you were away.`,
    false
  );
}

ui.changeBannerDismiss.addEventListener("click", markSeen);

// -- view tabs ---------------------------------------------------------------

function setView(view) {
  state.view = view;
  ui.tabLive.setAttribute("aria-selected", view === "live" ? "true" : "false");
  ui.tabHistory.setAttribute("aria-selected", view === "history" ? "true" : "false");
  // Rows are keyed by trip id; switching datasets means none of the cached
  // nodes belong to the new list.
  rowNodes.forEach((n) => n.row.remove());
  rowNodes.clear();
  renderList();
  renderTripMarkers();
  if (view === "history") refreshHistory();
}

ui.tabLive.addEventListener("click", () => setView("live"));
ui.tabHistory.addEventListener("click", () => setView("history"));

ui.zoneSelect.addEventListener("change", () => {
  state.activeZoneId = ui.zoneSelect.value || null;
  const zone = state.activeZoneId ? state.zonesById[state.activeZoneId] : state.zones[0];
  setText(ui.zoneLabel, zone ? zone.label : "All corridors");
  applyZoneMapVisibility();
  renderGateMarkers();
  markerNodes.forEach((n) => {
    n.wrap.remove();
    n.line.remove();
  });
  markerNodes.clear();
  renderTripMarkers();
  renderList();
  if (zone) loadZoneStats(zone.zone_id);
});

async function refreshHistory() {
  try {
    state.history = await api("/dashboard/history?limit=50");
    if (state.view === "history") renderList();
  } catch (e) {
    console.error("history load failed", e);
  }
}

// -- corridor stats ----------------------------------------------------------

async function loadZoneStats(zoneId) {
  try {
    state.zoneStats = await api(`/dashboard/zones/${encodeURIComponent(zoneId)}/stats`);
  } catch (e) {
    state.zoneStats = null;
  }
  renderZoneStats();
}

/** How long this corridor actually takes, by hour of day.
 *
 *  Shows which buckets are in use and which are still below the sample
 *  floor, because that distinction is the point rather than a footnote: a
 *  dispatcher should be able to tell at a glance whether the monitoring
 *  window for the current hour rests on measured crossings or on the
 *  registry's hand-set constant. */
function renderZoneStats() {
  const stats = state.zoneStats;
  while (ui.statsBody.firstChild) ui.statsBody.removeChild(ui.statsBody.firstChild);
  if (!stats || !stats.buckets || stats.buckets.length === 0) {
    // "No crossings" and "crossings, but every one of them escalated" are
    // very different facts about a corridor, and collapsing them into one
    // message hides the more interesting of the two.
    const escalatedOnly = stats && stats.total_crossings > 0;
    ui.statsBody.appendChild(
      el(
        "p",
        "record-empty",
        escalatedOnly
          ? `${stats.total_crossings} crossing${stats.total_crossings === 1 ? "" : "s"} recorded, ` +
            "none of them clean — escalated crossings are excluded from the baseline, so the " +
            "monitoring window is still planned against the registry's nominal time."
          : "No completed crossings yet — the monitoring window is planned against the registry's nominal time."
      )
    );
    return;
  }

  const summary = el(
    "p",
    "stats-summary",
    `${stats.clean_crossings} clean crossings of ${stats.total_crossings} recorded · ` +
      `${stats.min_samples} needed per hour before history replaces the nominal`
  );
  ui.statsBody.appendChild(summary);

  const max = Math.max(...stats.buckets.map((b) => b.p90_min), 1);
  const list = el("div", "stats-bars");
  for (const b of stats.buckets) {
    const rowEl = el("div", `stats-bar${b.in_use ? " in-use" : ""}`);
    rowEl.appendChild(el("span", "stats-hour", `${String(b.hour_of_day).padStart(2, "0")}:00`));
    const track = el("span", "stats-track");
    const fill = el("span", "stats-fill");
    fill.style.width = `${(b.p50_min / max) * 100}%`;
    const tail = el("span", "stats-tail");
    tail.style.left = `${(b.p50_min / max) * 100}%`;
    tail.style.width = `${((b.p90_min - b.p50_min) / max) * 100}%`;
    track.append(fill, tail);
    rowEl.appendChild(track);
    rowEl.appendChild(
      el("span", "stats-value", `${b.p50_min}/${b.p90_min} min`)
    );
    rowEl.title = b.in_use
      ? `${b.samples} crossings — in use for this hour`
      : `${b.samples} crossings — below the ${stats.min_samples}-sample floor, nominal still in use`;
    list.appendChild(rowEl);
  }
  ui.statsBody.appendChild(list);
}

// -- agent record rendering ---------------------------------------------------
// Parses the backend's plain-text "key   value" record format (see
// backend/app/agent/decision_record.py — read on camera as a dispatcher
// log, so the backend keeps it human-readable rather than JSON) into rows
// for structured display. A field's continuation lines are indented with
// no key of their own (build_decision_record wraps `reasoning` this way);
// those get appended to the previous row's value.

function parseAgentRecord(text) {
  const rows = [];
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    const m = raw.match(/^(\S+)\s+(.*)$/);
    if (m) {
      rows.push({ key: m[1], value: m[2].trim() });
    } else if (rows.length) {
      rows[rows.length - 1].value += " " + raw.trim();
    }
  }
  return rows;
}

function toolChips(value) {
  const wrap = el("span", "record-tools");
  value.split(",").map((s) => s.trim()).filter(Boolean).forEach((tool) => {
    wrap.appendChild(el("span", "tool-chip", tool));
  });
  return wrap;
}

function signalChips(value) {
  const wrap = el("span", "record-signals");
  value.split(/\s{2,}/).map((s) => s.trim()).filter(Boolean).forEach((pair) => {
    const chip = el("span", "signal-chip", pair);
    if (/reachability=CONNECTED/.test(pair)) chip.classList.add("is-reachable");
    if (/reachability=NOT_CONNECTED/.test(pair)) chip.classList.add("is-dark");
    wrap.appendChild(chip);
  });
  return wrap;
}

function renderRecordCard(rows, title) {
  const card = el("div", "record-card");
  if (title) card.appendChild(el("div", "record-card-title", title));

  for (const { key, value } of rows) {
    const row = el("div", "record-row");
    row.appendChild(el("span", "record-key", key));

    const v = el("span", "record-value");
    if (key === "reasoning") {
      row.classList.add("is-reasoning");
      v.textContent = value;
    } else if (key === "note") {
      row.classList.add("is-note");
      v.textContent = value;
    } else if (key === "model") {
      row.classList.add("is-model");
      v.textContent = value;
    } else if (key === "history") {
      row.classList.add("is-history");
      v.textContent = value;
    } else if (key === "declared") {
      row.classList.add("is-declared");
      v.textContent = value;
    } else if (key === "window") {
      row.classList.add("is-window");
      const m = value.match(/^(.+?)(\(.*\))$/);
      if (m) {
        v.append(m[1].trim());
        v.appendChild(el("span", "record-sub", m[2]));
      } else {
        v.textContent = value;
      }
    } else if (key === "action") {
      row.classList.add("is-action");
      v.textContent = value;
    } else if (key === "tools") {
      v.appendChild(toolChips(value));
    } else if (key === "signals") {
      v.appendChild(signalChips(value));
    } else {
      v.textContent = value;
    }

    row.appendChild(v);
    card.appendChild(row);
  }
  return card;
}

function renderEscalationRecord(container, text) {
  while (container.firstChild) container.removeChild(container.firstChild);
  for (const block of text.split(/\n\n+/)) {
    const rows = parseAgentRecord(block);
    const escRow = rows.find((r) => r.key === "escalation");
    const title = escRow ? `Escalation — ${escRow.value.toUpperCase()}` : null;
    container.appendChild(
      renderRecordCard(rows.filter((r) => r.key !== "escalation"), title)
    );
  }
}

function renderDecisionRecord(container, text) {
  while (container.firstChild) container.removeChild(container.firstChild);
  container.appendChild(renderRecordCard(parseAgentRecord(text)));
}

// -- detail panel -------------------------------------------------------------

let detailOpenerEl = null;

async function openDetail(tripId) {
  detailOpenerEl = document.activeElement;
  state.selectedTripId = tripId;
  renderList();
  renderTripMarkers();

  try {
    const trip = await api(`/dashboard/trips/${encodeURIComponent(tripId)}`);
    fillDetail(trip);
    ui.detailPanel.hidden = false;
    ui.scrim.hidden = false;
    ui.detailClose.focus();
  } catch (e) {
    console.error("failed to load trip detail", e);
    announce("Could not load trip detail.", false);
  }
}

function fillDetail(trip) {
  const sev = classify(trip);
  setText(ui.detailStatus, stateLabel(trip));
  ui.detailStatus.className = `status-pill state-${sev}`;
  setText(ui.detailName, trip.traveller_name || "Unknown traveller");
  setText(ui.detailMsisdn, trip.traveller_msisdn);
  setText(ui.detailEntry, trip.entry_point);
  setText(ui.detailLastKnown, trip.last_known_location);

  const elapsed = trip.elapsed_min != null ? `${Math.round(trip.elapsed_min)} min` : "—";
  const predicted = trip.predicted_crossing_min ? `${trip.predicted_crossing_min} min` : "—";
  const win = trip.monitoring_window_min ? `${trip.monitoring_window_min} min` : "—";
  setText(ui.detailPredicted, `${elapsed} elapsed · predicted ${predicted} · window ${win}`);

  // Stated as a range, in the words a dispatcher would use to brief a
  // search team — never as a coordinate that implies a fix nobody has.
  const est = trip.position_estimate;
  setText(
    ui.detailPosition,
    est
      ? `Somewhere in a ${est.spread_km} km stretch of the corridor` +
          (est.best_guess_meaningful
            ? ` · on schedule, likely near the ${Math.round(est.progress * 100)}% mark`
            : " · past the predicted crossing time, so no point estimate is meaningful")
      : "—"
  );

  setText(
    ui.detailBattery,
    trip.battery_at_entry != null
      ? `${trip.battery_at_entry}% (${trip.battery_band || "—"})`
      : "—"
  );
  setText(ui.detailCongestion, trip.congestion_tier);
  setText(ui.detailRisk, trip.risk);

  const notes = trip.notifications || [];
  const human = notes.filter((n) => n !== "tier0");
  setText(
    ui.detailAlert,
    human.length === 0
      ? notes.includes("tier0")
        ? "Traveller asked directly — no human contacted"
        : "Not notified"
      : human.includes("tier2")
        ? "Contact + emergency centre alerted"
        : "Contact alerted (Tier 1)"
  );
  setText(
    ui.detailConvoy,
    trip.convoy_id
      ? `In a convoy — see the escalation record for a peer who already came through`
      : "Travelling alone"
  );

  if (trip.decision_record) {
    renderDecisionRecord(ui.detailRecord, trip.decision_record);
  } else {
    while (ui.detailRecord.firstChild) ui.detailRecord.removeChild(ui.detailRecord.firstChild);
    ui.detailRecord.appendChild(el("p", "record-empty", "No decision record yet."));
  }

  if (trip.escalation_record) {
    renderEscalationRecord(ui.detailEscalation, trip.escalation_record);
    ui.detailEscalationBlock.hidden = false;
  } else {
    ui.detailEscalationBlock.hidden = true;
  }

  ui.detailHandoff.onclick = () => copyHandoff(trip);
  resetResolveButton(trip);
}

// -- resolve, with a confirmation step ---------------------------------------
//
// Resolving clears a live escalation: the trip leaves the queue, the
// emergency centre stops watching it, and there is no undo. One stray click
// on a 40px button should not be able to do that.

let pendingResolveId = null;

function resetResolveButton(trip) {
  pendingResolveId = null;
  ui.detailResolveConfirm.hidden = true;
  ui.detailResolve.hidden = false;
  ui.detailResolve.disabled = !["TIER0_CHECKING", "OVERDUE", "TIER1_ALERTED", "TIER2_ESCALATED"].includes(
    trip.state
  );
  ui.detailResolve.textContent = ui.detailResolve.disabled
    ? "No Action Needed"
    : "Mark Resolved";
  ui.detailResolve.onclick = () => armResolve(trip);
}

function armResolve(trip) {
  pendingResolveId = trip.trip_id;
  ui.detailResolve.hidden = true;
  ui.detailResolveConfirm.hidden = false;
  const name = trip.traveller_name || "this traveller";
  setText(
    ui.detailResolveConfirm.querySelector("[data-confirm-text]"),
    `Clear the escalation for ${name}? They leave the live queue and the emergency centre stops watching.`
  );
  ui.detailResolveConfirm.querySelector("[data-confirm-yes]").focus();
}

ui.detailResolveConfirm.querySelector("[data-confirm-no]").addEventListener("click", () => {
  const trip = (state.trips.find((t) => t.trip_id === pendingResolveId)) || { state: "" };
  resetResolveButton(trip);
  ui.detailResolve.focus();
});

ui.detailResolveConfirm.querySelector("[data-confirm-yes]").addEventListener("click", async () => {
  const id = pendingResolveId;
  if (!id) return;
  const yes = ui.detailResolveConfirm.querySelector("[data-confirm-yes]");
  yes.disabled = true;
  yes.textContent = "Resolving…";
  try {
    await api(`/dashboard/trips/${encodeURIComponent(id)}/resolve`, { method: "POST" });
    state.redSeen.delete(id);
    closeDetail();
    announce("Trip resolved and cleared from the queue.", false);
    await pollTrips();
  } catch (e) {
    console.error("resolve failed", e);
    yes.disabled = false;
    yes.textContent = "Yes, resolve";
    announce("Resolve failed — the trip is still in the queue.", true);
  }
});

/// The Tier 2 handoff.
///
/// An escalation currently produces a panel in somebody's browser. The
/// people who act on it — a search team, a highway patrol dispatcher, a
/// duty officer on the phone — are not looking at this screen and cannot
/// be handed a DOM node. Until there is a real integration (see the
/// backlog), the least this can do is produce the brief in a form that can
/// be pasted into whatever channel is actually being used.
///
/// Leads with the search area rather than a coordinate, for the same
/// reason the map draws a cone: the point estimate is not something this
/// system knows, and a brief that implies otherwise sends people to the
/// wrong place with confidence.
function handoffText(trip) {
  const zone = zoneFor(trip);
  const est = trip.position_estimate;
  const L = [];
  L.push(`SIGNALGUARD — ${stateLabel(trip).toUpperCase()}`);
  L.push(`Traveller:      ${trip.traveller_name || "unknown"} (${trip.traveller_msisdn || "no number"})`);
  L.push(`Corridor:       ${zone ? zone.label : trip.zone_id}`);
  L.push(`Entered:        ${trip.entered_at || "—"}`);
  L.push(`Elapsed:        ${trip.elapsed_min != null ? Math.round(trip.elapsed_min) + " min" : "—"}` +
         `  (predicted ${trip.predicted_crossing_min || "—"} min, window ${trip.monitoring_window_min || "—"} min)`);
  L.push("");
  L.push("SEARCH AREA");
  if (est && zone) {
    const a = alongCorridor(zone, est.progress_min);
    const b = alongCorridor(zone, est.progress_max);
    L.push(`  A ${est.spread_km} km stretch of the corridor between:`);
    L.push(`    ${a.lat.toFixed(5)}, ${a.lon.toFixed(5)}`);
    L.push(`    ${b.lat.toFixed(5)}, ${b.lon.toFixed(5)}`);
    L.push(`  Derived from elapsed time at 25-120 km/h. NOT a GPS fix —`);
    L.push(`  the network has one position for this trip, taken at entry.`);
  } else {
    L.push("  Not computable — no elapsed time or corridor geometry.");
  }
  L.push("");
  L.push("LAST REAL POSITION (entry gate)");
  L.push(`  ${trip.entry_point || "unavailable"}`);
  L.push("");
  L.push("CONDITIONS AT ENTRY");
  L.push(`  Battery:      ${trip.battery_at_entry != null ? trip.battery_at_entry + "% (" + (trip.battery_band || "—") + ")" : "—"}`);
  L.push(`  Congestion:   ${trip.congestion_tier || "—"}`);
  L.push(`  Risk:         ${trip.risk || "—"}`);
  L.push(`  Convoy:       ${trip.convoy_id ? "yes — see escalation record" : "travelling alone"}`);
  L.push("");
  L.push("WHO HAS BEEN TOLD");
  const notes = (trip.notifications || []).filter((n) => n !== "tier0");
  L.push(`  ${notes.length === 0 ? "nobody" : notes.join(", ")}`);
  if (trip.decision_record) {
    L.push("");
    L.push("AGENT DECISION AT ENTRY");
    for (const line of trip.decision_record.split("\n")) L.push("  " + line);
  }
  if (trip.escalation_record) {
    L.push("");
    L.push("ESCALATION RECORD");
    for (const line of trip.escalation_record.split("\n")) L.push("  " + line);
  }
  L.push("");
  L.push(`Generated ${new Date().toISOString()} · SignalGuard prototype`);
  return L.join("\n");
}

async function copyHandoff(trip) {
  const text = handoffText(trip);
  try {
    await navigator.clipboard.writeText(text);
    ui.detailHandoff.textContent = "Brief copied";
    announce("Handoff brief copied to the clipboard.", false);
  } catch (e) {
    // Clipboard needs a secure context; a plain-http dashboard on a LAN
    // does not have one. Fall back to something a person can still act on
    // rather than failing silently.
    console.warn("clipboard unavailable, opening a printable window", e);
    const w = window.open("", "_blank");
    if (w) {
      const pre = w.document.createElement("pre");
      pre.textContent = text;
      pre.style.font = "13px ui-monospace, monospace";
      pre.style.whiteSpace = "pre-wrap";
      w.document.body.appendChild(pre);
      w.document.title = "SignalGuard handoff";
    } else {
      ui.detailHandoff.textContent = "Could not copy";
    }
  }
  setTimeout(() => {
    ui.detailHandoff.textContent = "Copy handoff brief";
  }, 2500);
}

function closeDetail() {
  ui.detailPanel.hidden = true;
  ui.scrim.hidden = true;
  pendingResolveId = null;
  ui.detailResolveConfirm.hidden = true;
  ui.detailResolve.hidden = false;
  state.selectedTripId = null;
  renderList();
  renderTripMarkers();
  if (detailOpenerEl && document.body.contains(detailOpenerEl)) {
    detailOpenerEl.focus();
  }
  detailOpenerEl = null;
}

ui.detailClose.addEventListener("click", closeDetail);
ui.scrim.addEventListener("click", closeDetail);

// Focus trap. Without one, Tab walks straight out of the panel and behind
// the scrim, where the user is interacting with a list they cannot see.
const FOCUSABLE =
  'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

document.addEventListener("keydown", (e) => {
  if (ui.detailPanel.hidden) return;
  if (e.key === "Escape") {
    closeDetail();
    return;
  }
  if (e.key !== "Tab") return;

  const items = [...ui.detailPanel.querySelectorAll(FOCUSABLE)].filter(
    (n) => n.offsetParent !== null
  );
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

// -- keyboard ----------------------------------------------------------------
//
// A dispatcher console where every action needs a mouse is a console that
// slows down exactly when things are busy. These are the four actions that
// matter during an escalation, on keys that do not collide with browser
// defaults and are not modifier chords.

const SHORTCUTS = [
  ["j / ↓", "next trip"],
  ["k / ↑", "previous trip"],
  ["Enter", "open detail"],
  ["r", "resolve (asks first)"],
  ["c", "copy handoff brief"],
  ["Esc", "close"],
  ["?", "this list"],
];

function orderedRows() {
  return [...ui.tripList.querySelectorAll(".trip-row")];
}

function moveFocus(delta) {
  const rows = orderedRows();
  if (rows.length === 0) return;
  let idx = rows.findIndex((r) => r === document.activeElement);
  if (idx === -1) idx = state.focusedIndex;
  const next = Math.max(0, Math.min(rows.length - 1, idx + delta));
  state.focusedIndex = next;
  rows[next].focus();
  rows[next].scrollIntoView({ block: "nearest" });
}

function toggleShortcutHint() {
  ui.shortcutHint.hidden = !ui.shortcutHint.hidden;
}

function isTypingTarget(node) {
  if (!node) return false;
  const tag = node.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable;
}

document.addEventListener("keydown", (e) => {
  // Never steal a key from the backend URL field or the zone picker.
  if (isTypingTarget(e.target)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  if (e.key === "?" ) {
    e.preventDefault();
    toggleShortcutHint();
    return;
  }

  // Inside the detail panel, only the panel's own actions apply — the trip
  // list behind it is not what the dispatcher is looking at.
  if (!ui.detailPanel.hidden) {
    if (e.key === "r" && !ui.detailResolve.hidden && !ui.detailResolve.disabled) {
      e.preventDefault();
      ui.detailResolve.click();
    } else if (e.key === "c") {
      e.preventDefault();
      ui.detailHandoff.click();
    }
    return;
  }

  switch (e.key) {
    case "j":
    case "ArrowDown":
      e.preventDefault();
      moveFocus(1);
      break;
    case "k":
    case "ArrowUp":
      e.preventDefault();
      moveFocus(-1);
      break;
    case "Enter": {
      const row = document.activeElement;
      if (row && row.classList.contains("trip-row")) {
        e.preventDefault();
        row.click();
      }
      break;
    }
    case "r": {
      const row = document.activeElement;
      if (row && row.classList.contains("trip-row")) {
        e.preventDefault();
        // Opens the detail panel, which is where resolve lives — and
        // where the confirmation step is. A one-key resolve straight from
        // the list would be exactly the misclick the confirmation exists
        // to prevent, just faster.
        row.click();
      }
      break;
    }
    case "Escape":
      if (!ui.shortcutHint.hidden) toggleShortcutHint();
      break;
  }
});

ui.shortcutToggle.addEventListener("click", toggleShortcutHint);

function renderShortcutHint() {
  const list = ui.shortcutHint.querySelector("dl");
  while (list.firstChild) list.removeChild(list.firstChild);
  for (const [key, what] of SHORTCUTS) {
    list.appendChild(el("dt", null, key));
    list.appendChild(el("dd", null, what));
  }
}

// -- polling ------------------------------------------------------------------

function renderFreshness() {
  if (!state.lastPollAt) {
    setText(ui.lastUpdated, "—");
    return;
  }
  const secs = Math.round((Date.now() - state.lastPollAt) / 1000);
  // A small dot going grey was the only staleness signal here. On a screen
  // whose entire job is telling somebody whether a situation is current,
  // "updated 4s ago" is worth the eight characters it costs.
  ui.lastUpdated.textContent = state.lastPollOk
    ? secs < 5
      ? "updated just now"
      : `updated ${secs}s ago`
    : `stale — ${secs}s since last contact`;
  ui.lastUpdated.classList.toggle("is-stale", !state.lastPollOk || secs > 15);
}

async function pollTrips() {
  try {
    const trips = await api("/dashboard/trips");
    state.trips = trips;
    state.lastPollOk = true;
    state.lastPollAt = Date.now();
    ui.liveIndicator.classList.remove("stale");
    checkForNewEscalations(trips);
    renderList();
    renderTripMarkers();
  } catch (e) {
    state.lastPollOk = false;
    ui.liveIndicator.classList.add("stale");
    console.error("poll failed", e);
  }
  renderFreshness();
}

let pollTimer = null;
let freshnessTimer = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollTrips, POLL_MS);
  freshnessTimer = setInterval(renderFreshness, 1000);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (freshnessTimer) clearInterval(freshnessTimer);
  pollTimer = freshnessTimer = null;
}

// A hidden tab polled forever. Browsers throttle background timers anyway,
// which made the "live" indicator lie about freshness rather than saving
// anything; this stops cleanly and catches up on return.
document.addEventListener("visibilitychange", async () => {
  if (!state.bootstrapped) return;
  if (document.hidden) {
    // Snapshot before going quiet, so the diff on return is against what
    // the dispatcher actually last saw.
    state.seen = snapshotTrips();
    stopPolling();
  } else {
    await pollTrips();
    state.awayChanges = changeSince();
    renderChangeBanner();
    renderList();
    startPolling();
  }
});

// The tab can stay visible while the dispatcher is not — another monitor,
// a phone call. Window focus is the better proxy for "eyes on this", and
// it is free.
window.addEventListener("blur", () => {
  if (state.bootstrapped && !document.hidden) state.seen = snapshotTrips();
});
window.addEventListener("focus", async () => {
  if (!state.bootstrapped || document.hidden) return;
  await pollTrips();
  state.awayChanges = changeSince();
  renderChangeBanner();
  renderList();
});

async function bootstrap() {
  stopPolling();
  while (ui.mapOverlay.firstChild) ui.mapOverlay.removeChild(ui.mapOverlay.firstChild);
  rowNodes.forEach((n) => n.row.remove());
  rowNodes.clear();
  renderMuteToggle();
  renderShortcutHint();
  markerNodes.forEach((n) => {
    n.wrap.remove();
    n.line.remove();
  });
  markerNodes.clear();
  try {
    await loadCorridor();
  } catch (e) {
    setText(ui.zoneLabel, "Could not reach backend");
    console.error("bootstrap failed", e);
  }
  await pollTrips();
  await refreshHistory();
  markSeen();
  state.bootstrapped = true;
  if (!document.hidden) startPolling();
}

bootstrap();
