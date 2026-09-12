# SignalGuard

AI agent uses network-side CAMARA signals (not phone GPS) to detect when a
traveller enters a highway dead zone, prepares them for it, and raises a
tiered alarm if they don't reconnect on schedule.

MENA Ignite Hackathon 2026 · Team Beyond Signal.

## Status

- **Component 1 (backend)** — done. `python scripts/run_demo.py --scenario
  happy` passes for real against the live Nokia NaC sandbox, all 5 CAMARA
  APIs called for real (Geofencing Subscriptions, Location Retrieval,
  Congestion Insights, QoD, Device Reachability). All 4 demo scenarios
  (`happy`, `overdue`, `stand-down`, `model-down`) verified for real.
- **Component 2 (agent)** — done. LangGraph + Gemini via Google AI Studio
  (see model-string note below), deterministic fallback risk model
  underneath. Both paths verified for real: `--scenario happy` shows a
  genuine Gemini reasoning decision record; `--scenario model-down` proves
  the alarm still fires on the timer with the model switched off.
- **Component 3 (Flutter app)** — done. All 4 screens, verified live on an
  Android emulator against the real backend (registration, zone fetch,
  battery reporting, trip polling, connectivity-driven screen switching).
  Offline map is a real bundled OpenStreetMap tile pack (not a schematic),
  auto-zoomed to keep the destination gate in frame with a route line.
- **Component 4 (dashboard)** — done. Vanilla HTML/CSS/JS, no build step,
  served from `dashboard/`. Live trip list + real corridor map, green/amber/
  red colour coding, click-through detail panel (entry point, last-known
  location, predicted-vs-elapsed, battery, congestion, risk, contact-alert
  status, decision record), resolve action. Verified live: multi-trip
  colour/sort correctness, resolve-clears-queue, polling. Position on the
  map is estimated from elapsed time along the corridor, not a live GPS
  feed — the network only ever gives one location snapshot at entry, by
  design (see Security & Privacy doc §2), so a "live tracking dot" would
  misrepresent what the product actually knows.

## What was added after the first working build

The four components above were complete and demoable. This is what came out
of a review pass over them — one security fix, a set of measured
optimisations, and the product changes that came with actually using the
thing.

### The escalation ladder gained a rung below Tier 1

`ACTIVE -> TIER0_CHECKING -> OVERDUE -> TIER1_ALERTED -> TIER2_ESCALATED`

When the monitoring window expires and the network says the handset is
reachable again, SignalGuard asks the **traveller** before it tells any
human. Answering "I'm fine" closes the trip with nobody contacted. Not
answering costs 90 seconds (`tier0_grace_sec`) and then produces exactly
the Tier 1 that would have fired anyway.

This restores the silent-happy-path promise in the Security & Privacy doc
(§7) that the WhatsApp-on-entry change deviated from — as a feature rather
than a retraction. A dark handset skips Tier 0 entirely: pinging a phone
with no signal buys nothing and would only delay the alarm.

`tests/test_new_features.py::test_tier0_cannot_suppress_an_alarm` is the
one that matters. Tier 0 delays escalation by design, and a delay mechanism
with a bug in it is indistinguishable from a suppression mechanism.

### Quality on Demand moved to the reconnection edge

QoD used to be requested at the entry gate — applying a connectivity boost
to a handset that was seconds from losing signal entirely. It is now judged
at entry (`trip.qod_warranted`) and **spent** when the device comes back,
where the bandwidth actually buys something: position upload, the all-clear
to a waiting contact, queued data flushing. Same API, same one session per
crossing.

### Corridor times are learned, not hardcoded

`nominal_crossing_min` was one hand-set constant (75 for Highway 15) —
identical at 3am on an empty road and 5pm behind a truck convoy. Every
completed crossing now writes a `crossing_history` row, and once a
(zone, hour-of-day) bucket has `corridor_stats_min_samples` **clean**
crossings, the agent plans against the observed p50 and sizes the buffer
with the observed p90.

