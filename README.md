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

## Repo layout

```
backend/     FastAPI + LangGraph agent — Components 1 & 2
app/         Flutter traveller app — Component 3
dashboard/   emergency-centre web app — Component 4 (vanilla HTML/CSS/JS)
scripts/     run_demo.py — demo conductor (do not modify its contract)
docs/        product/technical/security/user-flow reference docs
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

## Running the backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env   # fill in NAC_API_KEY at minimum
./.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then, from the repo root:

```bash
python scripts/run_demo.py --scenario happy
python scripts/run_demo.py --scenario model-down
python scripts/run_demo.py --scenario all --dry-run   # no sandbox calls, rehearsal only
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
