# SignalGuard

**Network-side safety monitoring for travellers crossing mobile dead zones.**

MENA Ignite Hackathon 2026 · Team Beyond Signal

SignalGuard is an AI agent that uses operator network signals, exposed through
the CAMARA APIs on Nokia Network as Code, to detect when a traveller enters a
highway dead zone. It prepares the traveller before signal is lost, monitors the
crossing without relying on the phone, and raises a tiered alarm if the
traveller does not reconnect on schedule.

---

## Contents

- [Overview](#overview)
- [Key features](#key-features)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Demo scenarios](#demo-scenarios)
- [Testing](#testing)
- [Configuration](#configuration)
- [Known limitations](#known-limitations)
- [Integration notes](#integration-notes)
- [Repository layout](#repository-layout)

---

## Overview

Long desert corridors across the region have stretches with no mobile coverage.
A traveller who breaks down or is injured in one cannot call for help, and
nobody knows they are missing until hours later.

SignalGuard moves detection from the handset to the network:

1. **Entry.** A Geofencing subscription on the corridor's entry gate notifies the
   backend when the traveller's line crosses it.
2. **Assessment.** The agent gathers Location Retrieval, Congestion Insights and
   battery data, and sets a monitoring window based on learned corridor crossing
   times and a risk assessment.
3. **Monitoring.** Device Reachability Status subscriptions report when the line
   reconnects. The exit gate confirms the crossing.
4. **Escalation.** If the traveller has not reconnected when the window expires,
   SignalGuard follows an escalation ladder, from checking with the traveller
   through to alerting an emergency centre.

The alarm is timer-driven. The language model informs the risk assessment but is
never on the critical path, so an alarm fires on schedule even if the model is
unavailable.

## Key features

**Tiered escalation**

```
ACTIVE → TIER0_CHECKING → OVERDUE → TIER1_ALERTED → TIER2_ESCALATED
```

- **Tier 0.** If the line is reachable when the window expires, the traveller is
  asked to confirm they are safe. A confirmation closes the trip without
  contacting anyone. No response within 90 seconds escalates to Tier 1. A handset
  that is still offline skips Tier 0.
- **Tier 1.** The traveller's emergency contacts are alerted.
- **Tier 2.** The trip is escalated to the emergency-centre console with a search
  area, the agent's decision record and supporting network data.

**Network-side intelligence**

- All five CAMARA APIs integrated against the live Nokia sandbox: Geofencing
  Subscriptions, Location Retrieval, Congestion Insights, Quality on Demand and
  Device Reachability Status.
- Quality on Demand is requested at reconnection, where extra bandwidth helps
  with position upload and notifications.
- Reachability acts as the primary exit signal, so a traveller who leaves by a
  side road does not trigger a false alarm.

**Adaptive monitoring**

- **Learned corridor times.** Completed crossings build a per-hour baseline
  (p50 for planning, p90 for buffer sizing). Escalated crossings are excluded so
  that incidents do not skew normal travel times.
- **Coverage discovery.** Network observations identify likely unregistered dead
  zones. Candidates must be reviewed and promoted manually before they are
  monitored. Observations are anonymised and snapped to a ~5.5 km grid.
- **Convoy detection.** Travellers entering the same corridor within a short
  window are linked. A peer who has already exited is listed in the escalation
  record as a first point of contact. Peers can resolve a trip but can never
  delay an alarm.
- **Planned stops.** Travellers can extend their window for a declared stop,
  reducing false alarms.

**Emergency-centre console**

- Live triage queue with green, amber and red status, sorted by urgency.
- Corridor map showing the traveller's possible position as a range that widens
  over time, not as a single point.
- Trip detail panel: entry point, predicted vs. elapsed time, battery,
  congestion, risk score, contact-alert status and the agent's decision record.
- Network API activity panel with per-API call counts, latency and status, plus
  the individual CAMARA requests behind each trip.
- Audible alert on escalation, trip history, deep links to individual trips
  (`#trip/<id>`) and keyboard-accessible controls.

**Traveller app**

- Onboarding, approach, active-crossing, offline-map and reconnection screens.
- Bundled OpenStreetMap tile pack, so the map works with no signal.
- Clear deadline display ("If you're not back by 19:07, we text your contact").
- Light and dark themes, proximity-tiered GPS sampling to save battery, and
  haptic feedback on key events.

## Architecture

| Component | Stack | Location |
|---|---|---|
| Backend | Python, FastAPI, SQLAlchemy (async SQLite) | `backend/` |
| Agent | LangGraph, Gemini via Google AI Studio, deterministic fallback risk model | `backend/app/agent/` |
| Traveller app | Flutter (Android) | `app/` |
| Emergency-centre console | HTML, CSS, JavaScript (no build step) | `dashboard/` |

```
 Traveller app ──────────────┐
                             ▼
 Nokia Network as Code ──▶ FastAPI backend ──▶ LangGraph agent ──▶ Gemini
 (CAMARA APIs, webhooks)     │   state machine     └─▶ fallback risk model
                             ▼
                   Emergency-centre console
```

Screenshots of the app and console are in [`docs/screenshots/`](docs/screenshots/).

## Getting started

For a step-by-step walkthrough, see [REVIEWERS.md](REVIEWERS.md).

### Prerequisites

- Python 3.11+
- A [Nokia Network as Code](https://network-as-code.nokia.io) sandbox API key
- A Google AI Studio API key (optional; the agent uses its fallback model without one)
- Flutter 3.44+ and an Android emulator (traveller app only)

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # macOS/Linux: .venv/bin/pip
cp .env.example .env                             # set NAC_API_KEY at minimum
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify with `curl http://127.0.0.1:8000/healthz`.

### 2. Console

From the repository root:

```bash
python -m http.server 5500 --directory dashboard
```

Open <http://localhost:5500>. The backend URL is set in the top bar and defaults
to `http://127.0.0.1:8000`.

### 3. Sample data

```bash
python scripts/seed_console.py
```

This registers four travellers, arms the corridor geofences against the live
sandbox and moves the trips through escalated, near-deadline, in-transit and
completed states.

### 4. Demo controls

<http://localhost:5500/demo.html> provides manual controls for a crossing: arm the
corridor, enter or exit a gate, go offline, reconnect, advance the virtual clock
and simulate traveller or contact replies. It is available only when the backend
runs with `SIGNALGUARD_DEMO_MODE=true` and is kept separate from the operator
console.

### 5. Traveller app

```bash
cd app
flutter pub get
flutter run -d <emulator-id>
```

The app connects to `http://10.0.2.2:8000`, the Android emulator's address for the
host machine.

## Demo scenarios

`scripts/run_demo.py` runs narrated end-to-end scenarios against the live sandbox
with pass/fail assertions. Run from the repository root:

```bash
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario happy
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario overdue
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario stand-down
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario model-down
```

| Scenario | Demonstrates |
|---|---|
| `happy` | Normal crossing with real Gemini reasoning in the decision record |
| `overdue` | Missed reconnection and escalation through the tiers |
| `stand-down` | An emergency contact standing the alarm down |
| `model-down` | The alarm firing on schedule with the language model disabled |

On Windows terminals without UTF-8, set `PYTHONIOENCODING=utf-8` first.

## Testing

```bash
cd backend && .venv/Scripts/python -m pytest tests/ -q    # 57 tests
cd app && flutter test                                    # 17 tests
```

Tests stub the Nokia client and do not require a sandbox key.
`tests/test_fallback.py::test_escalation_fires_with_model_disabled` verifies the
core safety guarantee: escalation does not depend on the language model.

A benchmark for the continuously polled endpoints is also included:

```bash
cd backend && .venv/Scripts/python tools/bench_hot_paths.py
```

| Endpoint | Median latency |
|---|---|
| `GET /dashboard/trips` (12 live trips) | 5.9 ms |
| `GET /travellers/me/trip` | 5.5 ms |
| `GET /dashboard/history` | 3.0 ms |
| `GET /dashboard/zones/{id}/stats` | 2.6 ms |

## Configuration

Settings are read from `backend/.env`. See
[`backend/.env.example`](backend/.env.example) for the full list.

| Variable | Purpose |
|---|---|
| `NAC_API_KEY` | Nokia Network as Code sandbox key (required) |
| `GOOGLE_API_KEY` | Google AI Studio key for Gemini (optional) |
| `GEMINI_MODEL`, `GEMINI_RISK_MODEL` | Gemini model IDs (default `gemini-flash-lite-latest`) |
| `SIGNALGUARD_DEMO_MODE` | Enables demo endpoints and the demo controls page |
| `SIGNALGUARD_WEBHOOK_BASE` | Public base URL for CAMARA notification webhooks |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Contact notifications via WhatsApp/SMS |

Never commit `.env` or real API keys.

## Known limitations

This is a hackathon prototype. The following limits apply to the current build:

- **Simulated triggers.** The Nokia sandbox cannot move a device, so gate
  crossings and reachability changes are simulated. All downstream CAMARA calls,
  agent decisions and escalations are real.
- **Sandbox location.** The sandbox's simulator device reports a fixed European
  location. The console detects a fix far from the corridor and flags it rather
  than presenting it as a search position.
- **Contact notifications.** WhatsApp and SMS delivery is implemented and tested
  but disabled, because Twilio's trial tier blocks custom message content.
  Adding billing to the Twilio account enables delivery without code changes.
  Unsent messages appear as `SKIP` in the console. Inbound replies require a
  public HTTPS webhook and are not yet wired.
- **Security.** The console has no authentication and CORS is open. Both must
  be addressed before any deployment beyond a demo (see `backend/app/main.py`).

## Integration notes

Findings from integrating with the live Nokia Network as Code sandbox:

1. **`x-rapidapi-host` header.** Required alongside `x-rapidapi-key`. The value is
   the RapidAPI listing name (`network-as-code.nokia.rapidapi.com`), not the
   request host. Omitting it returns `404 {"message":"API doesn't exists"}`.
2. **Webhook sinks.** The `sink` field on Geofencing and Congestion Insights
   subscriptions must be a resolvable hostname, otherwise the sandbox returns
   `400 INVALID_SINK`.
3. **Congestion Insights response.** Returns an array of time-bucketed readings,
   most recent first, rather than a single object.
4. **Device Reachability Status subscriptions.** The request must include
   `protocol` and `config.subscriptionDetail.device`, and the v0.8 event type is
   `org.camaraproject.device-reachability-status-subscriptions.v0.reachability-data`.
5. **Gemini models.** Pinned `gemini-2.5-*` IDs are unavailable to new API keys.
   `gemini-flash-lite-latest` is the default because it is reliably available on
   the free tier.

## Repository layout

```
backend/       FastAPI backend and LangGraph agent
  app/
    agent/             agent graph, tools and fallback risk model
    nokia_client.py    CAMARA API client
    state_machine.py   trip lifecycle and escalation ladder
    api_activity.py    CAMARA call telemetry for the console
  tests/               backend test suite
  tools/               performance benchmark
app/           Flutter traveller app
dashboard/     emergency-centre console and demo controls
scripts/       run_demo.py (scenarios), seed_console.py (sample data)
docs/          screenshots and reference material
REVIEWERS.md   step-by-step setup and review guide
```

---

**Team Beyond Signal** · MENA Ignite Hackathon 2026
