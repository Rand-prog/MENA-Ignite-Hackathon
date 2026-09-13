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

/** True when this page is being served from somewhere other than the
 *  reviewer's own machine — a GitHub Pages copy, say. The console is a pure
 *  client, so a hosted copy is fully functional and has nothing to talk to;
 *  it should say the second part rather than looking broken. */
const IS_HOSTED =
  location.protocol === "https:" &&
  !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

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
  // trip_id -> {elapsedMin, at}. The backend's elapsed_min is three
  // seconds stale by the time the next poll lands, which is fine for a
  // number nobody watches and wrong for one that counts up in front of
  // the dispatcher. Rows interpolate from this base every second and are
  // corrected on each poll. See tripElapsed().
  clocks: new Map(),
  // The queue's true order, held back while the pointer is in the list.
  // See listIsHot() / renderList().
  pendingOrder: null,
  pendingUrgent: false,
  pendingHeld: 0,
  // /dashboard/api-activity: the per-API roll-up and the recent call feed.
  // Polled on a slower cadence than the queue (see API_EVERY) because
  // nothing on this screen needs to act on it within 3 seconds.
  apiActivity: null,
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

// -- formatting --------------------------------------------------------------

/** A duration in minutes, said the way a person would say it.
 *
 *  This dashboard used to print raw minutes everywhere: "1015 min elapsed
 *  / 105 min window". Past about ninety minutes that stops being a
 *  quantity anyone reads and becomes one they convert, which is the wrong
 *  thing to ask of someone deciding whether to dispatch. The Flutter app
 *  already spoke in hours and minutes (see app/lib/screens/
 *  approach_screen.dart's formatLeft) — same product, so the same words. */
