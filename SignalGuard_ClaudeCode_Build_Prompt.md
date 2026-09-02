# SignalGuard — Build Prompt for Claude Code

Paste this whole document as your first message to Claude Code, in an empty
project directory. Also drop these files into `docs/` in that same directory
before you start, so Claude Code can read them directly instead of working
from a paraphrase — filenames exactly as they exist in your `docs` folder:

- `docs/MENA Ignite Hackathon (1).pdf` — official rules, mandatory
  requirements, evaluation criteria, submission format
- `docs/SignalGuard_Idea_Capture_Template (4).pdf` — full product spec,
  team, business model, agent design, API usage, prototype-phase plan
- `docs/SignalGuard_Technical_Feasibility.pdf` — gate model, timing budget,
  agent architecture, failure modes, stack, cost per crossing
- `docs/SignalGuard_Security_Privacy (1).pdf` — consent model, data
  minimisation, retention, enterprise legal basis, regulatory alignment
- `docs/SignalGuard_User_Flow (1) (1).pdf` — the exact traveller and
  emergency-centre flows, screen by screen and state by state — this is
  the closest thing to a UI spec for Components 3 and 4
- `docs/SignalGuard Business Model Research (1).pdf` — pricing tiers,
  competitor benchmarks, unit economics — background only, not a coding
  reference, but keep it around for any pricing text that ends up in the UI
- `docs/SignalGuard_Submission_Description.pdf` — the polished public
  description; if any in-app or dashboard copy needs to describe the
  product, match this wording rather than improvising new phrasing
- `docs/Network_as_Code_API.pdf` and `docs/Network_as_Code_API_Full.pdf` —
  Nokia's own API reference (83 endpoints across 29 groups); the five we
  actually need are pulled out with exact request/response shapes in
  `docs/nokia-api-catalog.md` below — use that first, fall back to the raw
  PDFs for anything it doesn't cover
- `docs/nokia-api-catalog.md` — condensed reference for the five CAMARA
  APIs SignalGuard uses (provided separately, drop it into `docs/` too)
- `scripts/run_demo.py` — already built; the backend must satisfy its
  contract exactly (this one goes in `scripts/`, not `docs/`)

---

## Who you are building this for

We are **Team Beyond Signal**, five people, building **SignalGuard** for the
**MENA Ignite Hackathon 2026** (GSMA Open Gateway). Prototype Phase runs
28 Aug – 13 Sep 2026; submission is due 13 Sep 21:29 Amman time. We need a
working prototype, not a finished product — read
`docs/SignalGuard_Technical_Feasibility.pdf` §"What has to be true" and
treat everything outside its four claims as out of scope unless I ask for it.

**Read `docs/MENA Ignite Hackathon (1).pdf`,
`docs/SignalGuard_Idea_Capture_Template (4).pdf`,
`docs/SignalGuard_Technical_Feasibility.pdf`,
`docs/SignalGuard_Security_Privacy (1).pdf`, and
`docs/SignalGuard_User_Flow (1) (1).pdf` in full before writing any code.**
They are the source of truth for the hackathon's mandatory requirements, the
trip state machine, the agent's tool surface, the privacy model, and the
exact screen-by-screen and state-by-state flow. This prompt summarises them
for convenience but the documents are authoritative where they differ.

## The one-sentence pitch