Escalated crossings are excluded from the baseline. They are real durations
but they are evidence about an incident, not about how long the road
normally takes — counting them would make the system less likely to alarm
the more often it had to, which is exactly backwards.

The dashboard shows this per hour, including which buckets are still below
the sample floor.

### Dead zones can discover themselves

Every crossing already makes a real Location Retrieval call and a real
reachability check, so the network has been describing its own coverage
holes all along. `coverage_observations` keeps them; `GET /zones/candidates`
returns grid cells that look like holes and are not already registered.

Candidates are proposals, never live geofences — promotion is a deliberate
`POST /zones/candidates/{cell}/promote`, because arming a geofence on a
guess would page real contacts about a corridor nobody has surveyed. Rows
carry no traveller id and positions are snapped to a ~5.5 km grid before
use: this aggregates coverage, not people (Security & Privacy §2).

### Convoy mode

Two travellers entering the same corridor within `convoy_window_min` are
travelling together for practical purposes. When one goes overdue and a
peer is already out the far side, the peer lands in the escalation record —
somebody who drove that exact road minutes ago is a far better first check
than a contact 400 km away. Membership is derived from entry time, never
declared.

A peer can resolve a trip. A peer can never delay one; the Tier 1 deadline
is untouched by their existence.

### Device Reachability Status runs on the subscription form

Technical Feasibility §2 calls this out as a design improvement over
polling — "it removes polling entirely, which cuts per-crossing API cost to
near zero and means reconnection is detected within the operator's
notification latency rather than within one poll interval" — and §8 prices
the API at ~$0 per crossing on that basis. The code had the retrieve
endpoint wired up and the subscription helper sitting unused, which is the
superseded design the doc explicitly supersedes.

Arming a zone now creates a real Device Reachability Status subscription
alongside the two gate subscriptions (confirmed live: `201` from
`POST /device-status/device-reachability-status-subscriptions/v0.8/subscriptions`),
with `/hooks/reachability` as its sink. Pushed state is held on the
traveller, so the Tier 0 gate reads the answer the network already gave
instead of spending a CAMARA call to ask again.

The request body follows Nokia's own portal reference
(`docs/Network_as_Code_API_Full.pdf`) rather than the sample in
`docs/nokia-api-catalog.md`, which is a partial: it omits `protocol` and
`config.subscriptionDetail.device`, so a body built from it never says
which line to watch, and its event type (`...v0.reachable`) is not the one
v0.8 publishes (`...v0.reachability-data`).

The retrieve endpoint stays as the redundancy path — a lapsed subscription
or a lost notification — and a push from before the trip opened is not
trusted, because a stale "reachable" would offer Tier 0 to a handset that
is dark. `test_a_pushed_dark_handset_skips_tier0_without_a_camara_call` and
`test_a_stale_push_from_before_the_crossing_is_not_trusted` cover both
directions.

### Reachability is the primary exit signal

A traveller who leaves the corridor by a side road never crosses the exit
gate. Gate-only exit detection turned that ordinary event into a Tier 1
alarm about somebody already home. The gate is now a confirmation of
something the network usually reports first; `trip.exit_signal` records
which fired.

### Traveller-declared stops

`POST /travellers/me/planned-stop {minutes}` extends the window without
touching monitoring. The commonest reason a crossing runs long is a person
who stopped, and the real cost of those false alarms is not the one message
— it is that a contact who gets a few of them stops taking them seriously.

### Position is a range, not a point

The dashboard drew a dot at an interpolated position, visually identical to
a real GPS fix, with the caveat in small print under the map. It now draws
the range the system can actually defend (`uncertainty.py`), widening with
time since the entry-gate snapshot — the only real fix this system ever
holds.

Beyond honesty: a Tier 2 handoff needs a *search area*. A point is not one;
it is a false one, and a team sent to a false point has spent the only
resource that matters.

### Correctness fixes from the doc-conformance pass

