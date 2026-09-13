# SignalGuard — how to run it

MENA Ignite Hackathon 2026 · Team Beyond Signal · Prototype Phase.

SignalGuard is an AI agent that uses network-side CAMARA signals — not phone
GPS — to notice when a traveller enters a highway dead zone, prepare them
for it, and raise a tiered alarm if they do not come out the other side on
schedule. Four components: a FastAPI backend, a LangGraph agent, a Flutter
traveller app, and an emergency-centre console.

**The fastest useful thing:** the five-minute path below puts a live triage
queue on screen with real CAMARA calls behind it. Everything else here is
optional depth.

---

## What you need

| | |
|---|---|
| **Python 3.11+** | the backend and the demo conductor |
| **A Nokia Network as Code sandbox key** | free from [network-as-code.nokia.io](https://network-as-code.nokia.io) — this is what makes the CAMARA calls real |
| **A Google AI Studio key** | free tier; optional — without it the agent runs its deterministic fallback and says so |
| Flutter 3.44+ and an Android emulator | only for the traveller app (section 5) |

No Docker, no database server, no build step for the console. SQLite and
static files.

---

## 1 · Start the backend

```bash
cd backend
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

macOS / Linux:

```bash
.venv/bin/pip install -r requirements.txt && cp .env.example .env
```

Open `backend/.env` and set **`NAC_API_KEY`** at minimum. `GOOGLE_API_KEY`
is worth adding too — with it you see real Gemini reasoning in the decision
records; without it the agent falls back to the deterministic risk model,
which is a supported path, not a degraded one (see section 4).

Then, from `backend/`:

```bash
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check it:

```bash
curl http://127.0.0.1:8000/healthz
```

`{"ok":true,"demo":true,"nac":{"apis":[...five...]},"agent":{...}}`

## 2 · Start the console

In a second terminal, from the repo root:

```bash
python -m http.server 5500 --directory dashboard
```

Open <http://localhost:5500>. The **Backend** field in the top bar defaults
to `http://127.0.0.1:8000` and is editable.

The queue will be empty, and it will say so — correctly, because nobody has
crossed a gate yet.

## 3 · Put four crossings through it

In a third terminal, from the repo root:

```bash
python scripts/seed_console.py
```

That registers four travellers, arms the corridor's geofences on the network
for real, and walks them through the states a dispatcher sorts by. It takes
about a minute, most of which is real round-trips to the Nokia sandbox. When
it finishes the console shows:

- **one red** crossing, escalated to Tier 2, with the agent's own decision
  record and the reasoning behind the risk score;
- **one amber** crossing whose monitoring window is nearly up;
- **one green** crossing in transit;
- **one completed** crossing under the History tab, which is also what feeds
  the learned corridor times.

### Where to see the CAMARA API calls

This is the part worth two minutes of a reviewer's time.

- **Top bar** — a live counter: `CAMARA 23 calls · 5/5 APIs`. It goes amber
  or red if an API starts failing.
- **Left column, "Network API activity"** — all five CAMARA APIs with call
  counts, mean latency and last HTTP status, plus a feed of the individual
  requests (method, path, status, latency, age). Every one of the five is
  listed even at zero calls, because "not needed yet" and "unreachable" are
  different facts.
- **Click a trip → "CAMARA calls for this crossing"** — the exact requests
  that produced the numbers in that panel, attributed per trip.

The endpoint behind all of it is `GET /dashboard/api-activity`, mounted in
every build. Raw:

```bash
curl "http://127.0.0.1:8000/dashboard/api-activity?limit=5"
```

Nothing there is typed into a fixture. `backend/app/nokia_client.py` writes
one `api_log` row per real HTTP call to Nokia; the console reads that table.

### Driving a crossing by hand

<http://localhost:5500/demo.html> is a separate page with one button per
endpoint — enter/exit gate, go dark, reconnect, advance the virtual clock,
and the two human replies that stop an escalation. It shows the live trip
state and the real CAMARA calls with their latencies as they happen. It is
deliberately not on the operator console: `index.html` is the screen whose
job is to be believed, and a "simulate a crossing" button next to a live
queue undermines that.

## 4 · The scenarios, and the one test that matters

`scripts/run_demo.py` is the narrated conductor — four end-to-end scenarios
against the live sandbox, with pass/fail assertions. From the repo root:

```bash
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario happy
```

```bash
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario overdue
```

```bash
SIGNALGUARD_ENV_FILE=backend/.env python scripts/run_demo.py --scenario model-down
```

`SIGNALGUARD_ENV_FILE` is required: the conductor loads `.env` from the
current directory by default, and the real one lives in `backend/`. On a
Windows console that is not UTF-8, set `PYTHONIOENCODING=utf-8` first — the
conductor prints box-drawing characters.

**`--scenario model-down` is the one to run if you only run one.** It
switches the LLM off mid-flight and shows the alarm still firing on the
timer. An AI safety product whose alarm depends on the model answering is
not a safety product.

Tests:

```bash
cd backend && .venv\Scripts\python -m pytest tests/ -q     # 57 tests
cd app && flutter test                                     # 17 tests
```

`tests/test_fallback.py::test_escalation_fires_with_model_disabled` is the
one that matters most, for the same reason.

There is also a benchmark for the two endpoints that are polled forever —
no sandbox key needed, no real database touched:

```bash
cd backend && .venv\Scripts\python tools\bench_hot_paths.py
```

## 5 · The traveller app on an emulator

```bash
cd app
flutter pub get
flutter run -d <emulator-id>
```

The app defaults to `http://10.0.2.2:8000`, which is how an Android emulator
reaches the host machine. Onboarding is five steps and ends with a test
message to the contact you entered — that step reports honestly that no SMS
provider is wired up in this build, rather than showing a tick for a message
nobody received.

To see the screens in order without waiting for a real drive:

1. finish onboarding, then note your phone number;
2. arm the corridor and cross the gate for that traveller — the demo page
   (<http://localhost:5500/demo.html>) does both;
3. turn the emulator's radio off (`adb shell svc wifi disable` and
   `adb shell svc data disable`) to get the offline map; the app switches on
   connectivity, not on a button;
4. `adb emu geo fix 36.006 29.600` puts the device inside the corridor so
   the offline map has a position to draw.

A new trip passes through the BUFFER state in about three seconds, which is
long enough for the agent and too short to read. To hold it there while you
look at the approach screen, set `SIGNALGUARD_DEMO_BUFFER_HOLD_SEC=40` in
`backend/.env` and restart the backend. Never leave that set outside a demo:
it delays the moment a real crossing becomes monitored.

---

## Running without a Nokia sandbox key

The product still runs; it just stops being evidence.

| | |
|---|---|
| Backend, agent, state machine, console, app | work |
| The five CAMARA calls | fail, and the console's API panel shows them failing rather than hiding it |
| `run_demo.py` | stops at preflight with `NAC_API_KEY is not set` |
| `pytest` | passes — the tests stub the Nokia seam deliberately, so no test ever depends on a live third party |

## Known limits, stated plainly

- **The sandbox's simulator device reports a fixed European location**, so a
  trip's "last known fix" is a coordinate in Hungary rather than on Highway
  15. The console detects a fix that far from the corridor and says so in
  the trip panel instead of handing a search team a number nobody should
  drive to. The map plots elapsed time along the corridor, not that fix.
- **Gate crossings and reachability changes are synthetic.** Nokia's sandbox
  cannot move a device, so the trigger is simulated — and the demo page says
  that in a banner. Everything downstream of the trigger is real.
- **Position is a range, never a point.** The network gives exactly one
  location fix per crossing, at the entry gate. The console draws the
  stretch of corridor a traveller could be in, widening with elapsed time,
  because a Tier 2 handoff needs a search area and a false point is not one.
- **Contact notifications are built and tested but do not send.** Twilio's
  trial tier blocks custom message content on both WhatsApp and SMS, which
  needs billing on the account rather than a code change. Every send is
  logged as a no-op, and the console shows those rows as `SKIP` rather than
  as failures — "we never tried" and "we tried and failed" call for
  opposite actions. See Known limitations in `README.md`.
- **No auth on the console, and CORS is wide open.** It is a prototype; the
  note in `backend/app/main.py` says what would have to change first.

## Publishing the console (not done yet)

The console is a pure client — HTML, CSS and one JS file, no build step — so
hosting it is a file copy. What it is *not* is a demo: it reads the trip
queue, the corridor map and the CAMARA call log from a backend, and there is
no public backend to read from, because that backend holds real phone
numbers and calls a real operator API with a real key. A hosted copy with
nothing behind it says so on screen rather than looking broken.

To put it on GitHub Pages, from the repo root:

```bash
git subtree push --prefix dashboard origin gh-pages
```

Then, in the repository's **Settings → Pages**, set the source to the
`gh-pages` branch, root folder. The URL is
`https://<owner>.github.io/<repo>/`. The repository must be public, or on a
plan that allows Pages for private repositories, and enabling Pages needs
admin on the repository.

One caveat worth knowing before demoing from a hosted copy: a page served
over HTTPS calling `http://127.0.0.1:8000` is allowed by current browsers as
a localhost exception, but it is not guaranteed, and Private Network Access
checks may block it. Serving the console from `localhost` too (section 2) is
the path that always works.

## Where things are

```
backend/     FastAPI + the LangGraph agent
  app/api_activity.py     the CAMARA call telemetry the console reads
  app/nokia_client.py     the five CAMARA APIs, and the notes from
                          integrating them for real
  app/state_machine.py    the trip lifecycle and the escalation ladder
  tests/                  57 tests
app/         Flutter traveller app
dashboard/   emergency-centre console (vanilla HTML/CSS/JS, no build)
scripts/     run_demo.py (scenarios) · seed_console.py (populate the queue)
docs/        product, technical, security and business-model references
             plus docs/screenshots/ — every screenshot in the pitch deck
```

See `README.md` for the project overview, architecture and integration notes.