An AI agent uses network-side CAMARA signals (not the phone's GPS) to detect
when a traveller enters a highway dead zone, prepares them for it, and raises
a tiered alarm if they don't reconnect on schedule.

---

## Non-negotiable constraints — read this before writing any code

1. **The AI agent layer must be built only with LangGraph + Gemini 2.5 via
   Google AI Studio** — the hackathon's mandatory AI Resource & Tooling
   Guide. No other agent framework, no other model provider, anywhere in the
   agent layer.
2. **All code must be original and written during the hackathon window.**
   Standard open-source libraries are fine; do not scaffold from a template
   repo or copy a tutorial's project structure wholesale.
3. **The submission must use at least one CAMARA API on the Nokia
   Network-as-Code platform**, and the AI agent must orchestrate one or more
   of them as real-time data sources — not as a button the user presses.
   We're using five: Geofencing Subscriptions, Location Retrieval,
   Congestion Insights, Quality on Demand, Device Reachability Status.
4. **Nokia's sandbox has no device-location-injection endpoint.** The fixed
   simulator devices (see below) are static test fixtures — you query them,
   you cannot move them. Do not invent or assume a movement endpoint. The
   demo triggers gate crossings via the backend's own `/demo/simulate-*`
   endpoints instead (contract below); everything downstream of that trigger
   makes real Nokia calls.
5. **Secrets never touch git or stdout.** `.env` only, `.gitignore`'d,
   loaded via environment variables. No API keys in code, comments, fixtures,
   or log lines. See `docs/SignalGuard_Security_Privacy (1).pdf` §10.
6. **Escalation is timer-driven, not model-driven.** If Gemini is slow,
   unavailable, or returns garbage, a deterministic fallback risk model still
   sets a monitoring window and the alarm still fires on schedule. This is
   the single most important behavioural guarantee in the whole system —
   see Technical Design §5.4 and §6. Test it explicitly.

---

## Design & UI skills — use these for anything visual

Before writing any UI code — the Flutter app's four screens, the dashboard,
and any diagram or mockup you produce along the way — check your available
skills for **`taste`**, **`webdesign guidelines`**, and **`awesome design`**,
and load whichever apply. Several may be relevant to the same screen, so
check all three rather than stopping at the first match. This applies to
Components 3 and 4 specifically, and to any inline diagram or wireframe you
generate while explaining a design decision to me.

If a skill's guidance conflicts with something I've specified above (e.g.
the four-screen limit, or "no auth on the dashboard"), the product
constraints in this prompt win — apply the design skill within that scope,
not instead of it. If none of the three skills are available in this
environment, say so plainly and proceed with your own best judgement rather
than silently skipping the check.

---

## Nokia Network as Code — real API reference

Base URL: `https://network-as-code.p-eu.apihub.nokia.io`
Auth header: `x-rapidapi-key: <NAC_API_KEY>` (case-insensitive)

| Capability | Method + path |
|---|---|
| Create geofencing subscription | `POST /geofencing-subscriptions/v0.3/subscriptions` |
| List geofencing subscriptions | `GET /geofencing-subscriptions/v0.3/subscriptions` |
| Delete geofencing subscription | `DELETE /geofencing-subscriptions/v0.3/subscriptions/{id}` |
| Retrieve last known location | `POST /location-retrieval/v0/retrieve` |
| Congestion Insights (one-shot) | `POST /congestion-insights/v0/query` |
| Congestion Insights (subscribe) | `POST /congestion-insights/v0/subscriptions` |
| Create QoD session | `POST /qod/v0/sessions` |
| Get / delete / extend QoD session | `GET|DELETE /qod/v0/sessions/{id}`, `POST /qod/v0/sessions/{id}/extend` |
| Create reachability subscription | `POST /device-status/device-reachability-status-subscriptions/v0.8/subscriptions` |
| Retrieve reachability (one-shot) | `POST /device-status/device-reachability-status/v1/retrieve` |

**Fixed simulator devices** (use exactly these — do not invent others):
`+99999991000`, `+99999991001`, `+99999990400`, `+99999990404`,
`+99999990422`, `+99999990500`, `+99999990502`, `+99999990503`,
`+99999990504`. Pick one and use it consistently; each returns whatever
fixed location/status Nokia has configured for it, which becomes your real
"last known location" and "device status" values on screen.

Every request body follows the CAMARA `device: {phoneNumber: "..."}` shape.
Full request/response examples for all five APIs are in
`docs/nokia-api-catalog.md` — use those exact shapes, don't guess. If you
need an endpoint outside these five, check `docs/Network_as_Code_API_Full.pdf`
(all 83 endpoints) before inventing anything.

---

## Architecture

```
┌───────────────┐      ┌──────────────────────┐      ┌────────────────┐
│  Flutter app   │◀────▶│      Backend          │◀────▶│  Nokia NaC      │
│  (traveller)   │ HTTP │  FastAPI + agent       │ HTTP │  sandbox        │
└───────────────┘      │  (trip state machine,  │      └────────────────┘
                        │   LangGraph agent,     │
┌───────────────┐      │   Nokia client,        │
│  Dashboard     │◀────▶│   demo control surface)│
│  (emergency    │ HTTP │                        │
│   centre)      │      └──────────────────────┘
└───────────────┘                ▲
                                  │ HTTP
                        ┌──────────────────┐
                        │  run_demo.py      │  (already built — do not
                        │  demo conductor    │   change its contract)
                        └──────────────────┘
```

One repo, four components. Build in this order — each is usable on its own
before the next starts, which is also roughly our team's five-person split.

---

## Component 1 — Backend (build first)

**Stack:** Python, FastAPI, async. Persist trip/subscription state in
whatever's fastest to stand up (SQLite is fine for a hackathon; Postgres if
you're already comfortable with it — don't over-engineer this).

### Trip state machine

```
IDLE --area-entered(entry gate)--> BUFFER --agent completes--> ACTIVE
ACTIVE --reachable | area-entered(exit gate)--> EXITED
ACTIVE --monitoring window expires--> OVERDUE
OVERDUE --SMS to contact--> TIER1_ALERTED
TIER1_ALERTED --contact confirms safe--> RESOLVED
TIER1_ALERTED --grace elapses, no reply--> TIER2_ESCALATED
```

