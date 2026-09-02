#!/usr/bin/env python3
"""
SignalGuard — Demo Conductor
============================

Drives one complete crossing end to end so the demo is a repeatable performance
rather than a one-off recording.

WHAT IS REAL AND WHAT IS SYNTHETIC — READ THIS FIRST
    Nokia's Network as Code sandbox does not expose a way to move a simulator
    device's location. The simulator numbers (see FIXED_SIMULATOR_DEVICES
    below) are fixed test fixtures with pre-set behaviour on Nokia's side —
    you query them, you do not drive them around a map. An earlier version of
    this script assumed a location-injection endpoint that does not exist in
    the published API catalog. This version does not.

    So the fakery is isolated to exactly one seam, and named honestly on
    screen rather than hidden:

      SYNTHETIC  The "the device just crossed a gate" trigger. The backend's
                 demo-only /demo/simulate-gate-event endpoint injects a
                 CAMARA-shaped area-entered/area-left event straight into the
                 same webhook handler a real Nokia notification would hit.
                 This is the one thing Nokia's sandbox cannot do for us.

      REAL       Everything downstream of that trigger: the trip state
                 machine, the agent run, and the backend's actual calls to
                 Location Retrieval, Congestion Insights, Quality on Demand
                 and Device Reachability Status against a real fixed
                 simulator device on Nokia's sandbox — visible in the network
                 activity pane with real latencies and real response bodies.
                 The Geofencing Subscription itself is also created for real
                 (see NacSandboxClient) so it shows in the API log even
                 though it cannot fire from device movement.

    Say this on camera: a caption reading "gate crossing simulated · API
    responses live from Nokia NaC sandbox" is accurate and is exactly the
    kind of labelling the security & privacy document already commits you to.

    python scripts/run_demo.py --scenario overdue
    python scripts/run_demo.py --scenario model-down --step     # live, Phase 2
    python scripts/run_demo.py --scenario all --dry-run         # rehearse offline

SCENARIOS
    happy       Crossing completes; trip closes silently at reconnection.
                Asserts that nobody was notified.
    overdue     The main narrative. Window expires, Tier 1 SMS to the contact,
                no reply, Tier 2 escalation with search context.
    stand-down  Overdue through Tier 1, then the contact replies "he's fine".
                Asserts that no emergency centre was ever paged.
    model-down  The LLM is disabled before the crossing. The deterministic risk
                model sets the window and the alarm still fires on the timer.
                This is the ten seconds of video that proves the safety claim.

WHAT THIS SCRIPT ASSUMES THE BACKEND EXPOSES
    These are demo-only control endpoints. Mount them under /demo and gate them
    behind SIGNALGUARD_DEMO_MODE so they cannot exist in a production build.

      GET  /healthz                        -> {ok, nac:{apis:[...]}, agent:{model, enabled}}
      POST /demo/reset                     -> clears trips, subscriptions, api log
      POST /demo/travellers                {msisdn, name, contacts:[{name,msisdn}]}
                                           -> {traveller_id}
      POST /demo/zones/{zone_id}/arm       {traveller_id}
                                           -> {entry_subscription_id, exit_subscription_id}
                                           creates a REAL CAMARA Geofencing Subscription
                                           against nac_device (below) — visible in the
                                           network pane, even though nothing will move it
      POST /demo/simulate-gate-event       {traveller_id, zone_id, gate:"entry"|"exit"}
                                           -> {}   injects the CAMARA-shaped event that
                                           Nokia's sandbox cannot produce by itself; the
                                           backend then makes its normal REAL calls
                                           (Location Retrieval, Congestion Insights, QoD)
                                           against nac_device in response, exactly as it
                                           would for a genuine network event
      POST /demo/simulate-reachability     {traveller_id, reachable: bool}
                                           -> {}   injects the reachability change that
                                           would normally arrive via the Device
                                           Reachability Status subscription
      GET  /demo/trips/active              ?traveller_id=  -> {trip_id, state} | null
      GET  /trips/{trip_id}                -> full trip: state, risk, predicted_exit_at,
                                              decision_record, notifications[]
      POST /demo/clock/advance             {seconds} -> {now}          (virtual clock)
      POST /demo/battery                   {traveller_id, level}
      POST /demo/agent/model               {enabled: bool}
      POST /demo/contact-reply             {trip_id, reply:"safe"}
      GET  /demo/api-log                   ?since=<iso> -> [{ts, api, endpoint,
                                              latency_ms, status, cost_usd}]

    If you do not build a virtual clock, run with --clock real: the conductor
    then sleeps window/DEMO_TIME_SCALE seconds instead of jumping the clock.

FIXED SIMULATOR DEVICES (from Nokia's own catalog — do not invent others)
    Phone numbers:              +99999991000, +99999991001, +99999990400,
                                 +99999990404, +99999990422, +99999990500,
                                 +99999990502, +99999990503, +99999990504
    Network access identifiers: 8D8AC610-566D-4EF0-9C22-186B2A5ED793-1000@testcsp.net
                                 (and -1001, -1002, -1003, -0400, -0404, -0422,
                                 -0500, -0502, -0503, -0504 @testcsp.net)

    Your backend's real Nokia calls (Location Retrieval, Congestion Insights,
    QoD, Device Reachability) must address one of these — not the demo
    traveller's own-invented MSISDN, which only exists inside your backend.
    Pick one fixed device per zone and note whatever fixed location/status
    Nokia actually returns for it; that becomes your "entry point" and
    "last known location" values on screen, for real.

THE ONE THING TO CONFIRM ON DAY 1
    That NAC_API_KEY authenticates against network-as-code.p-eu.apihub.nokia.io
    with header x-rapidapi-key, and that a fixed device returns sane values
    for Location Retrieval / Device Reachability. Conductor.preflight() makes
    this call for real, every run, so a bad key fails loud before recording.

Dependencies: none. Uses httpx if the project already has it, stdlib urllib
otherwise — deliberately, so this runs on any teammate's laptop on recording day
without a working pip.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import httpx  # optional: used when the backend project already depends on it
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Tiny HTTP client
# --------------------------------------------------------------------------

class HttpError(Exception):
    """Transport failure — the host is unreachable, not a 4xx/5xx."""


class Response:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text) if self.text.strip() else {}


class Http:
    def __init__(self, base_url: str, headers: dict | None = None,
                 timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout
        self._x = (httpx.Client(base_url=self.base_url, headers=self.headers,
                                timeout=timeout) if httpx else None)

    def request(self, method: str, path: str, body: Any = None,
                params: dict | None = None) -> Response:
        if self._x is not None:
            try:
                r = self._x.request(method, path, json=body, params=params)
            except Exception as exc:  # httpx.HTTPError and friends
                raise HttpError(str(exc)) from exc
            return Response(r.status_code, r.content)

        url = self.base_url + path
        if params:
            query = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers,
                                     method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(resp.status, resp.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read())
        except urllib.error.URLError as exc:
            raise HttpError(str(exc.reason)) from exc


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_env_file(path: str = ".env") -> None:
    """Minimal .env loader so the conductor is self-contained."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --------------------------------------------------------------------------
