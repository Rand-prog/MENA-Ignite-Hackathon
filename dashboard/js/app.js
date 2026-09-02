"use strict";

/* SignalGuard — Emergency Centre dashboard.
 * Vanilla HTML/CSS/JS, no build step — matches the "hackathon prototype,
 * not a product" scope: no auth, no multi-tenancy, no settings beyond the
 * backend URL. See docs/SignalGuard_User_Flow's Emergency Center Flow.
 */

const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const POLL_MS = 3000;

const state = {
  backendUrl: localStorage.getItem("sg_backend_url") || DEFAULT_BACKEND,
  zones: [],
  corridorMeta: null,
  trips: [],
  selectedTripId: null,
  lastPollOk: true,
};

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

function parseLatLon(label) {
  if (!label) return null;
  const parts = label.split(",").map((s) => parseFloat(s.trim()));
  if (parts.length !== 2 || parts.some(Number.isNaN)) return null;
  return { lat: parts[0], lon: parts[1] };
}

// Network location signals are a single snapshot at the entry gate, by
// design — there is no live position feed once a trip goes dark (that's
// the whole premise: the network can't see inside a dead zone either).
// So "where the dot sits" during a live crossing is a straight-line
// estimate along the corridor from elapsed-vs-predicted time, not a GPS
// fix. This also sidesteps the sandbox's fixed simulator-device
// coordinates, which live nowhere near Jordan and would otherwise plot
// every trip off the edge of the map.
function estimatedPosition(trip, zone) {
  if (!zone) return null;
  const denom = trip.predicted_crossing_min || trip.monitoring_window_min || 1;
  let progress = (trip.elapsed_min || 0) / denom;
  progress = Math.max(0, Math.min(1, progress));
  return {
    lat: zone.entry_gate.lat + (zone.exit_gate.lat - zone.entry_gate.lat) * progress,
    lon: zone.entry_gate.lon + (zone.exit_gate.lon - zone.entry_gate.lon) * progress,
  };
}

// -- classification --------------------------------------------------------

function classify(trip) {
  if (["OVERDUE", "TIER1_ALERTED", "TIER2_ESCALATED"].includes(trip.state)) {
    return "red";
  }
  if (trip.state === "BUFFER") return "amber";
  // ACTIVE: amber once meaningfully into the monitoring window.
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
    OVERDUE: "Overdue",
    TIER1_ALERTED: "Contact alerted",
    TIER2_ESCALATED: "Action needed",
  };
  return labels[trip.state] || trip.state;
}

// -- DOM refs ---------------------------------------------------------------

const el = {
  backendInput: document.getElementById("backend-url"),
  liveIndicator: document.getElementById("live-indicator"),
  zoneLabel: document.getElementById("zone-label"),
  mapFrame: document.getElementById("map-frame"),
  mapImage: document.getElementById("map-image"),
  mapOverlay: document.getElementById("map-overlay"),
  mapAttribution: document.getElementById("map-attribution"),
  tripList: document.getElementById("trip-list"),
  tripCount: document.getElementById("trip-count"),
  emptyState: document.getElementById("empty-state"),
  detailPanel: document.getElementById("detail-panel"),
  detailClose: document.getElementById("detail-close"),
  detailStatus: document.getElementById("detail-status"),
  detailName: document.getElementById("detail-name"),
  detailMsisdn: document.getElementById("detail-msisdn"),
  detailEntry: document.getElementById("detail-entry"),
  detailLastKnown: document.getElementById("detail-lastknown"),
  detailPredicted: document.getElementById("detail-predicted"),
  detailBattery: document.getElementById("detail-battery"),
  detailCongestion: document.getElementById("detail-congestion"),
  detailRisk: document.getElementById("detail-risk"),
  detailAlert: document.getElementById("detail-alert"),
  detailRecord: document.getElementById("detail-record"),
  detailResolve: document.getElementById("detail-resolve"),
  scrim: document.getElementById("scrim"),
};

// -- backend URL field -------------------------------------------------------

el.backendInput.value = state.backendUrl;
el.backendInput.addEventListener("change", () => {
  const v = el.backendInput.value.trim().replace(/\/$/, "");
  if (!v) return;
  state.backendUrl = v;
  localStorage.setItem("sg_backend_url", v);
  bootstrap();
});

// -- fetch helpers ------------------------------------------------------------