**The agent read wall time, not the virtual clock.** `agent/graph.py` took
its hour-of-day from `datetime.now(timezone.utc)` while `corridor_stats`
writes its history bucket from `trip.entered_at`, which is the virtual
clock. In any run where `/demo/clock/advance` crossed an hour boundary the
two never named the same bucket, so the learned corridor time could not be
read back at all — and the hour handed to the model was not the hour the
crossing happened in, which matters because night is a risk input.
Reproduced with the clock advanced three hours: `entered_at` 20:27, record
`hour=17`. Now `clock.now().hour`.

**A timestamp that broke the naive-UTC convention.** `nokia_client`'s
success path logged an aware `datetime` to `api_log` while every other
writer — its own error path, the WhatsApp client, `clock.py` — writes
naive-UTC. `clock.py` says why that matters: mixing the two is what makes a
later comparison raise `TypeError`. Invisible on SQLite, whose dialect
strips tzinfo on the way in; not invisible on a backend that keeps the
offset.

**The SQLite file was only gitignored under `backend/`.** It is written
there when the backend starts from that directory and to the repo root when
it starts from here, and it holds traveller names, MSISDNs and contact
numbers. Now ignored wherever it lands, along with local verification
screenshots.

### Security fix — stored XSS in the dispatcher console

`dashboard/js/app.js` built trip rows with `innerHTML` and interpolated
`traveller_name` raw. A traveller registering as
`<img src=x onerror=...>` executed JavaScript in every dispatcher's browser
on every poll — confirmed reproducible end to end, then fixed. All
backend-derived text now goes through `textContent`; `TravellerIn.name` is
additionally length-bounded server-side as a second layer.

### Measured optimisations — network latency

| change | before | after |
|---|---|---|
| `_gather_signals` — congestion + location concurrent | 375 ms | 253 ms |
| `arm_zone` — two geofence creates concurrent | 402 ms | 219 ms |

(medians of 8 runs against the live sandbox)

Also: indexes on `trips.state`, `trips.traveller_id` and `api_log.ts`
(`tick()` runs on five endpoints including both poll paths and was doing a
full table scan); `/demo/api-log` is bounded.

### Measured optimisations — the polled endpoints

Everything above was about the calls that leave the process. This is the
work the backend does *between* them, on the two paths that run on a timer
forever: the dashboard's 3-second queue poll and the app's trip poll, both
of which go through `state_machine.tick()`.

`backend/tools/bench_hot_paths.py` is the harness — it drives the real ASGI
app in-process with the five Nokia calls stubbed the way the tests stub
them, so what it measures is this codebase's own SQL, ORM and serialization
with no sandbox in the sample. Numbers below are medians of 120 samples,
running the before and after alternately rather than back to back (the
run-to-run spread on a laptop is wide enough to swamp a 15% change
otherwise), reproduced across three rounds.

| endpoint | before | after | at 60 live trips |
|---|---|---|---|
| `GET /dashboard/trips` (12 live) | 23.9 ms | **5.9 ms** | 35.8 ms -> 10.6 ms |
| `GET /travellers/me/trip` | 19.7 ms | **5.5 ms** | 21.3 ms -> 5.6 ms |
| `GET /dashboard/history` | 10.2 ms | **3.0 ms** | |
| `GET /dashboard/zones/{id}/stats` | 8.8 ms | **2.6 ms** | |
| `GET /zones/candidates` (4k observations) | 17.9 ms | **11.0 ms** | |

The backend test suite went from 24.1 s to 10.6 s off the back of the same
changes plus one fixture fix.

