#!/usr/bin/env python3
"""
SignalGuard — Interactive Scenario Runner
==========================================

You drive the pacing. Each beat prints what it's about to do, does it for
real against the live backend (and your Android emulator, if adb is
found), then waits for you to press Enter before moving on — so you can
check the app and the dashboard at your own speed instead of racing a
scripted timer.

This is NOT scripts/run_demo.py — that one's contract is fixed and not to
be touched (see its own docstring). This is a separate, simpler tool for
manual walkthroughs, built the same way: stdlib only, no dependencies to
install.

Usage:
    python scripts/run_scenario_interactive.py

Needs:
    - Backend running (see README.md — uvicorn on :8000 by default)
    - Optional: adb on PATH (or set ADB_PATH below) to auto-toggle the
      emulator's connectivity and restore your app session. Without adb,
      the script still runs — it'll just tell you what to do by hand.

Prompts you'll be asked, up front:
    - Reuse your currently-onboarded app session, or register a fresh one?
    - Which scenario: overdue (Tier 1 -> Tier 2 -> safe exit), or happy
      (silent close)?
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252, which can't encode the arrows/dashes
# below and would otherwise crash on the first print. UTF-8 output is safe
# to force here regardless of platform.
for _stream in (sys.stdout, sys.stderr, sys.stdin):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best-effort; never block the script on this
        pass

BACKEND = os.environ.get("SIGNALGUARD_BACKEND", "http://127.0.0.1:8000")
ZONE_ID = "JO-H15-MUDAWWARA"
PACKAGE = "io.signalguard.signalguard"
DEMO_MSISDN = "+962790000001"
DEMO_NAME = "Sultan"
CONTACT_NAME = "Omar"
CONTACT_MSISDN = "+962790000002"


# -- tiny HTTP client (stdlib only, matches run_demo.py's own approach) -----

def api(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    url = BACKEND.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        print(f"  {C.red('[!]')} {method} {path} -> {exc.code}: {raw.decode('utf-8', 'replace')[:300]}")
        return {}
    return json.loads(raw) if raw else {}


# -- console: colour, boxes, clearly separated sections ----------------------

class _Colors:
    """ANSI colour helpers — no-op automatically when stdout isn't a real
    terminal (piped output, redirected to a file) so nothing ever prints
    raw escape codes where they'd just be noise."""

    def __init__(self) -> None:
        self.on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def amber(self, t: str) -> str: return self._wrap("33", t)
    def red(self, t: str) -> str: return self._wrap("31", t)
    def cyan(self, t: str) -> str: return self._wrap("36", t)
    def magenta(self, t: str) -> str: return self._wrap("35", t)


C = _Colors()

STATE_COLOR = {
    "BUFFER": C.amber, "ACTIVE": C.green, "EXITED": C.green,
    "OVERDUE": C.red, "TIER1_ALERTED": C.red, "TIER2_ESCALATED": C.red,
    "RESOLVED": C.green,
}
RISK_COLOR = {"LOW": C.green, "ELEVATED": C.red}


def colored_state(state: str) -> str:
    paint = STATE_COLOR.get(state, C.dim)
    return C.bold(paint(state))


def colored_risk(risk: str) -> str:
    paint = RISK_COLOR.get(risk, C.dim)
    return C.bold(paint(risk))


def banner(title: str) -> None:
    width = 78
    print()
    print(C.cyan("═" * width))
    print(C.bold(f"  {title}"))
    print(C.cyan("═" * width))


def section(title: str) -> None:
    width = 78
    print()
    print(C.dim("─" * width))
    print(C.bold(C.cyan(f"  {title}")))
    print(C.dim("─" * width))


def beat(name: str, note: str = "") -> None:
    print()
    print(f"{C.cyan('▸')} {C.bold(name)}")
    if note:
        print(f"  {C.dim(note)}")