State lives entirely server-side — never assume the phone is awake or online.

### Zone registry

One table: `zone_id`, `label`, `entry_gate {lat, lon}`, `exit_gate {lat, lon}`,
`gate_radius_m`, `corridor_km`, `nominal_crossing_min`. Seed with one zone to
start (Highway 15 / Al Mudawwara — exact coordinates in
`docs/SignalGuard_Technical_Feasibility.pdf` §1). This is the real geofence
configuration; it does not depend on the demo control surface below.

### Real endpoints (the actual product surface)

Design these from `docs/SignalGuard_Idea_Capture_Template (4).pdf` §8,
`docs/SignalGuard_Technical_Feasibility.pdf` §2–3, and the exact flow in
`docs/SignalGuard_User_Flow (1) (1).pdf`. At minimum:
- Register traveller + contacts, get an auth/session token for the app
- Report battery level
- Get current trip status for a traveller
- Webhook receiver for real Nokia geofencing/reachability notifications
  (production path — used once we have operator approval; the sandbox path
  goes through the demo endpoints below for now)
- Dashboard read endpoints: list active trips, trip detail, resolve trip

### Demo control surface — implement this to the letter

`run_demo.py` already exists and calls this exact contract. Do not change
these paths, methods, or payload shapes — the conductor is finished and
tested against them.

```
GET  /healthz                        -> {ok, nac:{apis:[...]}, agent:{model, enabled}}
POST /demo/reset                     -> clears trips, subscriptions, api log
POST /demo/travellers                {msisdn, name, contacts:[{name,msisdn}]}
                                     -> {traveller_id}
POST /demo/zones/{zone_id}/arm       {traveller_id}
                                     -> {entry_subscription_id, exit_subscription_id}
                                     creates a REAL Geofencing Subscription on Nokia's
                                     sandbox against your chosen fixed device
POST /demo/simulate-gate-event       {traveller_id, zone_id, gate:"entry"|"exit"}
                                     -> {}
                                     Injects a CAMARA-shaped event into the SAME
                                     webhook handler a real Nokia notification would
                                     hit. In response, the backend must make its
                                     normal REAL calls — Location Retrieval,
                                     Congestion Insights, QoD — against the fixed
                                     device, then run the agent.
POST /demo/simulate-reachability     {traveller_id, reachable: bool}
                                     -> {}
GET  /demo/trips/active              ?traveller_id=  -> {trip_id, state} | null
GET  /trips/{trip_id}                -> {trip_id, state, risk, battery_at_entry,
                                          battery_band, congestion_tier, entry_point,
                                          last_known_location, predicted_crossing_min,
                                          monitoring_window_min, decision_record (string),
                                          notifications: [string]}
POST /demo/clock/advance             {seconds} -> {now}
                                     Virtual clock. All timers (monitoring window,
                                     Tier 1/2 grace periods) must key off this clock
                                     when it's been advanced, not wall time.
POST /demo/battery                   {traveller_id, level}
POST /demo/agent/model               {enabled: bool}
                                     Kill switch. When disabled, the agent MUST still
                                     run — via the deterministic fallback risk model,
                                     not by skipping the agent step entirely.
POST /demo/contact-reply             {trip_id, reply:"safe"}
GET  /demo/api-log                   ?since=<iso>
                                     -> [{ts, api, endpoint, latency_ms, status, cost_usd}]
                                     Every real Nokia call the backend makes must be
                                     appended here — this feeds the network activity
                                     pane in the demo video.
```

Mount all of these under a router gated by `SIGNALGUARD_DEMO_MODE=true`, so
they cannot exist in whatever you'd eventually ship to production.

### The decision record

`trip.decision_record` must be a **human-readable multi-line string**, not
raw JSON — it's read on camera. Format loosely like:

```
signals   congestion=light  battery=18%  hour=14  zone=104km
model     gemini-2.5-flash
reasoning light traffic, but 18% battery shortens the window in which
          a handset can be found; escalate earlier than nominal
window    92 min (nominal 75 + buffer)
tools     get_congestion_insights, get_location, request_qod_session
action    QoD session requested; monitoring window armed
```

When the model is disabled, this becomes:
```
model     UNAVAILABLE -> deterministic risk model
          floors/ceilings applied, no reasoning text
window    105 min (deterministic — wider than the model's typical 92)
```

Build this first — before the Flutter app, before the dashboard. **Once
`python scripts/run_demo.py --scenario happy` runs clean against your
running backend, stop and tell me.** That's milestone 1.