# Nokia's real fixed simulator devices (from the published API catalog —
# these are not ours to invent; use exactly these or the sandbox rejects you)
# --------------------------------------------------------------------------

FIXED_SIMULATOR_PHONE_NUMBERS: list[str] = [
    "+99999991000", "+99999991001", "+99999990400", "+99999990404",
    "+99999990422", "+99999990500", "+99999990502", "+99999990503",
    "+99999990504",
]

FIXED_SIMULATOR_NAIS: list[str] = [
    f"8D8AC610-566D-4EF0-9C22-186B2A5ED793-{suffix}@testcsp.net"
    for suffix in ("1000", "1001", "1002", "1003", "0400", "0404", "0422",
                   "0500", "0502", "0503", "0504")
]


@dataclass
class Config:
    backend: str = "http://localhost:8000"
    nac_base: str = "https://network-as-code.p-eu.apihub.nokia.io"
    nac_key: str = ""
    nac_device: str = "+99999991000"        # a real Nokia fixed simulator device
    msisdn: str = "+962790000001"           # your OWN backend's demo traveller id —
                                             # never sent to Nokia, only to your SMS
                                             # provider and shown on the phone screen
    traveller_name: str = "Sultan"
    contact_name: str = "Omar"
    contact_msisdn: str = "+962790000002"
    time_scale: float = 60.0
    http_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            backend=os.getenv("SIGNALGUARD_BACKEND", cls.backend),
            nac_base=os.getenv("NAC_BASE", cls.nac_base),
            nac_key=os.getenv("NAC_API_KEY", ""),
            nac_device=os.getenv("NAC_DEVICE", cls.nac_device),
            msisdn=os.getenv("DEMO_MSISDN", cls.msisdn),
            traveller_name=os.getenv("DEMO_TRAVELLER_NAME", cls.traveller_name),
            contact_name=os.getenv("DEMO_CONTACT_NAME", cls.contact_name),
            contact_msisdn=os.getenv("DEMO_CONTACT_MSISDN", cls.contact_msisdn),
            time_scale=float(os.getenv("DEMO_TIME_SCALE", "60")),
        )

    def secrets(self) -> list[str]:
        """Values that must never reach the terminal while recording."""
        return [s for s in (self.nac_key, os.getenv("GOOGLE_API_KEY", "")) if s]


# --------------------------------------------------------------------------
# Zone registry (demo slice)
# --------------------------------------------------------------------------
# Placeholder coordinates on Jordan's Highway 15 / Desert Highway toward Al
# Mudawwara. REPLACE with the surveyed values from the zone registry before the
# 6 September dry run — these are the real coordinates your backend uses to
# create the real CAMARA Geofencing Subscription. They do not move a
# simulator device (Nokia's sandbox has no such endpoint); the demo trigger
# comes from /demo/simulate-gate-event instead — see the module docstring.

@dataclass
class Zone:
    zone_id: str
    label: str
    entry_gate: tuple[float, float]
    exit_gate: tuple[float, float]
    gate_radius_m: int
    corridor_km: int
    nominal_crossing_min: int


ZONES: dict[str, Zone] = {
    "JO-H15-MUDAWWARA": Zone(
        zone_id="JO-H15-MUDAWWARA",
        label="Highway 15 — Desert Highway, Al Mudawwara approach",
        entry_gate=(29.8320, 35.9910),
        exit_gate=(29.3350, 36.0240),
        gate_radius_m=8000,
        corridor_km=104,
        nominal_crossing_min=75,
    )
}


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