async function api(path, opts) {
  const resp = await fetch(state.backendUrl + path, opts);
  if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

// -- map rendering ------------------------------------------------------------

async function loadCorridor() {
  const [zones, meta] = await Promise.all([
    api("/zones"),
    fetch("assets/map/corridor.json").then((r) => r.json()),
  ]);
  state.zones = zones;
  state.corridorMeta = meta;

  const zone = zones[0];
  el.zoneLabel.textContent = zone ? zone.label : "No zone configured";
  el.mapAttribution.textContent = meta.attribution;
  // Intrinsic-size hints, not a display size override — CSS still sizes
  // this to fill .map-frame, whose own aspect-ratio (set below) already
  // matches the image, so this only prevents a layout jump before it
  // decodes rather than fighting the responsive sizing.
  el.mapImage.width = meta.width_px;
  el.mapImage.height = meta.height_px;
  el.mapImage.src = "assets/map/corridor.png";
  el.mapFrame.style.aspectRatio = `${meta.width_px} / ${meta.height_px}`;

  renderGateMarkers();
}

function renderGateMarkers() {
  const zone = state.zones[0];
  const meta = state.corridorMeta;
  if (!zone || !meta) return;

  for (const [lat, lon, label] of [
    [zone.entry_gate.lat, zone.entry_gate.lon, "ENTRY"],
    [zone.exit_gate.lat, zone.exit_gate.lon, "EXIT"],
  ]) {
    const { xFrac, yFrac } = projectFrac(lat, lon, meta);
    const marker = document.createElement("div");
    marker.className = "map-marker gate-marker";
    marker.style.left = `${xFrac * 100}%`;
    marker.style.top = `${yFrac * 100}%`;
    marker.innerHTML = `<span class="dot"></span><span class="label">${label}</span>`;
    el.mapOverlay.appendChild(marker);
  }
}

function renderTripMarkers() {
  const meta = state.corridorMeta;
  if (!meta) return;
  el.mapOverlay.querySelectorAll(".trip-marker-wrap").forEach((n) => n.remove());

  const zone = state.zones.find((z) => z.zone_id === state.trips[0]?.zone_id) || state.zones[0];

  for (const trip of state.trips) {
    const pos = estimatedPosition(trip, zone);
    if (!pos) continue;
    const { xFrac, yFrac } = projectFrac(pos.lat, pos.lon, meta);
    const sev = classify(trip);

    const wrap = document.createElement("div");
    wrap.className = "map-marker trip-marker-wrap";
    wrap.style.left = `${xFrac * 100}%`;
    wrap.style.top = `${yFrac * 100}%`;

    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = `trip-marker state-${sev}`;
    if (trip.trip_id === state.selectedTripId) dot.classList.add("selected");
    const label = `${trip.traveller_name || "Traveller"} — ${stateLabel(trip)}`;
    dot.title = label;
    dot.setAttribute("aria-label", label);
    dot.addEventListener("click", () => openDetail(trip.trip_id));

    wrap.appendChild(dot);
    el.mapOverlay.appendChild(wrap);
  }
}

// -- trip list ------------------------------------------------------------

function renderList() {
  el.tripCount.textContent = `${state.trips.length} monitored`;
  el.tripList.innerHTML = "";
  el.emptyState.classList.toggle("visible", state.trips.length === 0);

  const sorted = [...state.trips].sort((a, b) => {
    const sevDiff = SEVERITY_ORDER[classify(a)] - SEVERITY_ORDER[classify(b)];
    if (sevDiff !== 0) return sevDiff;
    return (b.elapsed_min || 0) - (a.elapsed_min || 0);
  });

  for (const trip of sorted) {
    const sev = classify(trip);
    const row = document.createElement("button");
    row.className = `trip-row state-${sev}`;
    if (trip.trip_id === state.selectedTripId) row.classList.add("selected");
    row.type = "button";

    const elapsedStr =
      trip.elapsed_min != null ? `${Math.round(trip.elapsed_min)} min elapsed` : "—";
    const windowStr = trip.monitoring_window_min
      ? ` / ${trip.monitoring_window_min} min window`
      : "";

    row.innerHTML = `
      <span class="status-dot"></span>
      <span class="trip-main">
        <div class="trip-name">${trip.traveller_name || "Unknown traveller"}</div>
        <div class="trip-meta">${elapsedStr}${windowStr} · ${trip.congestion_tier || "—"} traffic</div>
      </span>
      <span class="trip-state-label">${stateLabel(trip)}</span>
    `;
    row.addEventListener("click", () => openDetail(trip.trip_id));
    el.tripList.appendChild(row);
  }
}

// -- detail panel ------------------------------------------------------------

let detailOpenerEl = null;

async function openDetail(tripId) {
  detailOpenerEl = document.activeElement;
  state.selectedTripId = tripId;
  renderList();
  renderTripMarkers();

  try {
    const trip = await api(`/dashboard/trips/${tripId}`);
    fillDetail(trip);
    el.detailPanel.hidden = false;
    el.scrim.hidden = false;
    el.detailClose.focus();
  } catch (e) {
    console.error("failed to load trip detail", e);
  }
}

function fillDetail(trip) {
  const sev = classify(trip);
  el.detailStatus.textContent = stateLabel(trip);
  el.detailStatus.className = `status-pill state-${sev}`;
  el.detailName.textContent = trip.traveller_name || "Unknown traveller";
  el.detailMsisdn.textContent = trip.traveller_msisdn || "—";
  el.detailEntry.textContent = trip.entry_point || "—";
  el.detailLastKnown.textContent = trip.last_known_location || "—";

  const elapsed = trip.elapsed_min != null ? `${Math.round(trip.elapsed_min)} min` : "—";
  const predicted = trip.predicted_crossing_min ? `${trip.predicted_crossing_min} min` : "—";
  const window = trip.monitoring_window_min ? `${trip.monitoring_window_min} min` : "—";
  el.detailPredicted.textContent = `${elapsed} elapsed · predicted ${predicted} · window ${window}`;

  // The dashboard is the Tier 2 emergency-centre view, which sees the
  // actual figure — it's the contact's Tier 1 SMS that gets a band
  // instead, per docs/SignalGuard_Security_Privacy §2.
  el.detailBattery.textContent =
    trip.battery_at_entry != null
      ? `${trip.battery_at_entry}% (${trip.battery_band || "—"})`
      : "—";
  el.detailCongestion.textContent = trip.congestion_tier || "—";
  el.detailRisk.textContent = trip.risk || "—";

  const notes = trip.notifications || [];
  el.detailAlert.textContent =
    notes.length === 0
      ? "Not notified"
      : notes.includes("tier2")
        ? "Contact + emergency centre alerted"
        : "Contact alerted (Tier 1)";

  el.detailRecord.textContent = trip.decision_record || "No decision record yet.";

  el.detailResolve.disabled = !["OVERDUE", "TIER1_ALERTED", "TIER2_ESCALATED"].includes(
    trip.state
  );
  el.detailResolve.textContent = el.detailResolve.disabled
    ? "No Action Needed"
    : "Mark Resolved";
  el.detailResolve.onclick = () => resolveTrip(trip.trip_id);
}

function closeDetail() {
  el.detailPanel.hidden = true;
  el.scrim.hidden = true;
  state.selectedTripId = null;
  renderList();
  renderTripMarkers();
  if (detailOpenerEl && document.body.contains(detailOpenerEl)) {
    detailOpenerEl.focus();
  }
  detailOpenerEl = null;
}

async function resolveTrip(tripId) {
  el.detailResolve.disabled = true;
  el.detailResolve.textContent = "Resolving…";
  try {
    await api(`/dashboard/trips/${tripId}/resolve`, { method: "POST" });
    closeDetail();
    await pollTrips();
  } catch (e) {
    console.error("resolve failed", e);
    el.detailResolve.disabled = false;
    el.detailResolve.textContent = "Mark Resolved";
  }
}

el.detailClose.addEventListener("click", closeDetail);
el.scrim.addEventListener("click", closeDetail);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el.detailPanel.hidden) closeDetail();
});

// -- polling ------------------------------------------------------------

async function pollTrips() {
  try {
    const trips = await api("/dashboard/trips");
    state.trips = trips;
    state.lastPollOk = true;
    el.liveIndicator.classList.remove("stale");
    renderList();
    renderTripMarkers();
  } catch (e) {
    state.lastPollOk = false;
    el.liveIndicator.classList.add("stale");
    console.error("poll failed", e);
  }
}

let pollTimer = null;

async function bootstrap() {
  if (pollTimer) clearInterval(pollTimer);
  el.mapOverlay.innerHTML = "";
  try {
    await loadCorridor();
  } catch (e) {
    el.zoneLabel.textContent = "Could not reach backend";
    console.error("bootstrap failed", e);
  }
  await pollTrips();
  pollTimer = setInterval(pollTrips, POLL_MS);
}

bootstrap();