def pause(prompt: str = "press Enter to continue, Ctrl+C to stop ⏎ ") -> None:
    try:
        input(f"\n  {C.dim(prompt)}")
    except (EOFError, KeyboardInterrupt):
        print("\nstopped.")
        sys.exit(0)


def field(text: str, value, color=None) -> None:
    shown = color(str(value)) if color else str(value)
    print(f"    {C.dim(text + ':'):<38} {shown}")


def check(text: str) -> None:
    print(f"  {C.green('✓')} {text}")


def hint(text: str) -> None:
    print(f"  {C.magenta('→')} {text}")


# -- decision record parsing --------------------------------------------------
# trip.decision_record is a human-readable multi-line string, not JSON (by
# design — see backend/app/agent/decision_record.py), formatted as
# "label   value" per line, wrapped onto continuation lines with just
# leading spaces. Split it back into fields so it can be displayed as
# clearly-labelled data instead of a raw text dump.

def parse_decision_record(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key = None
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent <= 2 and " " in stripped:
            key, _, value = stripped.partition(" ")
            current_key = key.strip()
            fields[current_key] = value.strip()
        elif current_key:
            fields[current_key] += " " + stripped.strip()
    return fields


def print_ai_decision(trip: dict) -> None:
    """The centrepiece: what the agent was told, what it decided, and
    why — pulled apart into labelled fields instead of one text blob, so
    the actual judgement (not just the fact that a record exists) is
    visible at a glance."""
    record = trip.get("decision_record") or ""
    parsed = parse_decision_record(record)
    is_fallback = "UNAVAILABLE" in parsed.get("model", "")

    section("🤖  AI AGENT DECISION")

    if is_fallback:
        field("model", "UNAVAILABLE → deterministic fallback", C.amber)
        hint("Gemini was off/unreachable — the deterministic risk model set the window instead.")
        hint("This is the safety-net path: the alarm still fires on schedule either way.")
    else:
        field("model", parsed.get("model", "?"), C.cyan)

    field("signals in", parsed.get("signals", "?"))
    print()

    risk = trip.get("risk") or "?"
    field("risk score", risk, colored_risk)

    predicted = trip.get("predicted_crossing_min")
    window = trip.get("monitoring_window_min")
    if predicted and window:
        buffer_min = window - predicted
        field("predicted crossing", f"{predicted} min")
        field("monitoring window", f"{window} min  (+{buffer_min} min buffer)", C.bold)

    qod = "request_qod_session" in parsed.get("tools", "")
    field("connectivity boost (QoD)", "requested" if qod else "not requested", C.cyan if qod else C.dim)
    field("tools called", parsed.get("tools", "?"))

    if not is_fallback and parsed.get("reasoning"):
        print()
        print(f"    {C.dim('reasoning:')}")
        wrapped = textwrap.wrap(parsed["reasoning"], width=68)
        for line in wrapped:
            print(f"      {C.bold(line)}")

    print()
    field("action taken", parsed.get("action", "?"), C.green)


def print_state(trip: dict, label: str = "current state") -> None:
    """A quick status line for beats after the first — no need to re-dump
    the full decision every time, just what changed."""
    field(label, colored_state(trip.get("state", "?")))
    notes = trip.get("notifications") or []
    if notes:
        field("notifications sent", ", ".join(notes), C.red)


# -- adb (best-effort — everything still works without it) -------------------

def find_adb() -> str | None:
    env_path = os.environ.get("ADB_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    guess = Path.home() / "AppData/Local/Android/sdk/platform-tools/adb.exe"
    if guess.exists():
        return str(guess)
    return None


ADB = find_adb()


def adb(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    if not ADB:
        return None
    try:
        return subprocess.run(
            [ADB, *args], capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — adb is a nice-to-have, never fatal
        print(f"    (adb {' '.join(args)} failed: {exc})")
        return None


def set_connectivity(online: bool) -> None:
    state = "enable" if online else "disable"
    if not ADB:
        action = "Turn WiFi + mobile data back ON" if online else "Turn WiFi + mobile data OFF"
        print(f"    adb not found — {action} on the emulator yourself (Extended controls > Cellular/WiFi), "
              f"or install platform-tools and re-run.")
        return
    adb("shell", "svc", "wifi", state)
    adb("shell", "svc", "data", state)
    label = C.green("ONLINE") if online else C.red("OFFLINE (dead zone)")
    print(f"    emulator connectivity: {label}")


def restore_app_session(traveller_id: str, auth_token: str, zone: dict) -> bool:
    """Writes the traveller's session straight into the app's
    SharedPreferences via `adb run-as`, skipping onboarding taps entirely.
    Best-effort: prints manual instructions and returns False if adb, the
    device, or a debug build of the app isn't available."""
    if not ADB:
        return False

    prefs_xml = f"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
<boolean name="flutter.onboarded" value="true" />
<string name="flutter.auth_token">{auth_token}</string>
<string name="flutter.traveller_id">{traveller_id}</string>
<string name="flutter.traveller_name">{DEMO_NAME}</string>
<string name="flutter.msisdn">{DEMO_MSISDN}</string>
<string name="flutter.contacts">[{{&quot;name&quot;:&quot;{CONTACT_NAME}&quot;,&quot;msisdn&quot;:&quot;{CONTACT_MSISDN}&quot;}}]</string>
<string name="flutter.zone">{json.dumps(zone).replace('"', "&quot;")}</string>
<string name="flutter.backend_url">http://10.0.2.2:8000</string>
</map>
"""
    tmp_local = Path.home() / ".signalguard_prefs_tmp.xml"
    tmp_local.write_text(prefs_xml, encoding="utf-8")

    push = adb("push", str(tmp_local), "/data/local/tmp/prefs.xml")
    if not push or push.returncode != 0:
        print(f"    adb push failed: {(push.stderr or push.stdout).strip() if push else 'no adb response'}")
        return False
    adb("shell", "chmod", "666", "/data/local/tmp/prefs.xml")
    cp = adb(
        "shell", "run-as", PACKAGE, "cp", "/data/local/tmp/prefs.xml",
        f"/data/data/{PACKAGE}/shared_prefs/FlutterSharedPreferences.xml",
    )
    tmp_local.unlink(missing_ok=True)
    if not cp or cp.returncode != 0:
        print(f"    adb run-as cp failed: {(cp.stderr or cp.stdout).strip() if cp else 'no adb response'}")
        print(f"    (is the app installed and is this a debug build? package={PACKAGE})")
        return False

    adb("shell", "am", "force-stop", PACKAGE)
    time.sleep(1)
    adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
    return True


OVERDUE_STEPS = [
    "Arm the zone — real Nokia Geofencing Subscriptions created (entry + exit gates)",
    "Entry gate crossing — real Nokia + Gemini calls, AI sets the monitoring window",
    "Emulator goes OFFLINE (dead zone) — app switches to the offline map",
    "Clock advances partway through the window — still on track, dashboard stays calm",
    "Clock advances past the window — OVERDUE -> TIER1_ALERTED (contact notified)",
    "Clock advances through the grace period — TIER1_ALERTED -> TIER2_ESCALATED (emergency centre alerted)",
    "Emulator back ONLINE, exit gate crossing — resolves cleanly even though it was overdue",
]

HAPPY_STEPS = [
    "Arm the zone — real Nokia Geofencing Subscriptions created (entry + exit gates)",
    "Entry gate crossing — real Nokia + Gemini calls, AI sets the monitoring window",
    "Emulator goes OFFLINE (dead zone) — app switches to the offline map",
    "Clock advances most of the predicted crossing time — still well inside the window",
    "Emulator back ONLINE, exit gate crossing on time — silent close, no notifications",
]


def print_plan(steps: list[str]) -> None:
    section("WHAT THIS SCENARIO WILL DO")
    for i, step in enumerate(steps, 1):
        print(f"    {C.cyan(f'{i}.')} {step}")
    print()
    hint("You'll be asked to press Enter before each step — check the app/dashboard at your own pace.")


# -- scenario steps -----------------------------------------------------------

def setup() -> tuple[str, str]:
    banner("SIGNALGUARD — INTERACTIVE WALKTHROUGH")
    field("dashboard", "http://localhost:5500", C.cyan)
    field("backend", BACKEND, C.cyan)

    section("SETUP")
    health = api("GET", "/healthz")
    if not health:
        print(f"\n{C.red('Backend not reachable at')} {BACKEND}")
        print("Start it first: cd backend && ./.venv/Scripts/python -m uvicorn app.main:app --port 8000")
        sys.exit(1)
    field("agent model", health.get("agent", {}).get("model"))
    field("adb", ADB or "not found (emulator control will be manual)")

    reuse = input(
        f"\n  {C.dim('Reuse an already-onboarded app session? [y/N]')} "
    ).strip().lower() == "y"

    if reuse:
        traveller_id = input("  traveller_id (from an earlier registration): ").strip()
        return traveller_id, ""

    print(f"\n  {C.dim('Resetting backend state (clears all trips/travellers)...')}")
    api("POST", "/demo/reset")
    reg = api("POST", "/travellers", {
        "msisdn": DEMO_MSISDN, "name": DEMO_NAME,
        "contacts": [{"name": CONTACT_NAME, "msisdn": CONTACT_MSISDN}],
    })
    traveller_id = reg.get("traveller_id", "")
    auth_token = reg.get("auth_token", "")
    field("traveller_id", traveller_id)
    if not traveller_id:
        print(f"  {C.red('registration failed')} — see error above.")
        sys.exit(1)

    zones = api("GET", "/zones")
    zone = zones[0] if zones else None
    if not zone:
        print(f"  {C.red('no zone found')} — is the backend seeded?")
        sys.exit(1)

    restored = restore_app_session(traveller_id, auth_token, zone)
    if restored:
        check("app session restored on the emulator — it should now show the idle screen.")
    else:
        print(f"  {C.amber('could not auto-restore the app session')} (no adb, or run-as failed).")
        print(f"  onboard manually in the app with phone number {DEMO_MSISDN}.")

    return traveller_id, auth_token


def arm(traveller_id: str, battery: int) -> None:
    beat("Arming zone + setting battery", f"battery={battery}% — real Nokia Geofencing Subscriptions created")
    result = api("POST", f"/demo/zones/{ZONE_ID}/arm", {"traveller_id": traveller_id})
    field("entry subscription", result.get("entry_subscription_id"))
    field("exit subscription", result.get("exit_subscription_id"))
    api("POST", "/demo/battery", {"traveller_id": traveller_id, "level": battery})


def active_trip_id(traveller_id: str) -> str | None:
    active = api("GET", f"/demo/trips/active?traveller_id={traveller_id}")
    return active.get("trip_id") if active else None


def fetch_trip(trip_id: str) -> dict:
    return api("GET", f"/trips/{trip_id}")


def do_entry(traveller_id: str) -> tuple[str, dict]:
    beat("Entry gate crossing", "real Nokia calls + real Gemini reasoning — takes a few seconds")
    t0 = time.monotonic()
    api("POST", "/demo/simulate-gate-event", {
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "entry",
    })
    elapsed = time.monotonic() - t0
    trip_id = active_trip_id(traveller_id)
    if not trip_id:
        print(f"  {C.red('no trip appeared')} — check the backend log.")
        sys.exit(1)
    trip = fetch_trip(trip_id)
    field("real round-trip time", f"{elapsed:.1f}s")
    field("state", colored_state(trip.get("state", "?")))

    print_ai_decision(trip)

    section("CHECK NOW")
    hint('App should show "You\'re offline-ready" (or briefly "Preparing you now…").')
    hint(f"Dashboard (http://localhost:5500) should show {DEMO_NAME}, green, \"In transit\".")
    return trip_id, trip


def advance(seconds: int, why: str) -> None:
    beat(f"Advancing the virtual clock by {seconds // 60} min", why)
    now = api("POST", "/demo/clock/advance", {"seconds": seconds})
    field("clock now", now.get("now"))


def do_overdue_scenario(traveller_id: str, trip_id: str, trip: dict) -> None:
    set_connectivity(online=False)
    pause("Check the app — should show the offline map with a real route. Enter to continue ⏎ ")

    window = trip.get("monitoring_window_min") or 100
    partial = int(window * 0.7) * 60
    pause()

    advance(partial, "still under the window — dashboard should tick but stay green/amber")
    print_state(fetch_trip(trip_id))
    pause("Check the dashboard now. Enter to push past the window ⏎ ")

    advance((window - int(window * 0.7) + 5) * 60, "past the window — triggers OVERDUE → TIER1_ALERTED")
    print_state(fetch_trip(trip_id))
    pause("Contact would be texted now. Check the dashboard (should be red). Enter for the grace period ⏎ ")

    advance(20 * 60, "grace period elapses with no reply — triggers TIER2_ESCALATED")
    print_state(fetch_trip(trip_id))
    pause("Emergency centre alerted — check the dashboard's \"Action needed\" trip. Enter to bring the traveller out safely ⏎ ")

    beat("Exit gate crossing while overdue", "traveller was just running late — this must resolve cleanly, not stay stuck")
    set_connectivity(online=True)
    api("POST", "/demo/simulate-gate-event", {
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "exit",
    })
    print_state(fetch_trip(trip_id), label="final state")

    section("CHECK NOW")
    hint("Dashboard should show 0 monitored.")
    hint("App should show the reconnection screen, then settle back to idle.")


def do_happy_scenario(traveller_id: str, trip_id: str, trip: dict) -> None:
    set_connectivity(online=False)
    pause("Check the app — offline map with a real route. Enter to continue ⏎ ")

    window = trip.get("monitoring_window_min") or 90
    crossing = trip.get("predicted_crossing_min") or int(window * 0.7)
    advance(int(crossing * 0.9) * 60, "most of the predicted crossing time, still well inside the window")
    print_state(fetch_trip(trip_id))
    pause("Check the dashboard — still green. Enter to bring the traveller out on time ⏎ ")

    beat("Exit gate crossing, on time")
    set_connectivity(online=True)
    api("POST", "/demo/simulate-gate-event", {
        "traveller_id": traveller_id, "zone_id": ZONE_ID, "gate": "exit",
    })
    api("POST", "/demo/simulate-reachability", {"traveller_id": traveller_id, "reachable": True})
    print_state(fetch_trip(trip_id), label="final state")

    section("CHECK NOW")
    hint("Silent close — no notifications fired. Dashboard should show 0 monitored.")


def main() -> None:
    traveller_id, _ = setup()
    battery = input(f"\n  {C.dim('Battery level to simulate [18]:')} ").strip()
    battery = int(battery) if battery else 18

    scenario = input(
        f"\n  {C.dim('Scenario — [o]verdue (Tier 1 -> Tier 2 -> safe exit) or [h]appy (silent close)? [o]')} "
    ).strip().lower()

    print_plan(HAPPY_STEPS if scenario == "h" else OVERDUE_STEPS)
    pause("Ready. Enter to arm the zone and open the corridor ⏎ ")
    arm(traveller_id, battery)
    pause()

    trip_id, trip = do_entry(traveller_id)
    pause()

    if scenario == "h":
        do_happy_scenario(traveller_id, trip_id, trip)
    else:
        do_overdue_scenario(traveller_id, trip_id, trip)

    banner("DONE")


if __name__ == "__main__":
    main()