class Console:
    """Terminal output. This pane is on camera — keep it legible and quiet."""

    def __init__(self, color: bool = True, redact: Iterable[str] = ()) -> None:
        self.tty = sys.stdout.isatty()
        self.color = color and self.tty
        self._redact = [r for r in redact if r]
        self._wait_text = ""
        self._last_tick = 0.0
        self.t0 = time.monotonic()

    # -- styling -----------------------------------------------------------
    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def dim(self, t: str) -> str:    return self._c("2", t)
    def bold(self, t: str) -> str:   return self._c("1", t)
    def green(self, t: str) -> str:  return self._c("32", t)
    def amber(self, t: str) -> str:  return self._c("33", t)
    def red(self, t: str) -> str:    return self._c("31", t)
    def cyan(self, t: str) -> str:   return self._c("36", t)

    def scrub(self, text: str) -> str:
        for secret in self._redact:
            if secret and secret in text:
                text = text.replace(secret, "***redacted***")
        # catch bearer tokens that slipped through from a response body
        return re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{12,}", r"\1***redacted***", text)

    def clock(self) -> str:
        elapsed = time.monotonic() - self.t0
        return f"{int(elapsed // 60):02d}:{elapsed % 60:04.1f}"

    # -- primitives --------------------------------------------------------
    def banner(self, title: str, lines: list[str]) -> None:
        width = 74
        print()
        print(self.green("═" * width))
        print("  " + self.bold(title))
        for line in lines:
            print("  " + self.dim(line))
        print(self.green("═" * width))

    def beat(self, name: str, narration: str) -> None:
        print()
        print(f"{self.dim('[' + self.clock() + ']')}  {self.bold('▸ ' + name.upper())}")
        if narration:
            print(f"           {self.dim(narration)}")

    def line(self, label: str, value: str, status: str | None = None) -> None:
        mark = {"ok": self.green("ok"), "warn": self.amber("warn"),
                "fail": self.red("fail")}.get(status or "", "  ")
        print(f"           {label:<26} {mark}  {self.scrub(value)}")

    def note(self, text: str) -> None:
        print(f"           {self.scrub(text)}")

    def block(self, title: str, body: str) -> None:
        print()
        print(f"           {self.cyan('┌─ ' + title)}")
        for row in self.scrub(body).splitlines():
            print(f"           {self.cyan('│')} {row}")
        print(f"           {self.cyan('└─')}")

    def waiting(self, text: str) -> None:
        self._wait_text = text
        self._last_tick = 0.0
        sys.stdout.write(f"           {self.dim(text)} ")
        sys.stdout.flush()

    def tick(self, elapsed: float) -> None:
        """In-place on a terminal; one dot every 2s when piped to a file."""
        if self.tty:
            sys.stdout.write(
                f"\r           {self.dim(self._wait_text)} {self.dim(f'{elapsed:.0f}s')}  ")
            sys.stdout.flush()
        elif elapsed - self._last_tick >= 2.0:
            self._last_tick = elapsed
            sys.stdout.write(".")
            sys.stdout.flush()

    def done(self, text: str) -> None:
        if self.tty:
            sys.stdout.write(f"\r           {self.dim(self._wait_text)} ")
        print(f" {self.green(text)}")

    def failed(self, text: str) -> None:
        if self.tty:
            sys.stdout.write(f"\r           {self.dim(self._wait_text)} ")
        print(f" {self.red(text)}")


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class DemoFailure(RuntimeError):
    """Raised the moment a take goes wrong, so you stop recording immediately."""


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------

class BackendAdapter:
    def __init__(self, cfg: Config, con: Console) -> None:
        self.cfg = cfg
        self.con = con
        self.http = Http(cfg.backend, timeout=cfg.http_timeout)

    def _call(self, method: str, path: str, **kw) -> Any:
        try:
            r = self.http.request(method, path, **kw)
        except HttpError as exc:
            raise DemoFailure(f"backend unreachable at {self.cfg.backend}{path}: {exc}")
        if r.status_code >= 400:
            raise DemoFailure(f"{method} {path} -> {r.status_code} {self.con.scrub(r.text[:300])}")
        return r.json() if r.content else {}

    # -- control -----------------------------------------------------------
    def health(self) -> dict:                     return self._call("GET", "/healthz")
    def reset(self) -> dict:                      return self._call("POST", "/demo/reset")
    def set_model_enabled(self, on: bool) -> dict:
        return self._call("POST", "/demo/agent/model", body={"enabled": on})

    def enroll(self) -> str:
        payload = {
            "msisdn": self.cfg.msisdn,
            "name": self.cfg.traveller_name,
            "contacts": [{"name": self.cfg.contact_name, "msisdn": self.cfg.contact_msisdn}],
        }
        return self._call("POST", "/demo/travellers", body=payload)["traveller_id"]

    def arm_zone(self, zone_id: str, traveller_id: str) -> dict:
        return self._call("POST", f"/demo/zones/{zone_id}/arm",
                          body={"traveller_id": traveller_id})

    def simulate_gate_event(self, zone_id: str, traveller_id: str, gate: str) -> dict:
        """Injects the one thing Nokia's sandbox cannot produce: a gate
        crossing. Everything the backend does in response — Location
        Retrieval, Congestion Insights, QoD — is a real call against
        cfg.nac_device."""
        return self._call("POST", "/demo/simulate-gate-event",
                          body={"traveller_id": traveller_id,
                                "zone_id": zone_id, "gate": gate})

    def simulate_reachability(self, traveller_id: str, reachable: bool) -> dict:
        return self._call("POST", "/demo/simulate-reachability",
                          body={"traveller_id": traveller_id, "reachable": reachable})

    def set_battery(self, traveller_id: str, level: int) -> None:
        self._call("POST", "/demo/battery",
                   body={"traveller_id": traveller_id, "level": level})

    def advance_clock(self, seconds: int) -> dict:
        return self._call("POST", "/demo/clock/advance", body={"seconds": seconds})

    def contact_reply(self, trip_id: str, reply: str = "safe") -> dict:
        return self._call("POST", "/demo/contact-reply",
                          body={"trip_id": trip_id, "reply": reply})

    # -- reads -------------------------------------------------------------
    def active_trip(self, traveller_id: str) -> dict | None:
        return self._call("GET", "/demo/trips/active",
                          params={"traveller_id": traveller_id}) or None

    def trip(self, trip_id: str) -> dict:
        return self._call("GET", f"/trips/{trip_id}")

    def api_log(self, since: str | None = None) -> list[dict]:
        params = {"since": since} if since else {}
        return self._call("GET", "/demo/api-log", params=params)