---

## Component 2 — Agent

**Stack:** LangGraph, Gemini 2.5 (Flash for routine calls, Pro for risk
judgement) via Google AI Studio. Read
`docs/SignalGuard_Technical_Feasibility.pdf` §5 in full.

### Tools (fixed-parameter wrappers, one per CAMARA call or internal action)

```
get_congestion_insights(zone_id)      request_qod_session(trip_id)
get_location(trip_id)                 notify_contact(trip_id, tier)
check_device_reachability(trip_id)    escalate_to_dashboard(trip_id)
get_zone_profile(zone_id)             manage_geofence_subscription(...)
```

The agent selects a tool and supplies an identifier. It never constructs raw
HTTP requests, never picks an endpoint, never widens a query. Enforce this in
the tool layer (fixed function signatures), not by prompting the model to
behave — a prompt is a request, a function signature is a constraint.

### What the agent decides

- Predicted crossing time and a risk-adjusted monitoring window
- Whether to spend a QoD session (not every crossing warrants one)
- Escalation shape and timing
- When to stand down (contact replies "he's fine" → suppress further escalation)

### The fallback is not optional

Underneath the LLM, build a deterministic risk model with hard floors and
ceilings on the monitoring window. When `agent.model.enabled == false` (via
the demo kill switch, or a real Gemini outage), this fallback must still
produce a valid window and the timer-driven escalation in Component 1 must
still fire. **Write a test for this specifically** — call it something like
`test_escalation_fires_with_model_disabled` — because it's the single claim
the whole safety pitch rests on.

---

## Component 3 — Flutter app (traveller-facing)

Four screens only. Do not build more. This is on camera for the 3-minute
video (Technical Design's own films-well requirement) — apply the `taste`
and `awesome design` skills here specifically; a screen that looks
unfinished undercuts the "the product is the absence of interaction"
pitch it's meant to demonstrate.

1. **Onboarding + contacts** — grant background location, authorise network
   access (a static "authorised at your operator" screen is fine for the
   prototype — a real OIDC/CIBA flow is out of scope), add 1–2 emergency
   contacts.
2. **Approach notification** — "Low-coverage zone ahead. Preparing you
   now…" then "You're offline-ready. Risk: LOW, ~75 min."
3. **Offline map** — works with the radio off. Own GPS position, a
   corridor tile pack, an arrow pointing to the nearest reconnection point.
   Test this for real in airplane mode — it will be filmed that way.
4. **Reconnection** — quiet "Welcome back online — trip completed safely"
   confirmation.

Report battery level to the backend periodically. Android first; do not
build iOS.

---

## Component 4 — Dashboard (emergency-centre facing)

One web app. Apply the `webdesign guidelines` skill here — it's a browser
UI and also on camera (network activity pane + live trip map, per the demo
plan). Live trips on a map/list, colour-coded green (in transit) /
amber (approaching predicted exit) / red (overdue). Click a trip to see
entry point, last-known location, predicted-vs-elapsed time, battery band,
congestion tier, and a resolve action. No auth, no multi-tenancy, no
settings — this is a hackathon prototype, not a product.

---

## Explicit non-goals

Do not build any of: iOS, a real OIDC/CIBA consent flow, dashboard auth or
multi-tenancy, account management, a self-hosted map tile server (bundle one
corridor pack instead), more than one zone, crossing-history model training,
wearables, cross-border roaming handling, retention/erasure tooling. If it's
not in Components 1–4 above, don't build it unless I ask.

---

## How I want you to work

1. **Read the five reference PDFs in `docs/` first** (hackathon rules, idea
   capture, technical feasibility, security & privacy, user flow). Confirm
   you've read them before writing code.
2. **Build Component 1 (backend) completely first**, including the full
   demo control surface. Stop and tell me once
   `python scripts/run_demo.py --scenario happy` passes against it for real
   (not `--dry-run`) — that's the milestone that unblocks everything else.
3. Then Component 2 (agent), verified against `--scenario model-down`
   specifically — that's the one that proves the fallback works.
4. Components 3 and 4 can happen in parallel once Component 1's real
   endpoints (not just the demo ones) are stable — flag me when you're ready
   to start either one.
5. **Ask before making an architectural choice I haven't specified** —
   database choice, auth scheme, hosting — rather than picking silently and
   surfacing it later.
6. **Never commit a real API key, even a sandbox one.** Use `.env.example`
   with empty values; real values go in a gitignored `.env`.
7. When something in Technical Design or Security & Privacy conflicts with
   this prompt, the source document wins — tell me about the conflict rather
   than silently picking one.

Start by reading the docs, then propose the repo layout and confirm the
backend framework choices before writing the trip state machine.