function formatMins(mins) {
  if (mins === null || mins === undefined || Number.isNaN(Number(mins))) return "—";
  const total = Math.round(Number(mins));
  if (total < 1) return "under a minute";
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/** A wall-clock time, in the words the app already uses for it (see
 *  formatClock in app/lib/screens/approach_screen.dart). 24-hour and
 *  zero-padded: an emergency centre reads times back over a radio, and
 *  "3:40" is a question there in a way "15:40" is not. */
function formatClock(date) {
  return (
    String(date.getHours()).padStart(2, "0") +
    ":" +
    String(date.getMinutes()).padStart(2, "0")
  );
}

/** The moment a contact would be texted, and how far off it is.
 *
 *  Derived from (window_deadline − server_now) added to *this* machine's
 *  clock, never from window_deadline directly. The demo runs on a virtual
 *  clock that can sit hours from wall time, and printing the raw deadline
 *  would put a nonsense hour in front of a dispatcher who has no way of
 *  telling it is nonsense. Both timestamps arrive naive — no trailing Z —
 *  so they are parsed the same way and only ever subtracted from each
 *  other; converting either side to UTC would shift the delta by this
 *  browser's offset and quietly break the one number this is for. The app
 *  makes the identical move for the identical reason: see
 *  Trip.contactAlertAt in app/lib/models/trip.dart.
 *
 *  Null when the payload is missing either side, or when the moment has
 *  already gone by — a deadline in the past is not a time anything is
 *  still going to happen at, and this system does not guess. */
function contactAlertDue(trip) {
  if (!trip.window_deadline || !trip.server_now) return null;
  const left = new Date(trip.window_deadline) - new Date(trip.server_now);
  if (!Number.isFinite(left) || left <= 0) return null;
  return { clock: formatClock(new Date(Date.now() + left)), mins: left / 60000 };
}

/** How far past the monitoring window a trip is, in minutes, or null. */
function overdueMin(trip, elapsed) {
  const e = elapsed === undefined ? trip.elapsed_min : elapsed;
  if (e == null || !trip.monitoring_window_min) return null;
  const over = e - trip.monitoring_window_min;
  return over > 0 ? over : null;
}

/** Trim a backend "lat, lon" pair to something a person can read aloud.
 *
 *  The backend hands these over at full float precision — 14 decimal
 *  places, or roughly a nanometre, for a position derived from a cell
 *  geofence with an eight-kilometre radius. Five places is about a metre,
 *  which is already more than the signal supports and is short enough to
 *  read. Anything that isn't a coordinate pair passes through untouched. */
function formatCoords(value) {
  if (!value) return null;
  const m = String(value).match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return String(value);
  return `${Number(m[1]).toFixed(5)}, ${Number(m[2]).toFixed(5)}`;
}

/** Great-circle kilometres between two lat/lon pairs. */
function kmBetween(aLat, aLon, bLat, bLon) {
  const R = 6371;
  const rad = Math.PI / 180;
  const dLat = (bLat - aLat) * rad;
  const dLon = (bLon - aLon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** How far a network location fix sits from the corridor it belongs to, or
 *  null when either half is unknown.
 *
 *  Worth checking rather than assuming. A fix that is hundreds of
 *  kilometres from the gate the traveller crossed is not a position, it is
 *  a fault — the Nokia sandbox's shared simulator device reports a fixed
 *  European location regardless of which zone is armed, and on a live
 *  operator connection the same reading would mean a mis-provisioned line
 *  or the wrong MSISDN. Either way a console that prints it under "last
 *  known location" without comment is handing a search team a coordinate
 *  nobody should drive to. */
function fixOffCorridorKm(trip, value) {
  const zone = zoneFor(trip);
  if (!zone || !value) return null;
  const m = String(value).match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  const d = Math.min(
    kmBetween(lat, lon, zone.entry_gate.lat, zone.entry_gate.lon),
    kmBetween(lat, lon, zone.exit_gate.lat, zone.exit_gate.lon)
  );
  // Generous: a real fix can legitimately land a cell-sector's distance
  // outside the gate circle, and on a 100 km corridor the far gate is far.
  return d > 50 ? Math.round(d) : null;
}

/** Stable perpendicular lane for a trip's marker and cone, 0-indexed from
 *  the corridor centreline.
 *
 *  Convoy trips sit on the same line and merge into one indistinguishable
 *  bar, so they are fanned out. The offset used to be the trip's index in
 *  the poll response — which meant every remaining marker hopped to a new
 *  lane the moment any trip closed and the array shifted under it. On a
 *  map whose whole caveat is "this is an estimate, not a live feed",
 *  inventing sideways movement is the one artefact it cannot afford. A
 *  hash of the trip id never moves. */
function laneFor(tripId) {
  let h = 0;
  for (let i = 0; i < tripId.length; i++) {
    h = (Math.imul(h, 31) + tripId.charCodeAt(i)) >>> 0;
  }
  return (h % 5) - 2;
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
  apiChip: el_("api-chip"),
  apiChipText: el_("api-chip-text"),
  apiPanel: el_("api-panel"),
  apiWindow: el_("api-window"),
  apiLead: el_("api-lead"),
  apiList: el_("api-list"),
  apiFeed: el_("api-feed"),
  apiFeedLead: el_("api-feed-lead"),
  apiCalls: el_("api-calls"),
  detailApiBlock: el_("detail-api-block"),
  detailApiLead: el_("detail-api-lead"),
  detailApiCalls: el_("detail-api-calls"),
  zoneSelect: el_("zone-select"),
  zoneMapNote: el_("zone-map-note"),
  changeBanner: el_("change-banner"),
  changeBannerBody: el_("change-banner-body"),
  changeBannerDismiss: el_("change-banner-dismiss"),
  orderPending: el_("order-pending"),
  mapPanel: document.querySelector(".map-panel"),
  mapToggle: el_("map-toggle"),
  mapCollapse: el_("map-collapse"),
  shortcutHint: el_("shortcut-hint"),
  shortcutToggle: el_("shortcut-toggle"),
  detailHandoff: el_("detail-handoff"),
  detailPanel: el_("detail-panel"),
  detailClose: el_("detail-close"),
  detailStatus: el_("detail-status"),
  detailName: el_("detail-name"),
  detailMsisdn: el_("detail-msisdn"),
  detailGone: el_("detail-gone"),
  detailEntry: el_("detail-entry"),
  detailEntryLabel: el_("detail-entry-label"),
  detailLastKnown: el_("detail-lastknown"),
  detailLastKnownRow: el_("detail-lastknown-row"),
  detailRecordBlock: el_("detail-record-block"),
  detailRecordLead: el_("detail-record-lead"),
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

/** Create the AudioContext and ask the browser to let it run.
 *
 *  Browsers hand back a *suspended* context when no user gesture has
 *  happened yet, and a suspended context's currentTime does not advance —
 *  so the first escalation of a shift, which is the one nobody is expecting,
 *  was scheduled into a clock that was not moving and made no sound. The
 *  only unlock path used to be the mute button, and clicking that from the
 *  default unmuted state *mutes* alerts, so audio genuinely only ever came
 *  up if the dispatcher pressed the toggle twice.
 *
 *  Any click or keypress anywhere on the page counts as the gesture, and a
 *  dispatcher console gets one within seconds of being opened. Always
 *  resolves — the caller must never have to guard against audio failing. */
function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "running") return Promise.resolve();
    return Promise.resolve(audioCtx.resume()).then(renderMuteToggle, (e) => {
      console.warn("audio could not be unlocked", e);
      renderMuteToggle();
    });
  } catch (e) {
    console.warn("alert tone unavailable", e);
    return Promise.resolve();
  }
}

// Both, because a keyboard-driven dispatcher may never generate a pointer
// event and vice versa. unlockAudio() is idempotent, so whichever fires
// second is a no-op.
document.addEventListener("pointerdown", unlockAudio, { once: true });
document.addEventListener("keydown", unlockAudio, { once: true });

/** Two-tone alert, synthesised rather than shipped as an audio file so the
 *  dashboard keeps its no-assets, no-build-step property. */
function playAlert() {
  if (state.alertsMuted) return;
  try {
    unlockAudio();
    // Nothing is scheduled onto a context the browser has not allowed to
    // run. Queueing anyway is worse than silence: every escalation would
    // stack another pair of oscillators at a currentTime that never moves,
    // and the moment the dispatcher finally clicked something they would
    // all fire at once. Say so on the mute button instead.
    if (!audioCtx || audioCtx.state !== "running") {
      renderMuteToggle();
      return;
    }
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

/** The title to sit at when nothing is flashing.
 *
 *  The flash burst is an arrival signal and it is over in eight seconds.
 *  The escalation is not: it is still unresolved, still a person in a dead
 *  zone, and this tab is very often not the front one. Once the burst
 *  finished, a background tab holding a Tier 2 read exactly like an idle
 *  one — and the tab strip is the only part of this console a dispatcher
 *  can see while they are on the phone. */
function titleFor(n) {
  return n > 0 ? `(${n}) ⚠ ${BASE_TITLE}` : BASE_TITLE;
}

/** Red trips as of the last poll. checkForNewEscalations() rewrites
 *  state.redSeen before anything else, so this is current even mid-burst. */
function redCount() {
  return state.redSeen.size;
}

function applyTitleBadge() {
  // A running burst owns the title. Without this the two write over each
  // other every 700ms and the burst — which is the louder signal, and the
  // one the dispatcher is meant to catch — loses half its frames.
  if (titleFlashTimer !== null) return;
  document.title = titleFor(redCount());
}

function flashTitle(count) {
  clearInterval(titleFlashTimer);
  let on = false;
  let left = 12;
  titleFlashTimer = setInterval(() => {
    // The off-beat and the ending are the badge, never BASE_TITLE. A poll
    // landing mid-burst updates the count but cannot repaint the title, so
    // if this restored the plain title the badge would be lost until the
    // *next* trip escalated — which for a queue that never grows again is
    // forever.
    document.title = on ? titleFor(redCount()) : `⚠ ${count} NEED ACTION`;
    on = !on;
    if (--left <= 0) {
      clearInterval(titleFlashTimer);
      titleFlashTimer = null;
      document.title = titleFor(redCount());
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
  // Every poll, not just on a transition. A trip resolved from another
  // console, or one that was already red before this tab was opened, both
  // have to move the badge — and neither is a "fresh" escalation.
  applyTitleBadge();
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
  // Unmuting plays the tone back so the dispatcher knows it works. The
  // click itself is a user gesture, which makes it the one reliable moment
  // to lift a suspended context — but resume() settles asynchronously, so
  // wait for it rather than sounding into a clock that has not started.
  if (!state.alertsMuted) {
    unlockAudio().then(() => {
      renderMuteToggle();
      playAlert();
    });
  }
});

function renderMuteToggle() {
  // Three states, not two. A browser that has created the context but not
  // allowed it to run produces exact silence, and this button used to read
  // "🔔 Alerts on" the whole time it did — the worst possible label, since
  // it tells a dispatcher they will be told and they will not be.
  const blocked =
    !state.alertsMuted && audioCtx !== null && audioCtx.state !== "running";
  ui.muteToggle.textContent = state.alertsMuted
    ? "🔇 Alerts off"
    : blocked
      ? "🔕 Alerts blocked"
      : "🔔 Alerts on";
  ui.muteToggle.setAttribute("aria-pressed", state.alertsMuted ? "true" : "false");
  ui.muteToggle.title = state.alertsMuted
    ? "Escalation tone is off — click to enable"
    : blocked
      ? "This browser has not allowed sound on the page yet, so the escalation tone would be silent. Click here to enable it."
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

  trips.forEach((trip) => {
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
    //
    // Keyed to the trip id, never to its index in this array: see laneFor().
    const lane = laneFor(trip.trip_id);
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
  const overdue = el("div", "trip-overdue");
  const meta = el("div", "trip-meta");
  main.append(name, overdue, meta);
  const label = el("span", "trip-state-label");

  row.append(dot, main, label);
  row.addEventListener("click", () => openDetail(trip.trip_id));
  return { row, dot, name, overdue, meta, label };
}

/** Elapsed minutes for a trip, interpolated between polls.
 *
 *  The backend's elapsed_min is up to three seconds stale by the time the
 *  next poll replaces it. That is invisible on a number nobody watches and
 *  obvious on one that counts up in front of a dispatcher, so rows
 *  interpolate from the last poll and get corrected by the next. */
function tripElapsed(trip) {
  const base = state.clocks.get(trip.trip_id);
  if (!base) return trip.elapsed_min;
  return base.elapsedMin + (Date.now() - base.at) / 60000;
}

/** Record the clock base for every trip in a fresh poll response. */
function syncClocks(trips) {
  const now = Date.now();
  const live = new Set();
  for (const t of trips) {
    live.add(t.trip_id);
    if (t.elapsed_min != null) state.clocks.set(t.trip_id, { elapsedMin: t.elapsed_min, at: now });
  }
  for (const id of [...state.clocks.keys()]) {
    if (!live.has(id)) state.clocks.delete(id);
  }
}

function rowText(trip, elapsed) {
  const parts = [];
  if (elapsed == null) {
    parts.push("—");
  } else if (elapsed < 1) {
    parts.push("just entered");
  } else {
    parts.push(
      formatMins(elapsed) + " elapsed" +
        (trip.monitoring_window_min
          ? " of a " + formatMins(trip.monitoring_window_min) + " window"
          : "")
    );
  }
  if (trip.planned_stop_min) {
    parts.push("+" + formatMins(trip.planned_stop_min) + " declared stop");
  }
  // A trip still in BUFFER has no congestion reading yet. "— traffic" is
  // not a fact about the road, it is a placeholder leaking into a row a
  // dispatcher is meant to read at a glance.
  if (trip.congestion_tier) parts.push(trip.congestion_tier + " traffic");
  return parts.join(" · ");
}

/** The headline for a row whose window is running out, or has run out.
 *
 *  Overdue-by used to be left as arithmetic for the reader — the row said
 *  "1015 min elapsed / 105 min window" and the dispatcher did the
 *  subtraction. This is the answer, and it counts up. */
function rowOverdueText(trip, elapsed) {
  const over = overdueMin(trip, elapsed);
  if (over != null) {
    return over < 1 ? "Just went overdue" : "Overdue by " + formatMins(over);
  }
  if (elapsed != null && trip.monitoring_window_min) {
    const left = trip.monitoring_window_min - elapsed;
    if (left > 0) {
      return left < 1
        ? "Contacts told in seconds"
        : formatMins(left) + " before contacts are told";
    }
  }
  return "";
}

/** Refresh only the numbers that move between polls.
 *
 *  Runs once a second. Deliberately touches text nodes and nothing else,
 *  so it can never reorder or rebuild a row out from under a click. */
function renderRowClocks() {
  if (state.view !== "live") return;
  const byId = new Map(state.trips.map((t) => [t.trip_id, t]));
  for (const [id, node] of rowNodes) {
    const trip = byId.get(id);
    if (!trip) continue;
    const elapsed = tripElapsed(trip);
    setText(node.meta, rowText(trip, elapsed));
    node.overdue.textContent = rowOverdueText(trip, elapsed);
  }
}

// -- held reordering ---------------------------------------------------------
//
// The queue re-sorts by severity on every poll and moves the DOM nodes to
// match. That is right when nobody is touching the list and wrong the
// moment somebody is: a row that slides out from under a cursor between
// mousedown and mouseup opens a different traveller than the one that was
// clicked, and the panel it opens has "Mark Resolved" in it. So while the
// pointer is inside the list, a row holds focus, or the detail panel is
// open, the new order is computed but not applied — and the fact that it
// is being held is stated rather than left silently true.

let listPointerInside = false;

function listIsHot() {
  return (
    listPointerInside ||
    ui.tripList.contains(document.activeElement) ||
    !ui.detailPanel.hidden
  );
}

ui.tripList.addEventListener("pointerenter", () => {
  listPointerInside = true;
});
ui.tripList.addEventListener("pointerleave", () => {
  listPointerInside = false;
  if (state.pendingOrder) renderList();
});
ui.tripList.addEventListener("focusout", () => {
  // focusout fires before the new focus lands, so re-check on the next tick.
  window.setTimeout(() => {
    if (!listIsHot() && state.pendingOrder) renderList();
  }, 0);
});

ui.orderPending.addEventListener("click", () => {
  listPointerInside = false;
  renderList(true);
  ui.tripList.scrollTop = 0;
});

function renderOrderPending() {
  if (!state.pendingOrder) {
    ui.orderPending.hidden = true;
    return;
  }
  ui.orderPending.hidden = false;
  ui.orderPending.classList.toggle("is-urgent", state.pendingUrgent);
  // A held row is counted in "N monitored" but not yet drawn, so say how
  // many are waiting rather than leaving the discrepancy to be noticed.
  const held = state.pendingHeld;
  const waiting = held
    ? ` — ${held} new trip${held === 1 ? "" : "s"} waiting`
    : "";
  ui.orderPending.textContent = state.pendingUrgent
    ? "A trip needs action and is not at the top — click to re-sort the queue"
    : !ui.detailPanel.hidden
      ? `Queue order held while this trip is open${waiting} — it re-sorts on close`
      : `Queue order paused while you're in the list${waiting} — click to re-sort`;
}

// -- group headers -----------------------------------------------------------
//
// Severity sorting alone left the escalated trip as row one of an
// otherwise identical list, which reads as "first" rather than as
// "different". A header names the group and carries its count.

const GROUP_LABEL = {
  red: "Needs action",
  amber: "Window expiring",
  green: "In transit",
};

const groupNodes = new Map(); // severity -> {head, count}

function groupHead(sev) {
  let node = groupNodes.get(sev);
  if (!node) {
    const head = el("div", "group-head for-" + sev);
    const label = el("span", null, GROUP_LABEL[sev]);
    const count = el("span", "group-count");
    head.append(label, count);
    node = { head, count };
    groupNodes.set(sev, node);
  }
  return node;
}

function renderList(force) {
  const trips =
    state.view === "live" ? inActiveZone(state.trips) : inActiveZone(state.history);

  setText(
    ui.tripCount,
    state.view === "live"
      ? trips.length + " monitored"
      : trips.length + " completed"
  );
  // "No trips in the corridor right now" is a statement about the corridor,
  // and a failing poll is not evidence for it. On a cold load against a
  // backend that is down, the queue is empty for a reason that has nothing
  // to do with the road, so the offline copy takes the live copy's place —
  // silence here has to mean "quiet", never "not listening".
  const offlineEmpty = state.lastPollOk === false && state.view === "live";
  ui.emptyState.classList.toggle("visible", trips.length === 0);
  ui.emptyState.querySelector("[data-empty-live]").hidden =
    state.view !== "live" || offlineEmpty;
  ui.emptyState.querySelector("[data-empty-history]").hidden = state.view === "live";
  ui.emptyState.querySelector("[data-empty-offline]").hidden = !offlineEmpty;
  // The hosted copy has no backend to reach by default, which is a
  // different situation from a dispatcher whose backend just went down.
  ui.emptyState.querySelector("[data-empty-hosted]").hidden =
    !offlineEmpty || !IS_HOSTED;

  const sorted = [...trips].sort((a, b) => {
    if (state.view === "history") {
      return String(b.entered_at || "").localeCompare(String(a.entered_at || ""));
    }
    const sevDiff = SEVERITY_ORDER[classify(a)] - SEVERITY_ORDER[classify(b)];
    if (sevDiff !== 0) return sevDiff;
    return (b.elapsed_min || 0) - (a.elapsed_min || 0);
  });

  // Content first. Every row's text, colour and class is brought current on
  // every poll whether or not its position is allowed to move — holding the
  // order back must never mean holding the facts back.
  const seen = new Set();
  for (const trip of sorted) {
    seen.add(trip.trip_id);
    let node = rowNodes.get(trip.trip_id);
    if (!node) {
      node = buildRow(trip);
      rowNodes.set(trip.trip_id, node);
    }
    const sev = classify(trip);
    node.row.className =
      "trip-row state-" + sev +
      (trip.trip_id === state.selectedTripId ? " selected" : "");
    const elapsed = state.view === "live" ? tripElapsed(trip) : trip.elapsed_min;
    setText(node.name, trip.traveller_name || "Unknown traveller");
    node.overdue.textContent =
      state.view === "live" ? rowOverdueText(trip, elapsed) : "";
    setText(node.meta, rowText(trip, elapsed));
    setText(node.label, stateLabel(trip));
    // Marks rows that moved while the dispatcher was not looking — see
    // markSeen(). Cleared as soon as the banner is dismissed.
    node.row.classList.toggle(
      "changed-while-away",
      state.awayChanges.some((c) => c.trip_id === trip.trip_id)
    );
  }

  for (const [id, node] of rowNodes) {
    if (!seen.has(id)) {
      node.row.remove();
      rowNodes.delete(id);
    }
  }

  // The sequence the list should be in: a header per non-empty severity
  // group, then that group's rows. History is one flat, time-ordered list —
  // "Needs action" is not something a closed trip can be.
  const wanted = [];
  if (state.view === "live") {
    for (const sev of ["red", "amber", "green"]) {
      const group = sorted.filter((t) => classify(t) === sev);
      if (group.length === 0) continue;
      const head = groupHead(sev);
      setText(head.count, "(" + group.length + ")");
      wanted.push(head.head);
      for (const t of group) wanted.push(rowNodes.get(t.trip_id).row);
    }
  } else {
    for (const t of sorted) wanted.push(rowNodes.get(t.trip_id).row);
  }

  const current = [...ui.tripList.children];
  const same =
    current.length === wanted.length && current.every((n, i) => n === wanted[i]);

  // Holding is about not moving rows out from under a pointer. It is never
  // about keeping a group header that no longer has a group, so an emptied
  // queue applies immediately and headers whose group is gone are dropped
  // even while the rest of the order waits.
  if (!same && !force && listIsHot() && current.length > 0 && sorted.length > 0) {
    for (const node of current) {
      if (node.classList.contains("group-head") && !wanted.includes(node)) {
        node.remove();
      }
    }
    state.pendingOrder = wanted;
    state.pendingHeld = wanted.filter(
      (n) => n.classList.contains("trip-row") && !current.includes(n)
    ).length;
    // Urgent means "a trip needs action and the queue in front of you does
    // not show it that way" — either its row is being held out of the list
    // entirely, or it is sitting below a calmer one. Comparing the first
    // node of each sequence would not catch either case: the first node is
    // a group header, and headers are cached singletons that stay
    // identical across a re-sort.
    const redIds = new Set(
      sorted.filter((t) => classify(t) === "red").map((t) => t.trip_id)
    );
    let sawCalm = false;
    let redBelowCalm = false;
    for (const node of current) {
      if (!node.classList.contains("trip-row")) continue;
      if (redIds.has(node.dataset.tripId)) {
        if (sawCalm) redBelowCalm = true;
      } else {
        sawCalm = true;
      }
    }
    const redHeldOut = [...redIds].some(
      (id) => !current.includes(rowNodes.get(id).row)
    );
    state.pendingUrgent = redBelowCalm || redHeldOut;
    renderOrderPending();
    return;
  }

  for (let i = 0; i < wanted.length; i++) {
    if (ui.tripList.children[i] !== wanted[i]) {
      ui.tripList.insertBefore(wanted[i], ui.tripList.children[i] || null);
    }
  }
  for (const node of [...ui.tripList.children]) {
    if (!wanted.includes(node)) node.remove();
  }
  state.pendingOrder = null;
  state.pendingUrgent = false;
  state.pendingHeld = 0;
  renderOrderPending();
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
  // nodes belong to the new list. The severity headers go with them —
  // history has no severity groups, and a header left behind would sit
  // above a list it no longer describes.
  rowNodes.forEach((n) => n.row.remove());
  rowNodes.clear();
  groupNodes.forEach((n) => n.head.remove());
  state.pendingOrder = null;
  renderList();
  renderTripMarkers();
  if (view === "history") refreshHistory();
}

// -- map disclosure (narrow viewports) ---------------------------------------
//
// Stacked into one column, the map panel put roughly a full screen of
// terrain, legend, caveat and histogram above the trip queue — which is
// the thing the page exists to show. The queue now comes first (see the
// max-width:860px block in style.css) and the map starts collapsed, so
// the corridor is one tap away rather than in the way. The choice is
// remembered, because a dispatcher who wants the map open wants it open
// on every reload.

const MAP_NARROW = window.matchMedia("(max-width: 860px)");

function applyMapCollapse() {
  const narrow = MAP_NARROW.matches;
  const stored = localStorage.getItem("sg_map_open");
  const open = narrow ? stored === "1" : true;
  ui.mapPanel.classList.toggle("is-collapsed", !open);
  ui.mapToggle.setAttribute("aria-expanded", open ? "true" : "false");
  ui.mapToggle.textContent = open ? "Hide map" : "Show map";
}

ui.mapToggle.addEventListener("click", () => {
  const open = ui.mapToggle.getAttribute("aria-expanded") === "true";
  localStorage.setItem("sg_map_open", open ? "0" : "1");
  applyMapCollapse();
  // The overlay is positioned in percentages of a frame that was display:
  // none a moment ago, so nothing needs recomputing — but the markers do
  // need to exist, and a zone switch while collapsed may have dropped them.
  if (!open) renderTripMarkers();
});

MAP_NARROW.addEventListener("change", applyMapCollapse);
applyMapCollapse();

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

/** The open panel's address. A dispatcher handing a crossing to a
 *  colleague, or to a shift that starts in an hour, has had one way to do
 *  it — "Copy handoff brief", which produces text. This produces the
 *  screen: paste the URL and the other person is looking at the same trip
 *  rather than hunting for a name in a queue that has re-sorted since.
 *
 *  Written with replaceState, not pushState: the panel is a view of a row,
 *  not a navigation step, and stacking history entries would turn Back
 *  into "close the panel I opened three trips ago". */
function syncHash(tripId) {
  const want = tripId ? `#trip/${encodeURIComponent(tripId)}` : "";
  if ((location.hash || "") === want) return;
  history.replaceState(null, "", location.pathname + location.search + want);
}

function tripIdFromHash() {
  const m = /^#trip\/(.+)$/.exec(location.hash || "");
  return m ? decodeURIComponent(m[1]) : null;
}

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
    syncHash(tripId);
  } catch (e) {
    console.error("failed to load trip detail", e);
    announce("Could not load trip detail.", false);
  }
}

/** Render a coordinate pair with a copy control.
 *
 *  A dispatcher reading a position to a search team over a radio needs it
 *  short; one pasting it into a mapping tool needs it exact. Five decimal
 *  places is both. */
function fillCoordField(dd, value, note) {
  while (dd.firstChild) dd.removeChild(dd.firstChild);
  if (!value) {
    dd.textContent = "—";
    return;
  }
  const wrap = el("span", "coord-value");
  wrap.appendChild(el("span", "coord-text", value));
  const btn = el("button", "copy-coord", "Copy");
  btn.type = "button";
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      btn.textContent = "Copied";
    } catch (err) {
      // Clipboard needs a secure context; a plain-http dashboard on a LAN
      // does not have one. Select the text so a manual copy still works.
      const range = document.createRange();
      range.selectNodeContents(wrap.firstChild);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      btn.textContent = "Selected";
    }
    window.setTimeout(() => {
      btn.textContent = "Copy";
    }, 1800);
  });
  wrap.appendChild(btn);
  dd.appendChild(wrap);
  if (note) dd.appendChild(el("p", "coord-note", note));
}

// -- the fields that go stale under an open panel ----------------------------
//
// Factored out of fillDetail() so that it and refreshOpenDetail() below
// cannot drift apart. Each of these writes exactly one node and touches
// nothing else — no disclosure state, no button state, nothing focusable —
// because refreshOpenDetail() calls them under the dispatcher's hands.

// The trip state the open panel was last rendered against, so a refresh can
// tell "nothing moved" from "this trip escalated while you were reading it".
let detailRenderedState = null;

function fillDetailStatus(trip) {
  setText(ui.detailStatus, stateLabel(trip));
  ui.detailStatus.className = `status-pill state-${classify(trip)}`;
}

function fillDetailPredicted(trip) {
  const elapsed = formatMins(trip.elapsed_min);
  const predicted = formatMins(trip.predicted_crossing_min);
  const win = formatMins(trip.monitoring_window_min);
  const over = overdueMin(trip);
  setText(
    ui.detailPredicted,
    `${elapsed} elapsed · predicted ${predicted} · window ${win}` +
      (over != null ? ` · overdue by ${formatMins(over)}` : "")
  );
}

function fillDetailAlert(trip) {
  const notes = trip.notifications || [];
  const human = notes.filter((n) => n !== "tier0");
  if (human.length > 0) {
    setText(
      ui.detailAlert,
      human.includes("tier2")
        ? "Contact + emergency centre alerted"
        : "Contact alerted (Tier 1)"
    );
    return;
  }

  // Past tense alone answers "has anyone been told?" and leaves the
  // question a dispatcher deciding whether to act actually has — "when does
  // somebody get told?" — as arithmetic against the window figure two rows
  // up. The moment is already in the payload, so say it.
  const told = notes.includes("tier0")
    ? "Traveller asked directly — no human contacted"
    : "Not notified";
  const due = contactAlertDue(trip);
  setText(
    ui.detailAlert,
    due === null
      ? told
      : `${told} — contact is texted at ${due.clock} unless they're out (in ${formatMins(due.mins)})`
  );
}

function fillDetail(trip) {
  fillDetailStatus(trip);
  setText(ui.detailName, trip.traveller_name || "Unknown traveller");
  setText(ui.detailMsisdn, trip.traveller_msisdn);
  // The network has one location fix per trip, taken at the entry gate, so
  // on most trips these two fields are the same coordinate pair printed
  // twice. Say it once and say why.
  const entry = formatCoords(trip.entry_point);
  const lastKnown = formatCoords(trip.last_known_location);
  const sameFix = entry !== null && entry === lastKnown;
  fillCoordField(ui.detailEntry, entry);
  setText(
    ui.detailEntryLabel,
    sameFix ? "Entry point — also the last known fix" : "Entry point"
  );
  ui.detailLastKnownRow.hidden = sameFix;
  if (!sameFix) {
    fillCoordField(ui.detailLastKnown, lastKnown);
  }

  fillDetailPredicted(trip);

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

  fillDetailAlert(trip);
  setText(
    ui.detailConvoy,
    trip.convoy_id
      ? `In a convoy — see the escalation record for a peer who already came through`
      : "Travelling alone"
  );

  if (trip.decision_record) {
    renderDecisionRecord(ui.detailRecord, trip.decision_record);
    // The reasoning line is the one that justifies the risk score, so it
    // is the summary — readable without opening the disclosure at all.
    const reasoning = parseAgentRecord(trip.decision_record).find(
      (r) => r.key === "reasoning"
    );
    setText(ui.detailRecordLead, reasoning ? reasoning.value : "");
    if (!reasoning) ui.detailRecordLead.textContent = "";
  } else {
    while (ui.detailRecord.firstChild) ui.detailRecord.removeChild(ui.detailRecord.firstChild);
    ui.detailRecord.appendChild(el("p", "record-empty", "No decision record yet."));
    ui.detailRecordLead.textContent = "";
  }
  // Opens closed on every trip, so the panel always starts at the same
  // height and the pinned actions are always where they were last time.
  ui.detailRecordBlock.open = false;

  if (trip.escalation_record) {
    renderEscalationRecord(ui.detailEscalation, trip.escalation_record);
    ui.detailEscalationBlock.hidden = false;
  } else {
    ui.detailEscalationBlock.hidden = true;
  }

  // Provenance. Present only on /dashboard/trips/{id} — the queue poll
  // does not carry it, so on a refresh from the queue the list already on
  // screen is left alone rather than being blanked.
  if (Array.isArray(trip.api_calls)) {
    const camara = trip.api_calls.filter((c) => c.camara);
    setText(
      ui.detailApiLead,
      camara.length
        ? `${camara.length} call${camara.length === 1 ? "" : "s"} to ${
            new Set(camara.map((c) => c.api)).size
          } CAMARA API${new Set(camara.map((c) => c.api)).size === 1 ? "" : "s"}`
        : "none recorded"
    );
    renderApiCallList(
      ui.detailApiCalls,
      trip.api_calls,
      "No CAMARA calls attributed to this crossing."
    );
  }
  ui.detailApiBlock.open = false;

  ui.detailHandoff.onclick = () => copyHandoff(trip);
  resetResolveButton(trip);
  detailRenderedState = trip.state;
  ui.detailGone.hidden = true;
}

/** Bring the open panel's volatile fields up to date on each poll.
 *
 *  The panel used to be a photograph. Everything in it was written once, at
 *  the moment it was opened, and `fillDetail()` was never called again —
 *  so a dispatcher who opened a trip and then watched it was reading
 *  open-time numbers on the one screen they had deliberately chosen to look
 *  at, and the pill still said "In transit" minutes after that same trip
 *  had turned red in the list behind the scrim.
 *
 *  Deliberately NOT a re-run of fillDetail(): that would collapse the Agent
 *  Decision Record the dispatcher just opened and reset a resolve
 *  confirmation under their hand, which is a worse bug than the one being
 *  fixed here. Only the fields that can actually change are rewritten. */
function refreshOpenDetail() {
  if (ui.detailPanel.hidden || !state.selectedTripId) return;
  const trip = state.trips.find((t) => t.trip_id === state.selectedTripId);

  if (!trip) {
    // Resolved from another console, or closed because the traveller
    // reached the exit gate. The alternative to saying so is a panel of
    // numbers that quietly stopped moving — indistinguishable from a trip
    // that is simply calm, and the thing a dispatcher would act on.
    ui.detailGone.hidden = false;
    return;
  }
  ui.detailGone.hidden = true;

  fillDetailStatus(trip);
  fillDetailPredicted(trip);
  fillDetailAlert(trip);

  // Resolve is destructive and has a confirmation step in front of it.
  // Rebuilding the button on every poll would wipe a confirmation the
  // dispatcher has already armed — three seconds is not long enough to read
  // "Clear the escalation for Layla Haddad?" and decide — so it is touched
  // only when the trip's state actually moved and nothing is armed.
  if (trip.state !== detailRenderedState && pendingResolveId === null) {
    resetResolveButton(trip);
    detailRenderedState = trip.state;
  }

  // The brief is generated from whatever trip object the handler closed
  // over, so re-point it at the fresh one: a handoff copied ten minutes
  // into an escalation must not carry ten-minute-old elapsed time into
  // somebody else's radio call.
  ui.detailHandoff.onclick = () => copyHandoff(trip);
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
  L.push(`Elapsed:        ${formatMins(trip.elapsed_min)}` +
         `  (predicted ${formatMins(trip.predicted_crossing_min)}, ` +
         `window ${formatMins(trip.monitoring_window_min)})`);
  const handoffOver = overdueMin(trip);
  if (handoffOver != null) {
    L.push(`Overdue by:     ${formatMins(handoffOver)}`);
  }
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
  L.push(`  ${formatCoords(trip.entry_point) || "unavailable"}`);
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
  // "nobody" on its own reads as a decision. It is a countdown, and the
  // person this brief gets pasted to is the one who needs to know how much
  // of it is left before the escalation happens without them.
  const due = notes.length === 0 ? contactAlertDue(trip) : null;
  if (due) {
    L.push(
      `  Contact is texted at ${due.clock} unless they're out (in ${formatMins(due.mins)}).`
    );
  }
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
  ui.detailGone.hidden = true;
  detailRenderedState = null;
  state.selectedTripId = null;
  renderList();
  renderTripMarkers();
  if (detailOpenerEl && document.body.contains(detailOpenerEl)) {
    detailOpenerEl.focus();
  }
  detailOpenerEl = null;
  syncHash(null);
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

// -- network API activity ----------------------------------------------------
//
// What the queue above is actually made of. See api_activity.py for why
// this is on the operator console and not only on the demo page.

// One poll of /dashboard/api-activity per this many queue polls. The call
// log is a thing a dispatcher consults, not a thing they watch, and it
// reads a 24-hour window rather than a handful of live rows.
const API_EVERY = 3;
let apiPollCounter = 0;

/** Backend timestamps are naive UTC by convention (see clock.py), and the
 *  only reason the rest of this file gets away with `new Date(iso)` is
 *  that it always subtracts two backend timestamps, so the browser's
 *  offset cancels. These are compared against Date.now() instead — a real
 *  wall-clock instant, because an HTTP call to Nokia happens at one and
 *  /demo/clock/advance cannot move it — so the zone has to be stated. */
function parseUtc(iso) {
  if (!iso) return null;
  return new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
}

function agoText(iso) {
  const d = parseUtc(iso);
  if (!d || Number.isNaN(d.getTime())) return "";
  const secs = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

/** One call, as a row. Every string here comes from the backend, so all of
 *  it goes in through textContent — see el(). */
function apiCallRow(call) {
  const li = el("li", "api-call");
  // Three outcomes. A rejected call is a fault; a call that never got a
  // response is a fault of a different kind; a channel that was never
  // configured is not a fault at all, and colouring it like one trains a
  // dispatcher to ignore the colour.
  const skipped = call.attempted === false;
  if (!call.ok && !skipped) li.classList.add("is-failed");
  if (skipped) li.classList.add("is-skipped");
  li.appendChild(
    el(
      "span", "api-call-status",
      skipped ? "SKIP" : call.status ? String(call.status) : "ERR"
    )
  );
  li.appendChild(el("span", "api-call-method", call.method || ""));
  const path = el("span", "api-call-path");
  path.appendChild(el("span", "api-call-api", call.api));
  path.appendChild(document.createTextNode(call.path || ""));
  li.appendChild(path);
  li.appendChild(
    el("span", "api-call-meta", `${call.latency_ms} ms · ${agoText(call.ts)}`)
  );
  return li;
}

function renderApiCallList(container, calls, emptyText) {
  while (container.firstChild) container.removeChild(container.firstChild);
  if (!calls || !calls.length) {
    container.appendChild(el("li", "record-empty", emptyText));
    return;
  }
  calls.forEach((c) => container.appendChild(apiCallRow(c)));
}

function renderApiChip(data) {
  const t = data && data.totals;
  if (!t) {
    ui.apiChip.className = "api-chip";
    setText(ui.apiChipText, "CAMARA —");
    return;
  }
  // Three states worth distinguishing, and "no calls yet" is not a fault:
  // a console opened before the first crossing of the day has nothing to
  // report and should not claim a problem.
  const cls = t.failed > 0 ? (t.ok === 0 ? "is-down" : "is-degraded") : t.calls ? "is-ok" : "";
  ui.apiChip.className = "api-chip" + (cls ? " " + cls : "");
  setText(
    ui.apiChipText,
    t.calls
      ? `CAMARA ${t.calls} calls · ${t.apis_used}/${t.apis_total} APIs` +
          (t.failed ? ` · ${t.failed} failed` : "")
      : "CAMARA idle"
  );
}

function renderApiActivity() {
  const data = state.apiActivity;
  renderApiChip(data);
  if (!data) {
    setText(ui.apiLead, "No reading — this console is not in contact with the backend.");
    while (ui.apiList.firstChild) ui.apiList.removeChild(ui.apiList.firstChild);
    return;
  }
  setText(ui.apiWindow, `last ${data.window_hours}h`);
  const t = data.totals;
  setText(
    ui.apiLead,
    t.calls
      ? `${t.calls} live CAMARA calls on Nokia Network as Code · ${t.failed} failed · ` +
        `${t.apis_used} of ${t.apis_total} APIs exercised`
      : "No CAMARA calls in this window. The five APIs below are the ones this corridor runs on."
  );

  while (ui.apiList.firstChild) ui.apiList.removeChild(ui.apiList.firstChild);
  data.apis.forEach((a) => {
    const li = el("li", "api-row");
    li.classList.add(
      a.failed ? "is-failed" : a.calls - (a.skipped || 0) ? "is-ok" : "is-idle"
    );
    li.appendChild(el("span", "api-pip"));
    const name = el("span", "api-name", a.api);
    name.appendChild(el("span", "api-spec", a.camara_spec || a.category));
    li.appendChild(name);
    const count = el("span", "api-count");
    if (a.calls) {
      const attempted = a.calls - (a.skipped || 0);
      count.appendChild(
        document.createTextNode(
          attempted
            ? `${attempted} call${attempted === 1 ? "" : "s"}` +
              (a.avg_ms != null ? ` · ${a.avg_ms} ms` : "")
            : ""
        )
      );
      if (a.failed) count.appendChild(el("span", "api-fail", `${attempted ? " · " : ""}${a.failed} failed`));
      if (a.skipped) {
        count.appendChild(
          el("span", "api-skip", `${attempted || a.failed ? " · " : ""}${a.skipped} not sent`)
        );
      }
    } else {
      count.appendChild(document.createTextNode("not called yet"));
    }
    li.appendChild(count);
    ui.apiList.appendChild(li);
  });

  setText(
    ui.apiFeedLead,
    data.calls.length ? `${data.calls.length} most recent` : "nothing logged yet"
  );
  renderApiCallList(
    ui.apiCalls, data.calls, "No calls logged in this window."
  );
}

async function pollApiActivity() {
  try {
    state.apiActivity = await api("/dashboard/api-activity?limit=25");
  } catch (e) {
    // A failed poll here is not the same as an idle network layer, and
    // must not be drawn as one.
    state.apiActivity = null;
  }
  renderApiActivity();
}

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
    syncClocks(trips);
    state.lastPollOk = true;
    state.lastPollAt = Date.now();
    ui.liveIndicator.classList.remove("stale");
    checkForNewEscalations(trips);
    renderList();
    renderTripMarkers();
    // The open panel is a view onto this same data and has to move with it.
    refreshOpenDetail();
    if (apiPollCounter++ % API_EVERY === 0) pollApiActivity();
  } catch (e) {
    state.lastPollOk = false;
    ui.liveIndicator.classList.add("stale");
    console.error("poll failed", e);
    // The trips already on screen are kept — last known is better than
    // nothing — but the empty state has to switch on the poll that failed,
    // not whenever something else happens to re-render. On a cold load
    // there is nothing else: this is the only call that will ever come.
    renderList();
  }
  renderFreshness();
}

let pollTimer = null;
let freshnessTimer = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollTrips, POLL_MS);
  freshnessTimer = setInterval(() => {
    renderFreshness();
    renderRowClocks();
  }, 1000);
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
  // A link straight to one crossing. Deliberately after the first poll:
  // the panel is a view of a row, so the queue has to exist first.
  const deepLink = tripIdFromHash();
  if (deepLink) await openDetail(deepLink);
}

// A pasted link arriving at a console that is already open is a same-
// document navigation: nothing reloads, so without this the address bar
// would change and the screen would not.
window.addEventListener("hashchange", () => {
  if (!state.bootstrapped) return;
  const id = tripIdFromHash();
  if (id && id !== state.selectedTripId) openDetail(id);
  else if (!id && !ui.detailPanel.hidden) closeDetail();
});

bootstrap();