class NacSandboxClient:
    """A real client for Nokia's Network as Code sandbox — used only to prove
    connectivity before recording. It does not, and cannot, move a device:
    Nokia's published API catalog has no location-injection endpoint. See the
    module docstring for what this means for how the demo is structured."""

    def __init__(self, cfg: Config, con: Console) -> None:
        self.cfg = cfg
        self.con = con
        headers = {}
        if cfg.nac_key:
            headers["x-rapidapi-key"] = cfg.nac_key
            # RapidAPI's Kong gateway routes by this listing name, which is
            # NOT cfg.nac_base's host — confirmed against a working portal
            # snippet during Prototype Phase integration. Omitting it (or
            # using the URL host instead) returns a misleading 404 "API
            # doesn't exists" even with a valid key.
            headers["x-rapidapi-host"] = "network-as-code.nokia.rapidapi.com"
        self.http = Http(cfg.nac_base, headers=headers, timeout=cfg.http_timeout)

    def preflight(self) -> dict:
        """One real, read-only, side-effect-free call: list geofencing
        subscriptions. Confirms the base URL, the header name, and the key
        all actually work — cheaply, and before anyone is filming."""
        if not self.cfg.nac_key:
            raise DemoFailure(
                "NAC_API_KEY is not set — the sandbox check has nothing to "
                "authenticate with. Set it in .env before recording."
            )
        try:
            r = self.http.request(
                "GET", "/geofencing-subscriptions/v0.3/subscriptions")
        except HttpError as exc:
            raise DemoFailure(f"Nokia sandbox unreachable at {self.cfg.nac_base}: {exc}")
        if r.status_code == 401 or r.status_code == 403:
            raise DemoFailure(
                f"Nokia sandbox rejected the key -> {r.status_code}. "
                "Check NAC_API_KEY and the x-rapidapi-key header."
            )
        if r.status_code >= 400:
            raise DemoFailure(
                f"Nokia sandbox preflight failed -> {r.status_code} "
                f"{self.con.scrub(r.text[:200])}"
            )
        try:
            count = len(r.json())
        except Exception:
            count = "?"
        return {"ok": True, "existing_subscriptions": count}


