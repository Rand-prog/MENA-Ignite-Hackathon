#!/usr/bin/env python3
"""Fill the operator console with a realistic queue, in one command.

Why this exists. A reviewer who starts the backend and opens the dashboard
sees an empty queue and a correct-but-unhelpful "No trips in the corridor
right now" — the console's whole job is triage, and there is nothing to
triage until somebody crosses a gate. Stepping four travellers through
`dashboard/demo.html` by hand takes a couple of minutes and a reading of
the state machine first.

What it is not. It is **not** a scenario runner. `scripts/run_demo.py` owns
the scenarios — the beat sequence, the narration, the pass/fail assertions —
and its contract is frozen; a second implementation of `scenario_overdue`
would be a second source of truth for the one thing that must not break.
This only presses the same demo primitives a human would press in
`demo.html`, in an order that leaves one crossing in each of the three
states a dispatcher sorts by, and asserts nothing.

Every call it makes is real: four registrations, four armed zones, four
gate crossings against the live Nokia Network as Code sandbox. The CAMARA
calls that result are the ones the console's "Network API activity" panel
then shows — that panel is not fed by this script, it is fed by the calls
this script causes.

    python scripts/seed_console.py                     # http://127.0.0.1:8000
    python scripts/seed_console.py --backend http://host:8000

Needs `SIGNALGUARD_DEMO_MODE=true` on the backend (the default), since the
/demo/* primitives are not mounted otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

ZONE = "JO-H15-MUDAWWARA"

# Four crossings that leave the queue with one of each severity plus one
# closed trip in history. Battery is the interesting variable: the risk
# model keys off it, and 14% is what makes the agent judge a QoD session
# worth spending — which is the fifth CAMARA API.
TRAVELLERS = [
    {"name": "Faisal Al-Harbi", "msisdn": "+962790000001",
     "contact": ("Omar (brother)", "+962790000002"), "battery": 18},
    {"name": "Layla Haddad", "msisdn": "+962790000011",
     "contact": ("Rami (husband)", "+962790000012"), "battery": 86},
    {"name": "Khalid Mansour", "msisdn": "+962790000021",
     "contact": ("Dispatch (ops)", "+962790000022"), "battery": 64},
    {"name": "Yousef Zaid", "msisdn": "+962790000031",
     "contact": ("Hana (sister)", "+962790000032"), "battery": 14},
]


class Backend:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:300]
            raise SystemExit(f"{method} {path} -> {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"cannot reach {self.base} ({exc.reason}). Is the backend running?"
            ) from exc
        return json.loads(raw) if raw.strip() else {}


def enter(be: Backend, spec: dict) -> str:
    reg = be.call("POST", "/demo/travellers", {
        "msisdn": spec["msisdn"], "name": spec["name"],
        "contacts": [{"name": spec["contact"][0], "msisdn": spec["contact"][1]}],
    })
    tid = reg["traveller_id"]
    be.call("POST", f"/demo/zones/{ZONE}/arm", {"traveller_id": tid})
    be.call("POST", "/demo/battery", {"traveller_id": tid, "level": spec["battery"]})
    be.call("POST", "/demo/simulate-gate-event", {
        "traveller_id": tid, "zone_id": ZONE, "gate": "entry",
    })
    print(f"  entered  {spec['name']:<18} battery {spec['battery']}%")
    return tid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument(
        "--keep", action="store_true",
        help="add to the existing queue instead of resetting it first",
    )
    args = ap.parse_args()
    be = Backend(args.backend)

    health = be.call("GET", "/healthz")
    if not health.get("demo"):
        raise SystemExit(
            "backend is not in demo mode — the /demo/* primitives this needs "
            "are not mounted. Start it with SIGNALGUARD_DEMO_MODE=true."
        )

    if not args.keep:
        print("reset")
        be.call("POST", "/demo/reset")

    # 1 — a crossing that goes all the way to Tier 2. Dark handset first:
    #     pinging a phone with no signal buys nothing, so Tier 0 is skipped
    #     and the ladder runs ACTIVE -> OVERDUE -> TIER1 -> TIER2.
    print("crossing 1 — escalates to Tier 2")
    t1 = enter(be, TRAVELLERS[0])
    be.call("POST", "/demo/simulate-reachability", {"traveller_id": t1, "reachable": False})
    be.call("POST", "/demo/clock/advance", {"seconds": 7200})   # past the window
    be.call("POST", "/demo/clock/advance", {"seconds": 1500})   # past tier1 grace

    # 2 — entered before the clock jumped, so its window is most of the way
    #     through: the amber "window expiring" row.
    print("crossing 2 — window expiring (amber)")
    enter(be, TRAVELLERS[1])
    be.call("POST", "/demo/clock/advance", {"seconds": 5400})

    # 3 — just crossed the gate: the green "in transit" row.
    print("crossing 3 — in transit (green)")
    enter(be, TRAVELLERS[2])

    # 4 — a low-battery crossing that comes out the far side. Closing it
    #     spends the Quality on Demand session on the reconnection edge
    #     (where the bandwidth is actually worth something) and writes the
    #     crossing into history.
    print("crossing 4 — closes silently, spends QoD at reconnection")
    t4 = enter(be, TRAVELLERS[3])
    be.call("POST", "/demo/simulate-reachability", {"traveller_id": t4, "reachable": True})

    trips = be.call("GET", "/dashboard/trips")
    activity = be.call("GET", "/dashboard/api-activity?limit=1")
    totals = activity["totals"]

    print("\nqueue:")
    for t in trips:
        print(f"  {t['state']:<16} {t['traveller_name']:<18} risk {t['risk']}")
    print(
        f"\nCAMARA: {totals['calls']} calls, {totals['failed']} failed, "
        f"{totals['apis_used']}/{totals['apis_total']} APIs exercised"
    )
    if totals["apis_used"] < totals["apis_total"]:
        print(
            "  (an API showing zero calls means it was not needed on these "
            "four crossings, not that it is unreachable — the console says "
            "which is which)"
        )
    print("\nOpen the console: http://localhost:5500")
    return 0


if __name__ == "__main__":
    sys.exit(main())
