"""Trip lifecycle service. State lives entirely here (backed by the DB),
never assuming the phone is awake or online.

    IDLE --area-entered(entry)--> BUFFER --agent completes--> ACTIVE
    ACTIVE --reachable | area-entered(exit)--> EXITED
    ACTIVE --monitoring window expires--> OVERDUE
    OVERDUE --SMS to contact--> TIER1_ALERTED
    TIER1_ALERTED --contact confirms safe--> RESOLVED
    TIER1_ALERTED --grace elapses, no reply--> TIER2_ESCALATED

The transition out of ACTIVE is driven by the virtual clock (tick), never
by the agent — see docs/SignalGuard_Technical_Feasibility.pdf §3.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .agent.graph import run_agent
from .agent.tools import AgentContext
from .clock import clock
from .config import settings
from .models import Contact, Subscription, Traveller, Trip, TripState, Zone
from .models import _uuid
from .nokia_client import NokiaClient
from .notifications import notify_entry, notify_overdue, notify_safe_exit
from .runtime_state import runtime_state
from .whatsapp_client import WhatsAppClient

NON_TERMINAL_STATES = {
    TripState.BUFFER, TripState.ACTIVE, TripState.OVERDUE, TripState.TIER1_ALERTED,
    TripState.TIER2_ESCALATED,
}


def battery_band(level: int) -> str:
    return "low" if level < 25 else "healthy"


async def create_traveller(
    session: AsyncSession, *, msisdn: str, name: str, contacts: list[dict],
) -> Traveller:
    traveller = Traveller(msisdn=msisdn, name=name, created_at=clock.now())
    session.add(traveller)
    await session.flush()
    for c in contacts:
        session.add(Contact(traveller_id=traveller.id, name=c["name"], msisdn=c["msisdn"]))
    await session.commit()
    await session.refresh(traveller)
    return traveller


async def get_zone(session: AsyncSession, zone_id: str) -> Zone | None:
    return await session.get(Zone, zone_id)


async def arm_zone(
    session: AsyncSession, nac: NokiaClient, *, zone_id: str, traveller_id: str,
) -> tuple[str, str]:
    """Creates REAL CAMARA Geofencing Subscriptions on Nokia's sandbox, one
    per gate, against the fixed simulator device."""
    zone = await get_zone(session, zone_id)
    if zone is None:
        raise ValueError(f"unknown zone {zone_id}")

    entry_result = await nac.create_geofence_subscription(
        device_phone=settings.nac_device,
        sink=f"{settings.signalguard_webhook_base}/hooks/geofence",
        lat=zone.entry_lat, lon=zone.entry_lon, radius_m=zone.gate_radius_m,
    )
    exit_result = await nac.create_geofence_subscription(
        device_phone=settings.nac_device,
        sink=f"{settings.signalguard_webhook_base}/hooks/geofence",
        lat=zone.exit_lat, lon=zone.exit_lon, radius_m=zone.gate_radius_m,
    )
    entry_id = entry_result.get("id") or f"sub_entry_{traveller_id[:8]}"
    exit_id = exit_result.get("id") or f"sub_exit_{traveller_id[:8]}"

    session.add(Subscription(
        traveller_id=traveller_id, zone_id=zone_id, gate="entry",
        nac_subscription_id=entry_id,
    ))
    session.add(Subscription(
        traveller_id=traveller_id, zone_id=zone_id, gate="exit",
        nac_subscription_id=exit_id,
    ))
    await session.commit()
    return entry_id, exit_id


async def active_trip_for(session: AsyncSession, traveller_id: str) -> Trip | None:
    result = await session.execute(
        select(Trip)
        .where(Trip.traveller_id == traveller_id, Trip.state.in_(NON_TERMINAL_STATES))
        .order_by(Trip.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def simulate_gate_event(
    session: AsyncSession, nac: NokiaClient, wa: WhatsAppClient, *,
    traveller_id: str, zone_id: str, gate: str,
) -> Trip | None:
    traveller = await session.get(Traveller, traveller_id)
    if traveller is None:
        raise ValueError(f"unknown traveller {traveller_id}")

    if gate == "entry":
        existing = await active_trip_for(session, traveller_id)
        if existing is not None:
            return existing  # already open — CAMARA's initialEvent case

        zone = await get_zone(session, zone_id)
        if zone is None:
            raise ValueError(f"unknown zone {zone_id}")

        now = clock.now()
        # id set explicitly (not left to the column's flush-time default)
        # so this Trip is a real, addressable object before it ever
        # touches the session — see the comment below on why that matters.
        trip = Trip(
            id=_uuid(),
            traveller_id=traveller_id, zone_id=zone_id, state=TripState.BUFFER,
            created_at=now, entered_at=now,
            battery_at_entry=traveller.battery_level,
            battery_band=battery_band(traveller.battery_level),
            entry_point=None, notifications=[],
        )

        # Committed in BUFFER now, *before* the agent runs — this is what
        # actually makes "preparing you now…" visible to the app's poll.
        # Committing (not just flushing) means this transaction closes
        # here; nothing stays open while the agent's slow Nokia + Gemini
        # calls run next, each of which logs to api_log via its own
        # independent session/connection (see nac_singleton.py's
        # docstring). SQLite allows only one open writer transaction at a
        # time — an open-but-uncommitted trip insert spanning those calls
        # is exactly what caused a real deadlock before this was fixed
        # (see db.py's docstring). Committing BUFFER first and ACTIVE
        # second, rather than one commit at the end, gets both: a real
        # intermediate state another connection can actually observe, and
        # no transaction left open during the slow part.
        session.add(trip)
        await session.commit()
        await session.refresh(trip)

        ctx = AgentContext(session=session, nac=nac, trip=trip, zone=zone)
        decision = await run_agent(
            ctx, model_enabled=runtime_state.agent_model_enabled,
            battery_pct=traveller.battery_level,
        )
        trip.state = TripState.ACTIVE
        trip.window_deadline = now + timedelta(minutes=decision.monitoring_window_min)
        trip.tier1_deadline = trip.window_deadline + timedelta(minutes=settings.tier1_grace_min)
        await session.commit()
        await session.refresh(trip)
        await notify_entry(session, wa, trip, zone)
        return trip

    if gate == "exit":
        trip = await active_trip_for(session, traveller_id)
        if trip is None:
            return trip
        # A traveller who's already overdue (contact and maybe the
        # emergency centre alerted) still eventually drives out the exit
        # gate in the ordinary case — they were just running late, not
        # actually in trouble. That has to clear the escalation, not get
        # silently ignored because the code only expected the happy path
        # here. RESOLVED (not EXITED) so the record shows this trip did
        # escalate before it closed.
        if trip.state == TripState.ACTIVE:
            trip.state = TripState.EXITED
            trip.resolved_at = clock.now()
        elif trip.state in (TripState.OVERDUE, TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED):
            trip.state = TripState.RESOLVED
            trip.resolved_at = clock.now()
        else:
            return trip
        await session.commit()
        await session.refresh(trip)
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_safe_exit(session, wa, trip, zone)
        return trip

    raise ValueError(f"unknown gate {gate!r}")


async def simulate_reachability(
    session: AsyncSession, wa: WhatsAppClient, *, traveller_id: str, reachable: bool,
) -> Trip | None:
    trip = await active_trip_for(session, traveller_id)
    if trip is None or not reachable:
        return trip

    was_overdue = trip.state in (
        TripState.OVERDUE, TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
    )
    if trip.state == TripState.ACTIVE:
        trip.state = TripState.EXITED
        trip.resolved_at = clock.now()
    elif was_overdue:
        # Late reconnection — the traveller is fine after all.
        trip.state = TripState.RESOLVED
        trip.resolved_at = clock.now()
    await session.commit()
    await session.refresh(trip)

    if trip.state in (TripState.EXITED, TripState.RESOLVED):
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_safe_exit(session, wa, trip, zone)
    return trip


async def tick(session: AsyncSession, wa: WhatsAppClient) -> None:
    """Applies timer-driven transitions against the current virtual clock.
    Called after /demo/clock/advance and opportunistically on trip reads,
    so escalation never depends on a background scheduler firing in a demo
    process. Escalation is timer-driven, not model-driven — this function
    is the timer."""
    now = clock.now()
    result = await session.execute(
        select(Trip).where(Trip.state.in_(
            {TripState.ACTIVE, TripState.OVERDUE, TripState.TIER1_ALERTED}
        ))
    )
    trips = result.scalars().all()
    changed = False
    newly_overdue: list[Trip] = []
    for trip in trips:
        if trip.state == TripState.ACTIVE and trip.window_deadline and now >= trip.window_deadline:
            trip.state = TripState.OVERDUE
            changed = True
        if trip.state == TripState.OVERDUE:
            notes = list(trip.notifications or [])
            if "tier1" not in notes:
                notes.append("tier1")
                trip.notifications = notes
            trip.state = TripState.TIER1_ALERTED
            changed = True
            newly_overdue.append(trip)
        if trip.state == TripState.TIER1_ALERTED and trip.tier1_deadline and now >= trip.tier1_deadline:
            notes = list(trip.notifications or [])
            if "tier2" not in notes:
                notes.append("tier2")
                trip.notifications = notes
            trip.state = TripState.TIER2_ESCALATED
            changed = True
    if changed:
        await session.commit()
    for trip in newly_overdue:
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_overdue(session, wa, trip, zone)


async def contact_reply(session: AsyncSession, *, trip_id: str, reply: str) -> Trip:
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise ValueError(f"unknown trip {trip_id}")
    if reply == "safe" and trip.state in (
        TripState.OVERDUE, TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
    ):
        trip.state = TripState.RESOLVED
        trip.resolved_at = clock.now()
        await session.commit()
        await session.refresh(trip)
    return trip


async def set_battery(session: AsyncSession, *, traveller_id: str, level: int) -> None:
    traveller = await session.get(Traveller, traveller_id)
    if traveller is None:
        raise ValueError(f"unknown traveller {traveller_id}")
    traveller.battery_level = level
    await session.commit()