class FakeBackend(BackendAdapter):
    """--dry-run. Lets the team rehearse narration, beat order and timing with no
    sandbox, no keys and no cost. It mirrors the real trip state machine closely
    enough that a scenario which passes here is correctly *written*; it says
    nothing about whether the real system works. Never use a dry run as footage.
    """

    def __init__(self, cfg: Config, con: Console) -> None:
        self.cfg, self.con = cfg, con
        self._state = "IDLE"
        self._model = True
        self._notified: list[str] = []
        self._elapsed_min = 0
        self._window_min = 92
        self._battery = 82
        self._reachable = True

    # -- internal transitions ---------------------------------------------
    def _advance(self) -> None:
        """Applied on every read, the way a real backend's timers would be."""
        s = self._state
        if s == "BUFFER":
            self._state = "ACTIVE"
        elif s == "ACTIVE":
            if self._reachable and self._elapsed_min > 0:
                self._state = "EXITED"
            elif self._elapsed_min > self._window_min:
                self._state = "OVERDUE"
        elif s == "OVERDUE":
            self._state = "TIER1_ALERTED"
            self._notified.append("tier1")
        elif s == "TIER1_ALERTED" and self._elapsed_min > self._window_min + 15:
            self._state = "TIER2_ESCALATED"
            self._notified.append("tier2")

    def set_reachable(self, reachable: bool) -> None:
        self._reachable = reachable

    def simulate_gate_event(self, zone_id, traveller_id, gate: str):
        if gate == "entry" and self._state == "IDLE":
            self._state = "BUFFER"
        elif gate == "exit":
            self._reachable = True
        return {}

    def simulate_reachability(self, traveller_id, reachable: bool):
        self._reachable = reachable
        return {}

    # -- control ----------------------------------------------------------
    def health(self):
        return {"ok": True,
                "nac": {"apis": ["geofencing", "location", "congestion",
                                 "qod", "reachability"]},
                "agent": {"model": "gemini-2.5-flash", "enabled": self._model}}

    def reset(self):
        self.__init__(self.cfg, self.con)  # noqa: PLC2801 — deliberate full reset
        return {}

    def set_model_enabled(self, on: bool):
        self._model = on
        # the deterministic fallback is more conservative: a wider window
        self._window_min = 92 if on else 105
        return {"enabled": on}

    def enroll(self):
        return "trav_demo"

    def arm_zone(self, zone_id, traveller_id):
        return {"entry_subscription_id": "sub_entry_demo",
                "exit_subscription_id": "sub_exit_demo"}

    def set_battery(self, traveller_id, level):
        self._battery = level

    def advance_clock(self, seconds):
        self._elapsed_min += int(seconds // 60)
        return {"now": datetime.now(timezone.utc).isoformat()}

    def contact_reply(self, trip_id, reply="safe"):
        self._state = "RESOLVED"
        return {}

    # -- reads ------------------------------------------------------------
    def active_trip(self, traveller_id):
        if self._state == "IDLE":
            self._state = "BUFFER"          # the gate crossing opened a trip
        else:
            self._advance()
        return {"trip_id": "trip_demo", "state": self._state}

    def trip(self, trip_id):
        self._advance()
        fallback = not self._model
        return {
            "trip_id": trip_id,
            "state": self._state,
            "risk": "ELEVATED" if self._battery < 25 else "LOW",
            "battery_at_entry": self._battery,
            "battery_band": "low" if self._battery < 25 else "healthy",
            "congestion_tier": "light",
            "entry_point": "29.8320, 35.9910",
            "last_known_location": "29.8318, 35.9907",
            "predicted_crossing_min": 75,
            "monitoring_window_min": self._window_min,
            "decision_record": (
                f"signals   congestion=light  battery={self._battery}%  "
                f"hour=14  zone=104km\n"
                "model     " + ("gemini-2.5-flash"
                                if not fallback
                                else "UNAVAILABLE -> deterministic risk model\n"
                                     "          floors/ceilings applied, no reasoning text") + "\n"
                + ("reasoning light traffic, but 18% battery shortens the window in which\n"
                   "          a handset can be found; escalate earlier than nominal\n"
                   if not fallback and self._battery < 25 else
                   "reasoning light traffic and a healthy battery put this in the routine band\n"
                   if not fallback else "")
                + f"window    {self._window_min} min (nominal 75 + buffer)\n"
                "tools     get_congestion_insights, get_location, request_qod_session\n"
                "action    QoD session requested; monitoring window armed"
            ),
            "notifications": list(self._notified),
        }

    def api_log(self, since=None):
        return [
            {"ts": "—", "api": "Geofencing Subscriptions",
             "endpoint": "webhook /hooks/geofence", "latency_ms": 0,
             "status": 200, "cost_usd": 0.0},
            {"ts": "—", "api": "Congestion Insights",
             "endpoint": "GET /congestion-insights/v0", "latency_ms": 210,
             "status": 200, "cost_usd": 0.01},
            {"ts": "—", "api": "Location Retrieval",
             "endpoint": "POST /location-retrieval/v0/retrieve", "latency_ms": 190,
             "status": 200, "cost_usd": 0.01},
            {"ts": "—", "api": "Quality on Demand",
             "endpoint": "POST /qod/v0/sessions", "latency_ms": 340,
             "status": 201, "cost_usd": 0.0},
        ]


class FakeNacSandboxClient(NacSandboxClient):
    """--dry-run. No real network call — confirms the preflight *shape*, not
    real credentials."""

    def __init__(self, cfg: Config, con: Console) -> None:
        self.cfg, self.con = cfg, con

    def preflight(self) -> dict:
        return {"ok": True, "existing_subscriptions": 0}


# --------------------------------------------------------------------------
# The conductor
# --------------------------------------------------------------------------

@dataclass
class BeatRecord:
    name: str
    narration: str
    start_s: float
    end_s: float = 0.0
    ok: bool = True


@dataclass
class RunLog:
    scenario: str
    zone: str
    started_at: str
    beats: list[BeatRecord] = field(default_factory=list)
    ok: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "scenario": self.scenario,
                "zone": self.zone,
                "started_at": self.started_at,
                "ok": self.ok,
                "beats": [
                    {"name": b.name, "narration": b.narration,
                     "start": f"{int(b.start_s // 60):d}:{b.start_s % 60:04.1f}",
                     "duration_s": round(b.end_s - b.start_s, 1), "ok": b.ok}
                    for b in self.beats
                ],
            },
            indent=2,
        )