**Connection pooling.** SQLAlchemy defaults a file-backed aiosqlite engine
to `NullPool`, so every session checkout opened a brand-new SQLite
connection — and aiosqlite runs each connection on its own worker thread,
making that a thread spawn plus a file open plus two `PRAGMA`s. The poll
paths take two checkouts per request (`tick()` commits and releases before
the endpoint's own query runs), so the dashboard and the app were each
paying for two new threads every few seconds, forever. This is the single
biggest item in the table and it accounts for most of every row.

Worth being explicit that this is **not** the `StaticPool` that db.py's
docstring records as tried-and-reverted. StaticPool shares one connection
between every session, which is what let concurrent transactions interleave
and produce `StaleDataError`. A queue pool still hands each checkout its own
distinct connection with its own transaction; it only stops throwing that
connection away afterwards.

**`tick()` asks the database which trips can move.** It used to select every
trip in a live state and then compare deadlines in Python — so each poll,
from each app and each dashboard, loaded and hydrated a full ORM `Trip` for
every crossing currently in flight, and then in the overwhelmingly common
case did nothing with any of them. The four deadline conditions are now a
`WHERE` clause that is the exact SQL twin of the transition loop, so the
quiet case is one indexed lookup returning no rows. That is why the app's
poll is flat in the number of live trips after the change (5.5 ms at 12,
5.6 ms at 60) and was not before: a traveller's own poll no longer pays for
everybody else's crossing.

**One statement instead of three per dashboard poll.** `corridor_km` came
from a separate zone-registry read on every poll and the traveller came from
a `selectinload` follow-up query; both now ride along on the row the trip is
already being read from (outer join, so a trip whose zone was removed still
appears in the queue rather than vanishing from the one screen meant to show
every trip somebody is watching).

**Skipping FastAPI's encoder on the polled reads.** FastAPI runs whatever a
route returns through `jsonable_encoder` — a recursive walk testing every
value against pydantic-model / dataclass / enum / date / Decimal. That was
23% of `/dashboard/trips` CPU and all of it redundant: `serializers.py`
already emits nothing but JSON primitives, by hand, because the demo
contract pins those exact string shapes anyway. See `app/responses.py`;
it is used only on the endpoints that are actually polled on a timer.

**Smaller things.** `tick()` no longer re-`SELECT`s the rows it just wrote
(`expire_on_commit=False` means the commit expires nothing, so a read-back
can only return what is already in memory). `coverage.candidates` filters in
`HAVING` rather than in Python, so its cost tracks how many coverage holes
were found rather than how much of the map has been driven, and
`_known_zone_cells` reads four floats per zone instead of whole ORM rows.
The test fixture called `reset_db()` immediately before `POST /demo/reset`,
which calls it again — every test was rebuilding the schema twice.

### App and dashboard

**App** — the BUFFER screen's progress bar claimed to be "downloading the
offline map", under an indeterminate indicator tracking nothing; the map is
bundled, and a fake progress bar is the last thing worth keeping in a
product whose credibility rests on being straight about what it knows.
Replaced with what is actually happening.

The ACTIVE card now says *when* — "If you're not back by 19:07, we text
your contact" — rather than leaving the traveller to do arithmetic on a
"115 min window" while driving. The offline map carries the same countdown,
which works with the radio dark because the deadline was fixed at entry.

A daylight theme was added. Dark-only was a real failure in the one
environment this app is guaranteed to be used in: a phone in a windshield
mount on a desert highway at midday.

GPS sampling now tiers by proximity (`GpsMode.idle/approach/crossing`)
instead of running continuous high accuracy from launch to death — in a
product whose risk model keys off battery at entry, and whose pitch is that
detection is network-side so the phone doesn't have to do this. Polling
follows the same logic: 3s in Tier 0, 5s during a crossing, 45s idle,
stopped when backgrounded.

Reconnection persists until acknowledged rather than vanishing after four
seconds; entry, going dark, and Tier 0 fire haptics, because a driver is
not looking at the phone.

**Dashboard** — audible alert and title flash on escalation (a console
where a Tier 2 lands in silence is an operational hole); a dedicated live
region instead of `aria-live` on a list that repainted every 3 seconds; the
list updates in place so focus and scroll survive; focus trap on the detail
panel; a confirmation step on resolve; an explicit "updated 4s ago"
readout; a history view, because an emergency centre that forgets every
trip the moment it closes cannot answer the questions it exists to answer.

Polling pauses on a hidden tab. Static assets carry a `?v=` query — with no
build step, a browser silently running a previous build of `app.js` while
the disk had the current one cost real debugging time.

## What was added for the Prototype Phase submission

### The console shows the CAMARA calls it runs on

`api_log` has held one row per real Nokia call since the first build, but
the only reader was `/demo/api-log` — mounted only when
`SIGNALGUARD_DEMO_MODE=true`. So the one screen an operator actually
watches could not say whether the five APIs the whole product depends on
were answering at all.

That is an operational hole, not a presentation one. This console's queue
is not filled by anything it owns: a trip appears because a Geofencing
notification arrived, its risk numbers exist because Congestion Insights
and Location Retrieval answered, and its alarm is timed against a
reachability signal. When one of those starts returning 4xx the queue does
not turn red — it goes *quiet*, which on this screen is indistinguishable
from a calm corridor.

`GET /dashboard/api-activity` (`backend/app/api_activity.py`) is mounted in
every build and serves two views: a per-API roll-up, and the recent call
feed. The console renders a counter in the top bar, a panel under the
corridor stats, and — per trip — the exact requests behind the numbers in
that trip's detail panel.

Per-trip attribution needed one new thing. `NokiaClient._call` is given an
API name, a method and a path and knows nothing else, deliberately, so the
trip is carried in a `ContextVar` set at the tool boundary
(`agent/tools.py`'s `_on_behalf_of_trip`). The tool layer is the right
place: a single tool can fan out into more than one request, and each lands
in `api_log` separately.

Three outcomes, not two. A negative status now means "never attempted" —
today that is a contact message with no SMS gateway configured — and the
console draws it as `SKIP`, not as a failure. `whatsapp_client` used to log
those as status 0, which is the marker for "the request went out and got no
response". On an emergency console, "we did not try" and "we tried and
failed" call for opposite actions.

`tests/test_api_activity.py` drives `_call` for real through a
`MockTransport` rather than stubbing above it, because the logging happens
*inside* `_call` — a test that stubbed the client's methods would assert
nothing about the thing it claims to cover.

### A network fix that cannot be where the traveller is

The sandbox's shared simulator device reports a fixed European location
whichever zone is armed, so a trip's "last known fix" was a coordinate in
Hungary printed under a Jordanian corridor with no comment. On a live
operator line the same reading would mean a mis-provisioned line or the
wrong MSISDN. Either way it is not a position to search.

The console now measures the fix against the corridor's own gates and, past
50 km, says what it is looking at instead of handing a search team a number
nobody should drive to. The map was always plotting elapsed time along the
corridor rather than that coordinate; now the panel says so too.

### The app's position stream died silently on its first error

`LocationService` listened to `Geolocator.getPositionStream` with no
`onError`. Geolocator throws `LocationServiceDisabledException` the moment
the OS location toggle goes off, and platform errors are possible at any
time — so the first error became an unhandled Dart exception **and ended
the subscription**. Nothing restarted it. Positions then stopped for the
rest of the session, and the offline map — the screen that exists precisely
because the network cannot help — sat on "Locating…" indefinitely, with no
way for the traveller to know that what they were waiting for was never
going to arrive.

Reproduced on an Android 16 emulator inside a live crossing: one
`Unhandled Exception: The location service on the device is disabled` in
the log, and the dot never moved again.

Errors are now handled, classified (`LocationFault`), retried every 10
seconds — the commonest cause is a toggle a person can flip back — and,
the part that matters, *reportable*. The offline map names the switch when
there is one to name, and stops promising that "your position works without
signal" while it does not. `currentPosition()` no longer swallows the reason
either.

### Smaller things

- **A trip has an address.** `#trip/<id>` opens that crossing's panel, so a
  dispatcher handing one to a colleague can send the screen rather than a
  description of it. Written with `replaceState`, because the panel is a
  view of a row and not a navigation step.
- **`POST /demo/travellers` is idempotent.** `POST /travellers` already
  re-attached to an existing msisdn in demo mode; this path did not, and
  surfaced the raw unique-constraint violation as a 500 with a SQLAlchemy
  traceback. Registering the same number twice is normal here — the app
  onboards with one, then a script addresses the same traveller from the
  other side.
- **`scripts/seed_console.py`** walks four crossings through the demo
  primitives so a reviewer gets a populated triage queue in one command. It
  is not a scenario runner; `run_demo.py` still owns those, and its contract
  is still frozen.
- **The suite ignores a local demo aid.** `SIGNALGUARD_DEMO_BUFFER_HOLD_SEC`
  sleeps on every zone entry, and a developer who left it set in `.env` to
  record the approach screen should not find out by watching `pytest` take
  twenty minutes. `conftest.py` overrides it to 0.
- **A 4px sliver of accent green** sat at the top-left of the console on
  every page load: the skip link's `translateY(-120%)` did not clear its own
  box. Both dashboard pages also carry a favicon now, instead of a 404.
- **`docs/screenshots/`** ships in the repo. Every image in the pitch deck
  is a screenshot of the running build; `docs/` is otherwise ignored because
  it holds third-party reference PDFs.

## Repo layout

```
REVIEWERS.md how to run all of it, for somebody who has never seen it
backend/     FastAPI + LangGraph agent — Components 1 & 2
app/         Flutter traveller app — Component 3
dashboard/   emergency-centre web app — Component 4 (vanilla HTML/CSS/JS)
scripts/     run_demo.py     — demo conductor (do not modify its contract)
             seed_console.py — fill the console's queue in one command
docs/        product/technical/security/user-flow reference docs
docs/screenshots/  every image in the pitch deck, from the running build
```

## Running the dashboard

```bash
cd dashboard
python -m http.server 5500
```

Then open http://localhost:5500 with the backend already running (see
above) — the dashboard talks to it via the "Backend" field in its top bar
(defaults to `http://127.0.0.1:8000`, editable, persisted in
`localStorage`). CORS is wide open on the backend for this (`allow_origins:
["*"]`) since this is a prototype with no auth — see the note in
`backend/app/main.py` before shipping anything wider than a demo.

### Demo controls — http://localhost:5500/demo.html

A second page on the same static server that drives a crossing from the
browser: reset & arm, enter/exit gate, go dark/reconnect, advance the
virtual clock, and the two human replies (traveller answers Tier 0,
contact stands the alarm down). It shows the live trip state and the real
CAMARA calls with their latencies as they happen.

Two deliberate limits.

It is **not on the operator console**, it is its own page. `index.html` is
the one screen whose job is to be believed; a "simulate a gate crossing"
button next to a live trip queue tells a viewer the queue might be
synthetic too. The console has no knowledge this page exists.

It is **not a scenario runner**. `scripts/run_demo.py` owns the scenarios —
the beat sequences, the narration, the pass/fail assertions — and its
contract is frozen. A JavaScript copy of `scenario_overdue` would be a
second source of truth for the one thing that must not break on recording
day: the terminal run would keep passing while the browser one quietly
drifted. So the page exposes the primitives instead, one button per
endpoint the conductor already calls, and stepping through a scenario means
pressing them in order. The conductor stays the thing you record.

The page renders only when `GET /healthz` reports `demo: true`, which only
happens when the backend is running with `SIGNALGUARD_DEMO_MODE=true` — in
a production build the `/demo` router is never mounted and the panel says
so instead of drawing a single button.

Two additive fields were added to the backend for it, neither of which the
conductor reads: `demo: true` on `/healthz`, and `auth_token` on
`POST /demo/travellers` (the Tier 0 endpoint is traveller-authenticated).

## Running the backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env   # fill in NAC_API_KEY at minimum
./.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then, from the repo root. `SIGNALGUARD_ENV_FILE` is required: the conductor
loads `.env` from the current directory by default, but the real one lives
in `backend/`, so without it every scenario stops at preflight with
"NAC_API_KEY is not set".

```bash
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario happy
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario model-down
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario all --dry-run
```

Windows console note: the conductor prints Unicode box-drawing characters;
if your terminal isn't UTF-8, run with `PYTHONIOENCODING=utf-8` set first.

## Tests

```bash
cd backend
./.venv/Scripts/python -m pytest tests/ -q
```

`tests/test_fallback.py::test_escalation_fires_with_model_disabled` is the
one test that matters most — it proves the alarm still fires on schedule
when the LLM is disabled. See docs/SignalGuard_Technical_Feasibility.pdf
§5.4/§6.

The polled endpoints have a benchmark alongside the tests. It needs no
sandbox key and touches no real database:

```bash
cd backend
./.venv/Scripts/python tools/bench_hot_paths.py
./.venv/Scripts/python tools/bench_hot_paths.py --trips 60 --iters 80
```

## WhatsApp notifications — built, parked pending Twilio billing

Sends a message to every emergency contact at three points: dead-zone entry,
overdue (Tier 1), and safe exit. **This deviates from the docs'
silent-happy-path design** (Security & Privacy doc: contacts hear from
SignalGuard "because a crossing went overdue, and not otherwise") — a
deliberate choice made when this was added, not an oversight. See
`backend/app/notifications.py`'s docstring.

Fully wired into the trip state machine and tested (16/16 tests,
`backend/tests/test_whatsapp.py`) — but **not actually sending** yet.
Twilio's trial tier blocks custom message content on both channels:
WhatsApp requires an approved Content Template (Content Template Builder is
paid-only), and plain SMS hit `572006 Trial accounts can only use
predefined SMS templates`. Neither is a code problem — both need billing
added to the Twilio account (console → Account → Upgrade), a real payment
decision left to whoever owns that account.

Until then, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` stay blank and every
send is logged as a no-op (`backend/app/whatsapp_client.py`) rather than
failing the trip flow — the rest of the product works exactly as before
this was added. Once billing is added, fill in `.env` and it starts sending
for real with no code changes.

Inbound replies aren't wired up either way — that needs a public HTTPS
webhook Twilio can reach. Standing a trip down still goes through the
dashboard's resolve button or `/demo/contact-reply`.

## Secrets

`.env` is gitignored. Never commit a real `NAC_API_KEY` or `GOOGLE_API_KEY`,
even a sandbox one. See `backend/.env.example`.

## Nokia NaC integration notes (not in the original catalog doc)

Two things the reference docs didn't cover, found during real sandbox
integration and worth knowing before touching `nokia_client.py`:

1. **`x-rapidapi-host` header is required alongside `x-rapidapi-key`**, and
   its value is the RapidAPI *listing* name
   (`network-as-code.nokia.rapidapi.com`), not the request URL's host
   (`network-as-code.p-eu.apihub.nokia.io`). Omitting it, or using the URL
   host instead, returns a misleading `404 {"message":"API doesn't exists"}`
   even with a valid key.
2. **The `sink` field on Geofencing Subscriptions and Congestion Insights
   must be a resolvable hostname** — the sandbox validates it and rejects
   anything else with `400 INVALID_SINK`. Default is `https://example.com/...`;
   swap `SIGNALGUARD_WEBHOOK_BASE` for a real deployed webhook once one
   exists.
3. **Congestion Insights returns a JSON array** of recent time-bucketed
   readings (`congestionLevel: "Low"|"Medium"|"High"`), most recent first —
   not a single object. `get_congestion_insights` takes `result[0]`.

## Gemini model notes

Google retired the pinned `gemini-2.5-flash`/`gemini-2.5-pro` model IDs for
new API keys — both 404 with "no longer available to new users" even
though `ListModels` still lists them. `gemini-pro-latest` resolves to a
model with **zero free-tier quota** (429, `limit: 0`) on a fresh AI Studio
key; `gemini-flash-latest` was repeatedly 503-overloaded. The one that
actually answers on the free tier is `gemini-flash-lite-latest` — both
`GEMINI_MODEL` and `GEMINI_RISK_MODEL` default to it. The hackathon's own
mandatory requirement only names "Gemini via Google AI Studio," never a
pinned version, so this satisfies the actual rule. Swap `GEMINI_RISK_MODEL`
to a pro-tier model once billing is attached to the key, if a stronger risk
judgement is worth the cost.
