"""Demo control surface. Implemented to the letter of run_demo.py's
contract — do not change these paths, methods, or payload shapes, the
conductor is finished and tested against them.

Mounted only when SIGNALGUARD_DEMO_MODE=true, so this cannot exist in a
production build.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import state_machine as sm
from ..clock import clock
from ..config import settings
from ..db import SessionLocal, get_session, reset_db
from ..models import ApiLogEntry, Traveller, Trip, Zone
from ..nac_singleton import nac_client
from ..runtime_state import runtime_state
from ..whatsapp_singleton import whatsapp_client
from ..schemas import (
    AgentModelIn, ArmZoneIn, BatteryIn, ClockAdvanceIn, ContactReplyIn,
    SimulateGateEventIn, SimulateReachabilityIn, TravellerIn,
)
from ..serializers import trip_to_dict
from ..zones import ZONE_SEEDS

router = APIRouter(tags=["demo"])


async def _seed_zones(session: AsyncSession) -> None:
    for z in ZONE_SEEDS:
        session.add(Zone(
            zone_id=z.zone_id, label=z.label,
            entry_lat=z.entry_lat, entry_lon=z.entry_lon,
            exit_lat=z.exit_lat, exit_lon=z.exit_lon,
            gate_radius_m=z.gate_radius_m, corridor_km=z.corridor_km,
            nominal_crossing_min=z.nominal_crossing_min,
        ))
    await session.commit()


@router.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)):
    return {
        "ok": True,
        # Additive: lets the dashboard's demo control panel know these
        # endpoints exist before it renders a single synthetic-trigger
        # button. This router is only mounted when SIGNALGUARD_DEMO_MODE is
        # on, so reaching this line at all already means demo mode — the
        # flag is here so the gate stays explicit if /healthz ever moves.
        "demo": True,
        "nac": {
            "apis": [
                "geofencing-subscriptions", "location-retrieval",
                "congestion-insights", "qod", "device-reachability-status",
            ]
        },
        "agent": {
            "model": settings.gemini_risk_model or settings.gemini_model,
            "enabled": runtime_state.agent_model_enabled,
        },
    }


@router.post("/demo/reset")
async def demo_reset():
    await reset_db()
    async with SessionLocal() as session:
        await _seed_zones(session)
    runtime_state.reset()
    clock.reset()
    return {}


@router.post("/demo/travellers")
async def demo_travellers(body: TravellerIn, session: AsyncSession = Depends(get_session)):
    try:
        traveller = await sm.create_traveller(
            session, msisdn=body.msisdn, name=body.name,
            contacts=[c.model_dump() for c in body.contacts],
        )
    except IntegrityError:
        # msisdn is unique, and registering the same number twice is a
        # normal thing to do here: the app onboards with a number, then a
        # seeding script or the demo panel addresses the same traveller
        # from the other side. `POST /travellers` already re-attaches in
        # demo mode (real.py) rather than bouncing; this path did not, and
        # surfaced the raw constraint violation as a 500 with a SQLAlchemy
        # traceback. Same behaviour, same reason — the whole point of a
        # demo-only surface is that the identity is shared.
        await session.rollback()
        result = await session.execute(
            select(Traveller).where(Traveller.msisdn == body.msisdn)
        )
        traveller = result.scalar_one_or_none()
        if traveller is None:  # pragma: no cover — lost a race with a delete
            raise HTTPException(409, "msisdn is taken but its traveller is gone")
    # auth_token is additive (run_demo.py reads traveller_id only). The
    # dashboard's demo panel needs it to call the traveller-authenticated
    # endpoints — /travellers/me/tier0-response above all, which is the
    # rung that closes a trip with nobody contacted.
    return {"traveller_id": traveller.id, "auth_token": traveller.auth_token}


@router.post("/demo/zones/{zone_id}/arm")
async def demo_arm_zone(zone_id: str, body: ArmZoneIn, session: AsyncSession = Depends(get_session)):
    try:
        entry_id, exit_id, reach_id = await sm.arm_zone(
            session, nac_client, zone_id=zone_id, traveller_id=body.traveller_id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    # reachability_subscription_id is additive — run_demo.py reads the two
    # gate ids only, and the contract's two keys are unchanged. It is
    # surfaced because arming now creates a third real CAMARA subscription
    # (Device Reachability Status, the subscription form — see
    # state_machine.arm_zone), and a subscription the demo can't see is one
    # nobody notices has lapsed. None when the sandbox declined it.
    return {
        "entry_subscription_id": entry_id,
        "exit_subscription_id": exit_id,
        "reachability_subscription_id": reach_id,
    }


@router.post("/demo/simulate-gate-event")
async def demo_simulate_gate_event(body: SimulateGateEventIn, session: AsyncSession = Depends(get_session)):
    try:
        await sm.simulate_gate_event(
            session, nac_client, whatsapp_client, traveller_id=body.traveller_id,
            zone_id=body.zone_id, gate=body.gate,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {}


@router.post("/demo/simulate-reachability")
async def demo_simulate_reachability(body: SimulateReachabilityIn, session: AsyncSession = Depends(get_session)):
    await sm.simulate_reachability(session, whatsapp_client, traveller_id=body.traveller_id, reachable=body.reachable)
    return {}


@router.get("/demo/trips/active")
async def demo_trips_active(traveller_id: str, session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    trip = await sm.active_trip_for(session, traveller_id)
    if trip is None:
        return None
    return {"trip_id": trip.id, "state": trip.state.value}


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: str, session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(404, "unknown trip")
    return trip_to_dict(trip)


@router.post("/demo/clock/advance")
async def demo_clock_advance(body: ClockAdvanceIn, session: AsyncSession = Depends(get_session)):
    now = clock.advance(body.seconds)
    await sm.tick(session, whatsapp_client)
    return {"now": now.isoformat()}


@router.post("/demo/battery")
async def demo_battery(body: BatteryIn, session: AsyncSession = Depends(get_session)):
    try:
        await sm.set_battery(session, traveller_id=body.traveller_id, level=body.level)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {}


@router.post("/demo/agent/model")
async def demo_agent_model(body: AgentModelIn):
    runtime_state.agent_model_enabled = body.enabled
    return {"enabled": body.enabled}


@router.post("/demo/contact-reply")
async def demo_contact_reply(body: ContactReplyIn, session: AsyncSession = Depends(get_session)):
    try:
        trip = await sm.contact_reply(session, trip_id=body.trip_id, reply=body.reply)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return trip_to_dict(trip)


@router.get("/demo/api-log")
async def demo_api_log(
    since: str | None = None, limit: int = 200,
    session: AsyncSession = Depends(get_session),
):
    """Every real Nokia call, oldest first.

    Bounded: this used to return the whole table on every request, and the
    demo conductor polls it once per beat. `limit` takes the most recent N
    and returns them still in ascending order, so the conductor's
    "last 6 calls" view is unaffected while a long session can't turn one
    poll into a full-table read."""
    limit = max(1, min(1000, limit))
    stmt = select(ApiLogEntry).order_by(ApiLogEntry.ts.asc())
    if since:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        since_dt = since_dt.replace(tzinfo=None)  # naive-UTC convention, see clock.py
        stmt = stmt.where(ApiLogEntry.ts >= since_dt)
    # Newest N, then flipped back to ascending — a plain ascending LIMIT
    # would return the *oldest* N, which is the opposite of useful.
    count_stmt = stmt.order_by(None).order_by(ApiLogEntry.ts.desc()).limit(limit)
    result = await session.execute(count_stmt)
    rows = list(reversed(result.scalars().all()))
    return [
        {
            "ts": r.ts.isoformat(), "api": r.api, "endpoint": r.endpoint,
            "latency_ms": r.latency_ms, "status": r.status, "cost_usd": r.cost_usd,
        }
        for r in rows
    ]