class Conductor:
    def __init__(self, cfg: Config, con: Console, backend: BackendAdapter,
                 nac: NacSandboxClient, zone: Zone, *, step: bool,
                 hold: float, clock_mode: str, poll_interval: float = 1.0) -> None:
        self.cfg, self.con = cfg, con
        self.backend, self.nac, self.zone = backend, nac, zone
        self.step, self.hold, self.clock_mode = step, hold, clock_mode
        self.poll_interval = poll_interval
        self.traveller_id: str = ""
        self.trip_id: str = ""
        self.log: RunLog | None = None

    # -- beats -------------------------------------------------------------
    @contextmanager
    def beat(self, name: str, narration: str = ""):
        rec = BeatRecord(name, narration, time.monotonic() - self.con.t0)
        self.con.beat(name, narration)
        try:
            yield
        except Exception:
            rec.ok = False
            rec.end_s = time.monotonic() - self.con.t0
            if self.log:
                self.log.beats.append(rec)
            raise
        rec.end_s = time.monotonic() - self.con.t0
        if self.log:
            self.log.beats.append(rec)
        if self.hold:
            time.sleep(self.hold)
        if self.step:
            try:
                input(self.con.dim("           ⏎ next beat "))
            except (EOFError, KeyboardInterrupt):
                raise DemoFailure("stopped by operator")

    # -- helpers -----------------------------------------------------------
    def wait_for_state(self, expected: set[str], timeout: float = 90.0,
                       label: str = "waiting for state") -> dict:
        self.con.waiting(f"{label} {'/'.join(sorted(expected))}")
        start = time.monotonic()
        deadline = start + timeout
        last = "—"
        while time.monotonic() < deadline:
            trip = (self.backend.active_trip(self.traveller_id)
                    if not self.trip_id else self.backend.trip(self.trip_id))
            if trip:
                self.trip_id = trip.get("trip_id", self.trip_id)
                last = trip.get("state", "—")
                if last in expected:
                    self.con.done(last)
                    return self.backend.trip(self.trip_id)
            self.con.tick(time.monotonic() - start)
            time.sleep(self.poll_interval)
        self.con.failed(f"timeout — stuck in {last}")
        raise DemoFailure(
            f"expected {'/'.join(sorted(expected))} within {timeout:.0f}s, "
            f"trip is in {last}. Stop the recording."
        )

    def show_api_calls(self, title: str = "CAMARA calls in this beat") -> None:
        calls = self.backend.api_log()
        if not calls:
            return
        rows, total = [], 0.0
        for c in calls[-6:]:
            total += float(c.get("cost_usd") or 0)
            rows.append(
                f"{c.get('api',''):<28} {str(c.get('endpoint',''))[:34]:<34} "
                f"{str(c.get('latency_ms','—')) + 'ms':>7}  {c.get('status','')}"
            )
        rows.append(f"{'':<28} {'':<34} {'':>7}  running cost ${total:.03f}")
        self.con.block(title, "\n".join(rows))

    def elapse(self, minutes: int, why: str) -> None:
        """Move time forward, virtually or by sleeping at DEMO_TIME_SCALE."""
        if self.clock_mode == "virtual":
            self.backend.advance_clock(minutes * 60)
            self.con.note(f"clock advanced {minutes} min — {why}")
        else:
            wait = (minutes * 60) / max(self.cfg.time_scale, 1.0)
            self.con.waiting(f"{why} — {minutes} min at ×{self.cfg.time_scale:g} "
                             f"({wait:.0f}s real)")
            start = time.monotonic()
            end = start + wait
            while time.monotonic() < end:
                self.con.tick(time.monotonic() - start)
                time.sleep(min(2.0, max(0.2, wait / 20)))
            self.con.done("elapsed")

    # -- shared opening ----------------------------------------------------
    def preflight(self) -> None:
        with self.beat("preflight", "everything checked before a single frame is recorded"):
            h = self.backend.health()
            self.con.line("backend", self.cfg.backend, "ok" if h.get("ok") else "fail")
            apis = ", ".join(h.get("nac", {}).get("apis", [])) or "none reported"
            self.con.line("nac sandbox (backend)", apis,
                          "ok" if apis != "none reported" else "warn")
            agent = h.get("agent", {})
            self.con.line("agent model", str(agent.get("model", "—")),
                          "ok" if agent.get("enabled") else "warn")

            nac_check = self.nac.preflight()
            self.con.line("nac sandbox (direct)",
                          f"{self.cfg.nac_base} · auth ok · "
                          f"{nac_check.get('existing_subscriptions', '?')} subs live", "ok")
            self.con.line("nac fixed device", self.cfg.nac_device, "ok")
            self.con.line("zone", f"{self.zone.zone_id} · {self.zone.corridor_km} km", "ok")
            self.con.line("time scale", f"×{self.cfg.time_scale:g} ({self.clock_mode} clock)", "ok")
            if not h.get("ok"):
                raise DemoFailure("backend reported unhealthy — fix before recording")

    def arm(self, *, model_enabled: bool = True, battery: int = 82) -> None:
        with self.beat("arm", "clean state, traveller enrolled, gates armed on the network"):
            self.backend.reset()
            self.backend.set_model_enabled(model_enabled)
            self.con.line("agent model", "enabled" if model_enabled
                          else "DISABLED for this run", "ok" if model_enabled else "warn")
            self.traveller_id = self.backend.enroll()
            self.con.line("traveller", f"{self.cfg.traveller_name} → {self.traveller_id}", "ok")
            self.con.line("contact", f"{self.cfg.contact_name} {self.cfg.contact_msisdn}", "ok")
            subs = self.backend.arm_zone(self.zone.zone_id, self.traveller_id)
            self.con.line("entry gate", f"{subs['entry_subscription_id']} · "
                                        f"r={self.zone.gate_radius_m}m", "ok")
            self.con.line("exit gate", f"{subs['exit_subscription_id']} · "
                                       f"r={self.zone.gate_radius_m}m", "ok")
            self.backend.set_battery(self.traveller_id, battery)
            self.con.line("battery", f"{battery}%", "ok")

    def approach(self) -> None:
        with self.beat("approach", "the phone is doing nothing at all — detection is network-side"):
            self.con.note("no trip, no record, no API call while the traveller "
                          "is nowhere near a zone")
            self.con.note("(nothing to show on screen here — that silence is the point)")

    def cross_entry_gate(self) -> dict:
        with self.beat("entry gate crossed",
                       "the network fires; the backend learns about it without asking the phone"):
            self.con.line("trigger", "synthetic — simulate-gate-event(entry)", "warn")
            self.con.note("Nokia's sandbox has no location-injection endpoint; this call "
                          "stands in for the geofence webhook a real crossing would send")
            t_trigger = time.monotonic()
            self.backend.simulate_gate_event(self.zone.zone_id, self.traveller_id, "entry")
            trip = self.wait_for_state({"BUFFER", "ACTIVE"}, timeout=120,
                                       label="backend reacts →")
            latency = time.monotonic() - t_trigger
            self.con.line("reaction latency", f"{latency:.1f}s trigger → BUFFER/ACTIVE", "ok")
            self.con.note("everything from here — Location Retrieval, Congestion Insights, "
                          "QoD — is a real call against " + self.cfg.nac_device)
            return trip

    def agent_run(self) -> dict:
        with self.beat("agent run",
                       "the agent decides what the signals mean, then acts"):
            trip = self.wait_for_state({"ACTIVE"}, timeout=120, label="agent →")
            record = trip.get("decision_record") or "(no decision record returned)"
            self.con.block("agent decision record", record)
            self.con.line("risk", str(trip.get("risk", "—")), "ok")
            self.con.line("predicted crossing",
                          f"{trip.get('predicted_crossing_min','—')} min", "ok")
            self.con.line("monitoring window",
                          f"{trip.get('monitoring_window_min','—')} min", "ok")
            self.show_api_calls()
            return trip

    def go_dark(self) -> None:
        with self.beat("inside the zone", "no connectivity — and no calls attempted"):
            self.backend.simulate_reachability(self.traveller_id, False)
            self.con.line("reachability", "device is dark (synthetic — see docstring)", "ok")
            self.con.note("the app still shows the offline map and the direction back to signal")

    # -- assertions --------------------------------------------------------
    def assert_not_notified(self, trip: dict) -> None:
        notes = trip.get("notifications") or []
        if notes:
            raise DemoFailure(f"expected a silent close, but these fired: {notes}")
        self.con.line("contact notified", "no — correct", "ok")

    def assert_notified(self, trip: dict, tier: str) -> None:
        notes = [str(n) for n in (trip.get("notifications") or [])]
        if not any(tier in n for n in notes):
            raise DemoFailure(f"expected {tier} in notifications, got {notes}")
        self.con.line(f"{tier} fired", "yes", "ok")


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

