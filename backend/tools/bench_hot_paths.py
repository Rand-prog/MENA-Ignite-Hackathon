"""Benchmark for the endpoints that run on a timer.

The dashboard polls `/dashboard/trips` every 3 seconds, the app polls
`/travellers/me/trip` every 3-45 seconds depending on where the traveller
is, and both of those run `state_machine.tick()` on the way through. Those
are the paths where a few milliseconds are paid over and over, so they are
the ones worth a repeatable number rather than an opinion.

Drives the real ASGI app in-process (httpx ASGITransport) with the five
Nokia calls stubbed exactly the way tests/conftest.py stubs them, and with
GOOGLE_API_KEY blank so the agent takes its deterministic path. What is
measured is therefore this codebase's own work — SQL, ORM hydration,
serialization — with no sandbox latency in the sample. Uses its own temp
database; it never touches backend/signalguard.db.

    cd backend
    ./.venv/Scripts/python tools/bench_hot_paths.py
    ./.venv/Scripts/python tools/bench_hot_paths.py --trips 60 --iters 80

Compare two revisions by running it under each with the same arguments,
alternating rather than doing all of one and then all of the other — the
run-to-run spread on a laptop is wide enough to swamp a 15% change if the
two sets are minutes apart.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

# Python puts this file's own directory on sys.path, not the one you ran it
# from, so `app` needs putting there explicitly for the usual `cd backend`
# invocation to work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Settings() reads the environment at import time, so this has to happen
# before anything under app/ is imported (same constraint tests/conftest.py
# works around the same way).
_db = Path(tempfile.gettempdir()) / "signalguard_bench.db"
for _suffix in ("", "-wal", "-shm"):
    _f = Path(str(_db) + _suffix)
    if _f.exists():
        _f.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db.as_posix()}"
os.environ["SIGNALGUARD_DEMO_MODE"] = "true"
os.environ["NAC_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""  # deterministic fallback, no model call

import httpx  # noqa: E402

from app import coverage  # noqa: E402
from app.clock import clock  # noqa: E402
from app.db import SessionLocal, reset_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CoverageObservation  # noqa: E402
from app.nac_singleton import nac_client  # noqa: E402
from app.whatsapp_singleton import whatsapp_client  # noqa: E402
from app.zones import ZONE_SEEDS  # noqa: E402

ZONE = ZONE_SEEDS[0]


async def _fake_geofence(**kw):
    return {"id": f"sub_{kw.get('lat')}_{kw.get('lon')}"}


async def _fake_location(**kw):
    return {
        "lastLocationTime": "2026-08-30T12:00:00Z",
        "area": {
            "areaType": "CIRCLE",
            "center": {"latitude": ZONE.entry_lat, "longitude": ZONE.entry_lon},
            "radius": 1000,
        },
    }


async def _fake_congestion(**kw):
    return {"congestionLevel": "light"}


async def _fake_qod(**kw):
    return {"sessionId": "qod_bench", "qosStatus": "REQUESTED"}


async def _fake_reachability(**kw):
    return {"reachable": True, "connectivity": ["DATA"]}


async def _fake_whatsapp(*, to_msisdn: str, body: str):
    return {"sent": True}


def patch_network() -> None:
    """Every real-call seam on the process-wide clients, stubbed."""
    nac_client.create_geofence_subscription = _fake_geofence
    nac_client.retrieve_location = _fake_location
    nac_client.query_congestion = _fake_congestion
    nac_client.create_qod_session = _fake_qod
    nac_client.retrieve_reachability = _fake_reachability
    whatsapp_client.send = _fake_whatsapp


async def _timed(fn, iters: int) -> dict:
    await fn()  # warmup: first-call statement compilation is not the subject
    samples = []
    for _ in range(iters):
        started = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return {
        "median": statistics.median(samples),
        "p90": samples[max(0, round(0.9 * len(samples)) - 1)],
        "min": samples[0],
    }


async def _seed(ac: httpx.AsyncClient, n_trips: int, n_obs: int) -> str:
    await reset_db()
    await ac.post("/demo/reset")
    clock.reset()

    first_token = ""
    for i in range(n_trips):
        resp = await ac.post("/travellers", json={
            "msisdn": f"+9627000{i:04d}",
            "name": f"Bench Traveller {i}",
            "contacts": [{"name": "Contact", "msisdn": "+962790000000"}],
        })
        body = resp.json()
        first_token = first_token or body["auth_token"]
        await ac.post(f"/demo/zones/{ZONE.zone_id}/arm",
                      json={"traveller_id": body["traveller_id"]})
        await ac.post("/demo/simulate-gate-event", json={
            "traveller_id": body["traveller_id"],
            "zone_id": ZONE.zone_id,
            "gate": "entry",
        })

    # Coverage observations, spread over a patch of grid cells around the
    # corridor, so /zones/candidates has a realistic amount to aggregate.
    async with SessionLocal() as session:
        now = clock.now()
        for i in range(n_obs):
            lat = ZONE.entry_lat + (i % 40) * 0.02
            lon = ZONE.entry_lon + (i % 17) * 0.02
            session.add(CoverageObservation(
                lat=lat, lon=lon, cell=coverage.cell_key(lat, lon),
                reachable=(i % 5 == 0), congestion_level="light",
                observed_at=now,
            ))
        await session.commit()
    return first_token


async def main(n_trips: int, iters: int, n_obs: int) -> None:
    patch_network()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bench"
    ) as ac:
        token = await _seed(ac, n_trips, n_obs)
        auth = {"Authorization": f"Bearer {token}"}

        async def get(path: str, headers: dict | None = None):
            resp = await ac.get(path, headers=headers or {})
            assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"

        cases = {
            f"GET /dashboard/trips ({n_trips} live)": lambda: get("/dashboard/trips"),
            "GET /travellers/me/trip": lambda: get("/travellers/me/trip", auth),
            f"GET /zones/candidates ({n_obs} obs)": lambda: get("/zones/candidates"),
            "GET /dashboard/history": lambda: get("/dashboard/history"),
            "GET /dashboard/zones/{id}/stats":
                lambda: get(f"/dashboard/zones/{ZONE.zone_id}/stats"),
        }
        results = {name: await _timed(fn, iters) for name, fn in cases.items()}

    width = max(len(name) for name in results)
    print(f"\n{'endpoint':<{width}}  {'median':>10}  {'p90':>10}  {'min':>10}")
    print("-" * (width + 36))
    for name, r in results.items():
        print(f"{name:<{width}}  {r['median']:>8.2f}ms  {r['p90']:>8.2f}ms  {r['min']:>8.2f}ms")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trips", type=int, default=12, help="live crossings to seed")
    parser.add_argument("--iters", type=int, default=120, help="samples per endpoint")
    parser.add_argument("--obs", type=int, default=4000, help="coverage observations to seed")
    args = parser.parse_args()
    asyncio.run(main(args.trips, args.iters, args.obs))