def scenario_happy(c: Conductor) -> None:
    c.preflight()
    c.arm()
    c.approach()
    c.cross_entry_gate()
    c.agent_run()
    c.go_dark()
    c.elapse(int(c.zone.nominal_crossing_min * 0.9), "the crossing itself")

    with c.beat("exit gate", "the traveller comes out the other side"):
        c.con.line("trigger", "synthetic — simulate-gate-event(exit)", "warn")
        c.backend.simulate_gate_event(c.zone.zone_id, c.traveller_id, "exit")
        c.backend.simulate_reachability(c.traveller_id, True)
        trip = c.wait_for_state({"EXITED"}, timeout=120, label="reachability push →")
        c.con.line("trip", "closed silently", "ok")
        c.assert_not_notified(trip)
        c.con.note("the traveller did nothing the entire trip. That is the product.")


def scenario_overdue(c: Conductor) -> None:
    c.preflight()
    c.arm(battery=18)
    c.approach()
    c.cross_entry_gate()
    trip = c.agent_run()
    c.go_dark()

    window = int(trip.get("monitoring_window_min") or 92)
    c.elapse(window + 5, "past the predicted reconnection time, with no signal")

    with c.beat("tier 1", "the personal contact is texted first, with context a human can act on"):
        trip = c.wait_for_state({"OVERDUE", "TIER1_ALERTED"}, timeout=120,
                                label="escalation →")
        trip = c.wait_for_state({"TIER1_ALERTED"}, timeout=120, label="sms →")
        c.assert_notified(trip, "tier1")
        c.con.note("watch the second handset — this is a real SMS")

    c.elapse(20, "the grace window, with no reply from the contact")

    with c.beat("tier 2", "the emergency centre gets a search, not a missing-person report"):
        trip = c.wait_for_state({"TIER2_ESCALATED"}, timeout=120, label="dashboard →")
        c.assert_notified(trip, "tier2")
        c.con.line("entry point", str(trip.get("entry_point", "—")), "ok")
        c.con.line("last known", str(trip.get("last_known_location", "—")), "ok")
        c.con.line("battery band", str(trip.get("battery_band", "low")), "ok")
        c.con.line("congestion at entry", str(trip.get("congestion_tier", "—")), "ok")


def scenario_stand_down(c: Conductor) -> None:
    c.preflight()
    c.arm()
    c.approach()
    c.cross_entry_gate()
    trip = c.agent_run()
    c.go_dark()

    window = int(trip.get("monitoring_window_min") or 92)
    c.elapse(window + 5, "past the predicted reconnection time")

    with c.beat("tier 1", "contact texted"):
        trip = c.wait_for_state({"TIER1_ALERTED"}, timeout=120, label="sms →")
        c.assert_notified(trip, "tier1")

    with c.beat("stand down", "a human closes the loop before any emergency centre is paged"):
        c.backend.contact_reply(c.trip_id, "safe")
        trip = c.wait_for_state({"RESOLVED"}, timeout=60, label="resolution →")
        notes = [str(n) for n in (trip.get("notifications") or [])]
        if any("tier2" in n for n in notes):
            raise DemoFailure("tier 2 fired despite a stand-down — this is a real bug, not a bad take")
        c.con.line("tier 2", "never fired — correct", "ok")
        c.con.note("false alarms are resolved by one SMS reply. That is why tier 1 exists.")


def scenario_model_down(c: Conductor) -> None:
    c.preflight()
    c.arm(model_enabled=False, battery=18)

    with c.beat("model disabled", "the language model is switched off before the crossing starts"):
        h = c.backend.health()
        enabled = h.get("agent", {}).get("enabled", True)
        if enabled:
            raise DemoFailure("agent model still reports enabled — the kill switch did not take")
        c.con.line("gemini", "unavailable", "warn")
        c.con.note("everything from here runs on the deterministic risk model underneath")

    c.approach()
    c.cross_entry_gate()

    with c.beat("fallback risk model", "the window still gets set — by rules, not by reasoning"):
        trip = c.wait_for_state({"ACTIVE"}, timeout=120, label="fallback →")
        c.con.block("decision record", trip.get("decision_record") or "(none)")
        c.con.line("monitoring window",
                   f"{trip.get('monitoring_window_min','—')} min (deterministic)", "ok")

    c.go_dark()
    window = int(trip.get("monitoring_window_min") or 92)
    c.elapse(window + 5, "past the window, model still down")

    with c.beat("the alarm fires anyway",
                "escalation is timer-driven, not model-driven"):
        trip = c.wait_for_state({"TIER1_ALERTED"}, timeout=120, label="escalation →")
        c.assert_notified(trip, "tier1")
        c.con.note("a model failure degrades the quality of the judgement, never the "
                   "existence of the alarm")


SCENARIOS: dict[str, Callable[[Conductor], None]] = {
    "happy": scenario_happy,
    "overdue": scenario_overdue,
    "stand-down": scenario_stand_down,
    "model-down": scenario_model_down,
}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def run_one(name: str, cfg: Config, args) -> RunLog:
    con = Console(color=not args.no_color, redact=cfg.secrets())
    zone = ZONES[args.zone]

    if args.dry_run:
        backend: BackendAdapter = FakeBackend(cfg, con)
        nac: NacSandboxClient = FakeNacSandboxClient(cfg, con)
    else:
        backend = BackendAdapter(cfg, con)
        nac = NacSandboxClient(cfg, con)

    con.banner(
        f"SIGNALGUARD DEMO CONDUCTOR — {name}",
        [
            f"zone        {zone.zone_id}  ·  {zone.label}",
            f"nac device  {cfg.nac_device}  (Nokia fixed simulator — real calls, not moved)",
            f"time        ×{cfg.time_scale:g}  ·  {args.clock} clock"
            + ("  ·  DRY RUN — not footage" if args.dry_run else ""),
        ],
    )

    c = Conductor(cfg, con, backend, nac, zone, step=args.step,
                  hold=args.hold, clock_mode=args.clock,
                  poll_interval=0.2 if args.dry_run else 1.0)
    c.log = RunLog(scenario=name, zone=zone.zone_id,
                   started_at=datetime.now(timezone.utc).isoformat())

    try:
        SCENARIOS[name](c)
    except DemoFailure as exc:
        c.log.ok = False
        print()
        print(con.red("═" * 74))
        print("  " + con.bold(con.red("RUN FAILED — STOP RECORDING")))
        print("  " + con.scrub(str(exc)))
        print(con.red("═" * 74))
        return c.log
    except KeyboardInterrupt:
        c.log.ok = False
        print("\n  " + con.amber("interrupted"))
        return c.log

    print()
    print(con.green("═" * 74))
    print("  " + con.bold(con.green(f"{name} — complete")) +
          con.dim(f"   {con.clock()} elapsed"))
    print(con.green("═" * 74))
    return c.log


def main() -> int:
    load_env_file(os.getenv("SIGNALGUARD_ENV_FILE", ".env"))
    cfg = Config.from_env()

    p = argparse.ArgumentParser(
        description="SignalGuard demo conductor — drives one crossing end to end.")
    p.add_argument("--scenario", default="overdue",
                   choices=[*SCENARIOS.keys(), "all"],
                   help="which crossing to run (default: overdue)")
    p.add_argument("--zone", default="JO-H15-MUDAWWARA", choices=list(ZONES.keys()))
    p.add_argument("--clock", default="virtual", choices=["virtual", "real"],
                   help="virtual jumps the backend clock; real sleeps at DEMO_TIME_SCALE")
    p.add_argument("--step", action="store_true",
                   help="pause between beats — use this for the live Phase 2 demo")
    p.add_argument("--hold", type=float, default=1.5,
                   help="seconds to hold after each beat so the edit has room")
    p.add_argument("--dry-run", action="store_true",
                   help="fake backend and simulator; rehearsal only, never footage")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--log", metavar="PATH",
                   help="write a run log with beat timestamps — feeds the timestamped "
                        "demo description in the submission")
    args = p.parse_args()

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    logs = [run_one(n, cfg, args) for n in names]

    if args.log:
        Path(args.log).write_text(
            "[\n" + ",\n".join(l.to_json() for l in logs) + "\n]\n", encoding="utf-8")
        print(f"\n  run log → {args.log}")

    return 0 if all(l.ok for l in logs) else 1


if __name__ == "__main__":
    sys.exit(main())
