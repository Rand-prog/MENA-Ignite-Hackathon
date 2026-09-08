"""Trip lifecycle service. State lives entirely here (backed by the DB),
never assuming the phone is awake or online.

    IDLE  --area-entered(entry)--> BUFFER --agent completes--> ACTIVE
    ACTIVE --reachable | area-entered(exit)--> EXITED
    ACTIVE --monitoring window expires, handset reachable--> TIER0_CHECKING
    ACTIVE --monitoring window expires, handset dark------> OVERDUE
    TIER0_CHECKING --traveller answers "safe"--> RESOLVED
    TIER0_CHECKING --tier0 grace elapses------> OVERDUE
    OVERDUE --SMS to contact--> TIER1_ALERTED
    TIER1_ALERTED --contact or convoy peer confirms safe--> RESOLVED
    TIER1_ALERTED --grace elapses, no reply--> TIER2_ESCALATED

The transition out of ACTIVE is driven by the virtual clock (tick), never
by the agent — see docs/SignalGuard_Technical_Feasibility.pdf §3.

TIER0_CHECKING is the one rung added below Tier 1: before a human is told
anything, the traveller's own handset is asked. It is entered only when the
network says the handset is reachable again (asking a dark phone buys
nothing), and it is bounded by its own deadline like every other state, so
the worst case of a traveller who never answers is a 90-second delay and
then the identical Tier 1 that would have fired anyway. Nothing here can
suppress an alarm — only the traveller actively answering "I'm safe" can,
which is the same authority a contact reply has always had.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import convoy, corridor_stats, coverage, retention
from .agent.escalation_graph import run_escalation_agent
from .agent.graph import run_agent
from .agent.tools import AgentContext, check_device_reachability, request_qod_session
from .clock import clock
from .config import settings
from .models import Contact, Subscription, Traveller, Trip, TripState, Zone
from .models import _uuid
from .nac_singleton import nac_client
from .nokia_client import NokiaClient
from .notifications import notify_entry, notify_overdue, notify_safe_exit
from .runtime_state import runtime_state
from .whatsapp_client import WhatsAppClient

NON_TERMINAL_STATES = {
    TripState.BUFFER, TripState.ACTIVE, TripState.TIER0_CHECKING, TripState.OVERDUE,
    TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
}

# States a trip can be in and still be "someone might need help" — used by
# the exit paths to decide whether closing the trip is a quiet completion
# or the clearing of a live escalation.
ESCALATED_STATES = {
    TripState.OVERDUE, TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
}

REACHABLE_STATUSES = {"CONNECTED_DATA", "CONNECTED_SMS"}


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
) -> tuple[str, str, str | None]:
    """Creates the REAL CAMARA subscriptions this traveller is watched by:
    two Geofencing Subscriptions, one per gate, and one Device
    Reachability Status subscription — all against the fixed simulator
    device.

    The reachability subscription is the one the safety claim rests on.
    docs/SignalGuard_Technical_Feasibility.pdf §2 is explicit that
    reachability is consumed "on change, via subscription" rather than
    polled — "it removes polling entirely, which cuts per-crossing API
    cost to near zero and means reconnection is detected within the
    operator's notification latency rather than within one poll interval"
    — and §8 prices the API at ~0 per crossing on exactly that basis. Its
    notifications land on /hooks/reachability.

    All three are independent requests, so they go out concurrently —
    measured on the live sandbox, running the two geofence creates
    together halved this call (402ms sequential -> 219ms, median of 8
    runs), and the reachability create rides along in the same beat rather
    than adding its latency on the end. Nothing about the result depends
    on ordering.

    The reachability id comes back as None if the sandbox rejects that
    subscription: it is created per device rather than per gate, so a
    second arm of another zone for the same traveller is a duplicate as
    far as the operator is concerned. Arming still succeeds — the retrieve
    endpoint is the redundancy path, and a zone whose gates are armed is
    still a monitored zone."""
    zone = await get_zone(session, zone_id)
    if zone is None:
        raise ValueError(f"unknown zone {zone_id}")

    sink = f"{settings.signalguard_webhook_base}/hooks/geofence"
    entry_result, exit_result, reach_result = await asyncio.gather(
        nac.create_geofence_subscription(
            device_phone=settings.nac_device, sink=sink,
            lat=zone.entry_lat, lon=zone.entry_lon, radius_m=zone.gate_radius_m,
        ),
        nac.create_geofence_subscription(
            device_phone=settings.nac_device, sink=sink,
            lat=zone.exit_lat, lon=zone.exit_lon, radius_m=zone.gate_radius_m,
        ),
        nac.create_reachability_subscription(
            device_phone=settings.nac_device,
            sink=f"{settings.signalguard_webhook_base}/hooks/reachability",
            access_token=settings.nac_sink_access_token,
        ),
    )
    entry_id = entry_result.get("id") or f"sub_entry_{traveller_id[:8]}"
    exit_id = exit_result.get("id") or f"sub_exit_{traveller_id[:8]}"
    reach_id = reach_result.get("id")

    session.add(Subscription(
        traveller_id=traveller_id, zone_id=zone_id, gate="entry",
        nac_subscription_id=entry_id,
    ))
    session.add(Subscription(
        traveller_id=traveller_id, zone_id=zone_id, gate="exit",
        nac_subscription_id=exit_id,
    ))
    if reach_id:
        session.add(Subscription(
            traveller_id=traveller_id, zone_id=zone_id, gate="reachability",
            nac_subscription_id=reach_id,
        ))
    await session.commit()
    return entry_id, exit_id, reach_id


async def active_trip_for(session: AsyncSession, traveller_id: str) -> Trip | None:
    result = await session.execute(
        select(Trip)
        .where(Trip.traveller_id == traveller_id, Trip.state.in_(NON_TERMINAL_STATES))
        .order_by(Trip.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _apply_deadlines(trip: Trip, *, anchor: datetime, window_min: int) -> None:
    """Set the whole deadline chain from one anchor.

    Kept in one place because these three timestamps have to stay
    consistent with each other — a planned stop that extended the window
    but not the Tier 1 grace would produce a trip that goes overdue and
    escalates to the emergency centre in the same instant, with no window
    for the contact to reply in."""
    stop = max(0, trip.planned_stop_min or 0)
    trip.window_deadline = anchor + timedelta(minutes=window_min + stop)
    trip.tier0_deadline = trip.window_deadline + timedelta(seconds=settings.tier0_grace_sec)
    trip.tier1_deadline = trip.tier0_deadline + timedelta(minutes=settings.tier1_grace_min)


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
            planned_stop_min=traveller.pending_stop_min or 0,
        )
        # A declared stop is consumed by the crossing it was declared for.
        # Leaving it on the traveller would silently widen every future
        # crossing too, which is the opposite of a *planned* stop.
        traveller.pending_stop_min = 0

        # Two travellers in the same corridor within the convoy window are
        # travelling together for practical purposes — see convoy.py. This
        # is derived from entry time, never declared by the user.
        await convoy.assign(session, trip)

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
        _apply_deadlines(trip, anchor=now, window_min=decision.monitoring_window_min)
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
        if not await _close_trip(session, trip, signal="gate"):
            return trip
        await session.commit()
        await session.refresh(trip)
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_safe_exit(session, wa, trip, zone)
        return trip

    raise ValueError(f"unknown gate {gate!r}")


async def _close_trip(session: AsyncSession, trip: Trip, *, signal: str) -> bool:
    """Move a trip to its terminal state and write its history row.

    Returns False when the trip was already closed, so callers don't fire
    a second "made it through safely" message for the same crossing.

    EXITED means a quiet completion; RESOLVED means this crossing did
    escalate before it closed, and the record has to keep saying so — a
    trip that woke somebody's contact at 2am should not read afterwards as
    though nothing happened."""
    if trip.state not in NON_TERMINAL_STATES:
        return False

    # "tier0" in notifications is deliberately NOT escalation — Tier 0 is
    # defined by the fact that no human was told. A crossing that asked the
    # traveller and got an answer closes as a clean EXITED, and its
    # duration stays eligible for the learned baseline.
    escalated = trip.state in ESCALATED_STATES or any(
        n != "tier0" for n in (trip.notifications or [])
    )
    trip.state = TripState.RESOLVED if escalated else TripState.EXITED
    trip.resolved_at = clock.now()
    trip.exit_signal = signal

    if trip.entered_at is not None:
        trip.actual_crossing_min = await corridor_stats.record_crossing(
            session,
            zone_id=trip.zone_id,
            entered_at=trip.entered_at,
            closed_at=trip.resolved_at,
            congestion_tier=trip.congestion_tier,
            battery_band=trip.battery_band,
            # An escalated crossing is a real duration but not evidence of a
            # normal one; letting it into the learned baseline would make
            # the system less likely to alarm the more often it had to.
            clean=not escalated,
        )
    return True


async def simulate_reachability(
    session: AsyncSession, wa: WhatsAppClient, *, traveller_id: str, reachable: bool,
) -> Trip | None:
    """The handset came back (or went dark).

    Reachability is the PRIMARY exit signal, not the geofence. A traveller
    who leaves the corridor by a side road never crosses the exit gate, and
    gate-only exit detection turns that ordinary event into a Tier 1 alarm
    about a person who is fine and already home. The exit gate is a
    confirmation of something the network has usually told us first.
    """
    # Recorded before anything else, and whether or not there is a trip:
    # this is the notification the Device Reachability Status subscription
    # created in arm_zone() pushes on change, and it is the current answer
    # for this device from here on. Holding it is what lets the escalation
    # path below read reachability instead of spending a CAMARA call to
    # ask what the network has already said.
    await _record_pushed_reachability(session, traveller_id, reachable=reachable)

    trip = await active_trip_for(session, traveller_id)
    if trip is None:
        await session.commit()
        return trip

    if not reachable:
        # Going dark is not a state change — it is the expected condition
        # for the whole crossing. Recorded as coverage evidence only.
        await _record_coverage(session, trip, reachable=False)
        await session.commit()
        return trip

    was_escalated = trip.state in ESCALATED_STATES
    tier0 = trip.state == TripState.TIER0_CHECKING

    await _record_coverage(session, trip, reachable=True)

    if not await _close_trip(session, trip, signal="reachability"):
        await session.commit()
        return trip
    await session.commit()
    await session.refresh(trip)

    # Quality on Demand is spent HERE, on the reconnection edge — not at
    # entry. A boost applied to a handset that is seconds away from losing
    # signal buys nothing; the first moments back online are when the
    # position upload and the all-clear actually need the bandwidth, and
    # they are also the moments a worried contact is waiting on. Same API,
    # same one call per crossing, materially better spent.
    if trip.qod_warranted:
        zone = await get_zone(session, trip.zone_id)
        if zone is not None:
            ctx = AgentContext(session=session, nac=nac_client, trip=trip, zone=zone)
            result = await request_qod_session(ctx, trip.id)
            if result.get("requested"):
                trip.qod_session_id = result.get("session_id")
                trip.qod_edge = "reconnect"
                await session.commit()

    if trip.state in (TripState.EXITED, TripState.RESOLVED):
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_safe_exit(session, wa, trip, zone)
    # A reconnection that lands during Tier 0 or after an escalation is
    # exactly the case Tier 0 exists to catch; nothing extra to do here
    # beyond the safe-exit message the contact already gets.
    _ = (was_escalated, tier0)
    return trip


async def _record_pushed_reachability(
    session: AsyncSession, traveller_id: str, *, reachable: bool,
) -> None:
    """Store what the reachability subscription just pushed. Not committed
    here — the callers all commit, and this must land in the same
    transaction as whatever state change the notification caused."""
    traveller = await session.get(Traveller, traveller_id)
    if traveller is None:
        return
    traveller.last_reachable = reachable
    traveller.last_reachability_at = clock.now()


async def _pushed_reachability(session: AsyncSession, trip: Trip) -> bool | None:
    """What the network last pushed about this handset during THIS
    crossing, or None if it has not said anything yet.

    Bounded to the crossing on purpose. A notification from before the
    trip opened describes a device that was on a road with coverage,
    which says nothing about whether it is reachable now — trusting it
    would be reading a stale answer as a current one, and in the
    reachable direction that means offering Tier 0 to a handset that is
    dark. Outside that window the caller falls back to the retrieve
    endpoint, which is the same behaviour as before any of this existed."""
    if trip.entered_at is None:
        return None
    traveller = await session.get(Traveller, trip.traveller_id)
    if traveller is None or traveller.last_reachability_at is None:
        return None
    if traveller.last_reachability_at < trip.entered_at:
        return None
    return traveller.last_reachable


async def _record_coverage(session: AsyncSession, trip: Trip, *, reachable: bool) -> None:
    """Feed the auto-discovery store. The position used is the entry-gate
    snapshot, which is the only real fix this system ever holds — see
    coverage.py on why nothing here is joinable back to a person."""
    lat = lon = None
    point = trip.entry_point or trip.last_known_location
    if point:
        try:
            lat_s, lon_s = point.split(",")
            lat, lon = float(lat_s.strip()), float(lon_s.strip())
        except (ValueError, AttributeError):
            lat = lon = None
    await coverage.record_observation(
        session, lat=lat, lon=lon, reachable=reachable,
        congestion_level=trip.congestion_tier, observed_at=clock.now(),
    )


async def declare_planned_stop(
    session: AsyncSession, *, traveller_id: str, minutes: int,
) -> dict:
    """"I'm stopping for lunch" — the single cheapest false-alarm fix there
    is.

    The commonest reason a crossing runs long is not an emergency, it is a
    person who stopped. Today that produces a Tier 1 text to a contact who
    then phones around about somebody sitting in a roadside cafe, and the
    cost of a few of those is that the contact starts ignoring the texts —
    which is the only way this product actually fails.

    Applies to a live trip immediately, and is otherwise held on the
    traveller for the next crossing to pick up. Capped at
    planned_stop_max_min so a declaration can't quietly switch monitoring
    off for a whole day."""
    traveller = await session.get(Traveller, traveller_id)
    if traveller is None:
        raise ValueError(f"unknown traveller {traveller_id}")
    minutes = max(0, min(settings.planned_stop_max_min, int(minutes)))

    trip = await active_trip_for(session, traveller_id)
    if trip is not None and trip.state in (TripState.ACTIVE, TripState.TIER0_CHECKING):
        # Extend from the existing deadlines rather than recomputing from
        # entry: the agent's window was a judgement about this crossing and
        # a declared break is additive to it, not a replacement for it.
        added = minutes - (trip.planned_stop_min or 0)
        if added > 0:
            trip.planned_stop_min = minutes
            trip.window_deadline = (trip.window_deadline or clock.now()) + timedelta(minutes=added)
            trip.tier0_deadline = (trip.tier0_deadline or trip.window_deadline) + timedelta(minutes=added)
            trip.tier1_deadline = (trip.tier1_deadline or trip.window_deadline) + timedelta(minutes=added)
            # A trip already in Tier 0 because its window expired should go
            # back to simply being in progress — the traveller has just
            # explained the delay.
            if trip.state == TripState.TIER0_CHECKING:
                trip.state = TripState.ACTIVE
                trip.tier0_asked_at = None
        await session.commit()
        await session.refresh(trip)
        return {
            "applied_to": "trip",
            "trip_id": trip.id,
            "planned_stop_min": trip.planned_stop_min,
            "window_deadline": trip.window_deadline.isoformat() if trip.window_deadline else None,
        }

    traveller.pending_stop_min = minutes
    await session.commit()
    return {"applied_to": "next_trip", "planned_stop_min": minutes}


async def tier0_answer(
    session: AsyncSession, *, traveller_id: str, answer: str,
) -> Trip | None:
    """The traveller answered the Tier 0 ping themselves.

    "safe" closes the trip without any human ever being contacted — which
    is the whole point, and restores the silent-happy-path promise in
    docs/SignalGuard_Security_Privacy §7. Any other answer (or none) lets
    the timer do exactly what it would have done anyway."""
    trip = await active_trip_for(session, traveller_id)
    if trip is None or trip.state != TripState.TIER0_CHECKING:
        return trip
    trip.tier0_answer = answer
    if answer == "safe":
        await _close_trip(session, trip, signal="tier0_self_report")
    await session.commit()
    await session.refresh(trip)
    return trip


async def _run_escalation(session: AsyncSession, trip: Trip, *, tier: str) -> None:
    """Best-effort enrichment via the escalation agent (real Nokia
    check_device_reachability + notify_contact/escalate_to_dashboard tool
    calls — see agent/escalation_graph.py). Guaranteed fallback below keeps
    the actual invariant intact regardless of whether this succeeds:
    escalation is timer-driven, and the tier record must land even if the
    agent, or the zone lookup, fails."""
    zone = await get_zone(session, trip.zone_id)
    if zone is not None:
        ctx = AgentContext(session=session, nac=nac_client, trip=trip, zone=zone)
        await run_escalation_agent(ctx, tier=tier)  # never raises, see its own try/except
    notes = list(trip.notifications or [])
    if tier not in notes:
        notes.append(tier)
        trip.notifications = notes


async def _handset_reachable(session: AsyncSession, trip: Trip, *, zone: Zone | None) -> bool:
    """One real Device Reachability *retrieve* — the redundancy path, used
    only for a trip the reachability subscription has pushed nothing about
    since it opened (a lapsed subscription, or a lost notification). The
    normal path reads the pushed state via _pushed_reachability and costs
    no call at all; see tick()'s phase 2.

    Any failure reads as "not reachable", which routes straight to Tier 1 —
    a reachability check that errors must never be able to hold an alarm
    open.

    Takes the zone pre-resolved rather than looking it up itself — see
    tick()'s phase 2 comment on why: this runs concurrently with its
    siblings via asyncio.gather, and check_device_reachability never
    touches ctx.session (only ctx.nac), but a zone *lookup* is a session
    read, and AsyncSession does not tolerate two coroutines touching it at
    once. Resolving zones stays sequential; only the network round-trips
    run concurrently."""
    if zone is None:
        return False
    ctx = AgentContext(session=session, nac=nac_client, trip=trip, zone=zone)
    try:
        result = await check_device_reachability(ctx, trip.id)
    except Exception:  # noqa: BLE001 — see docstring; failure means "escalate"
        return False
    return result.get("status") in REACHABLE_STATUSES


async def tick(session: AsyncSession, wa: WhatsAppClient) -> None:
    """Applies timer-driven transitions against the current virtual clock.
    Called after /demo/clock/advance and opportunistically on trip reads,
    so escalation never depends on a background scheduler firing in a demo
    process. Escalation is timer-driven, not model-driven — this function
    is the timer.

    Structured in three phases for one reason: no real network call may
    happen while this session holds an open writer transaction.
    nac_singleton logs every real call through its own independent session
    (see that module's docstring), and a write on that connection while
    this one has an uncommitted transaction open is a SQLite "database is
    locked" deadlock — simulate_gate_event hit exactly this and was fixed
    the same way. So: read and decide (phase 1), make the network calls
    with nothing pending (phase 2), then write and commit (phase 3).
    """
    # Opportunistic retention prune — see retention.py. Throttled to a
    # timestamp check in the common case, so putting it here (tick() runs
    # on five endpoints, including both poll paths) doesn't cost this hot
    # path anything most of the time, and there is no active-trip count
    # this can be gated on: the tables it prunes (api_log,
    # crossing_history, coverage_observations) grow from CLOSED trips, so
    # it has to run even in the all-quiet, zero-active-trips case, which
    # is most of the time.
    await retention.maybe_prune(session)

    now = clock.now()

    # -- phase 1: which trips have run out of window? ----------------------
    #
    # The predicate below is the SQL twin of phase 3's loop: every one of
    # these four cases is a trip that phase 3 would set `moved` on, and
    # nothing outside them can move. It used to be a plain
    # `state IN (ACTIVE, TIER0_CHECKING, OVERDUE, TIER1_ALERTED)` with the
    # deadline comparisons done in Python afterwards, which meant every
    # poll from every app and every dashboard loaded and instantiated a
    # full ORM Trip for every crossing currently in flight — and then, in
    # the overwhelmingly common case, did nothing with any of them. A
    # deadline that has not arrived yet is exactly the kind of thing an
    # index answers for free; now the quiet case is one indexed lookup
    # that returns no rows and returns here.
    result = await session.execute(
        select(Trip).where(
            or_(
                and_(
                    Trip.state == TripState.ACTIVE,
                    Trip.window_deadline.is_not(None),
                    Trip.window_deadline <= now,
                ),
                and_(
                    Trip.state == TripState.TIER0_CHECKING,
                    Trip.tier0_deadline.is_not(None),
                    Trip.tier0_deadline <= now,
                ),
                # OVERDUE always advances to TIER1 on the next tick — there
                # is no deadline left to wait on, that is what OVERDUE means.
                Trip.state == TripState.OVERDUE,
                and_(
                    Trip.state == TripState.TIER1_ALERTED,
                    Trip.tier1_deadline.is_not(None),
                    Trip.tier1_deadline <= now,
                ),
            )
        )
    )
    trips = result.scalars().all()
    if not trips:
        return

    # Every ACTIVE row the query returned is one whose window has already
    # expired — that is the only reason it came back.
    expiring = [t for t in trips if t.state == TripState.ACTIVE]
    # Close the read transaction before any network call below.
    await session.commit()

    # -- phase 2: network, with nothing pending on this session ------------
    # Tier 0 is only worth attempting against a handset the network says is
    # actually back, so the question this phase answers is one per expiring
    # trip: is this device reachable right now?
    #
    # Zones and the pushed state are resolved sequentially, first — both are
    # session reads, and AsyncSession doesn't tolerate two coroutines
    # touching the same session at once. check_device_reachability itself
    # never touches ctx.session (only ctx.nac), so once each trip has its
    # zone in hand the actual network round-trips are safe to run
    # concurrently. A probe that raises still reads as "not reachable" (see
    # _handset_reachable's own try/except) — gather() doesn't change that;
    # nothing here can hold an alarm open by failing slowly or failing at all.
    zones_by_id: dict[str, Zone | None] = {}
    for trip in expiring:
        if trip.zone_id not in zones_by_id:
            zones_by_id[trip.zone_id] = await get_zone(session, trip.zone_id)

    # The subscription created in arm_zone() pushes reachability on change,
    # so for most crossings the answer is already on the traveller row and
    # this costs no CAMARA call at all — which is the whole point of the
    # subscription form over the retrieve one (Technical Feasibility §2,
    # and the ~0 per-crossing figure in §8). A pushed "dark" is just as
    # useful as a pushed "back": probing a handset the network has already
    # told us is unreachable spends a call to learn something known and
    # delays the alarm by the Tier 0 grace for no possible benefit.
    #
    # Only a trip the subscription has said nothing about since it opened
    # falls through to the retrieve endpoint. That is the redundancy path
    # — a lapsed subscription or a lost notification — and it stays a
    # concurrent gather because convoy mode (convoy.py) groups travellers
    # who entered together, so their windows expire together too:
    # serial probing scaled linearly with that (1 trip 243ms, 3 trips
    # 678ms, 6 trips 1292ms, all inside one poll), gather()ing them makes
    # N probes cost what one does.
    tier0_eligible: set[str] = set()
    needs_probe: list[Trip] = []
    for trip in expiring:
        pushed = await _pushed_reachability(session, trip)
        if pushed is None:
            needs_probe.append(trip)
        elif pushed:
            tier0_eligible.add(trip.id)

    if needs_probe:
        probe_results = await asyncio.gather(*(
            _handset_reachable(session, trip, zone=zones_by_id[trip.zone_id])
            for trip in needs_probe
        ))
        tier0_eligible |= {
            trip.id for trip, reachable in zip(needs_probe, probe_results) if reachable
        }

    # -- phase 3: apply transitions and commit -----------------------------
    changed: list[Trip] = []
    newly_overdue: list[Trip] = []
    newly_tier0: list[Trip] = []
    pending_escalations: list[tuple[Trip, str]] = []

    for trip in trips:
        moved = False
        if trip.state == TripState.ACTIVE and trip.window_deadline and now >= trip.window_deadline:
            if trip.id in tier0_eligible:
                trip.state = TripState.TIER0_CHECKING
                trip.tier0_asked_at = now
                newly_tier0.append(trip)
                moved = True
            else:
                trip.state = TripState.OVERDUE
                moved = True

        # Tier 0 never holds an alarm past its own deadline. A traveller who
        # doesn't answer gets exactly the Tier 1 they would have got, 90
        # seconds later.
        if (
            trip.state == TripState.TIER0_CHECKING
            and trip.tier0_deadline
            and now >= trip.tier0_deadline
        ):
            trip.state = TripState.OVERDUE
            moved = True

        if trip.state == TripState.OVERDUE:
            trip.state = TripState.TIER1_ALERTED
            newly_overdue.append(trip)
            pending_escalations.append((trip, "tier1"))
            moved = True

        if trip.state == TripState.TIER1_ALERTED and trip.tier1_deadline and now >= trip.tier1_deadline:
            trip.state = TripState.TIER2_ESCALATED
            pending_escalations.append((trip, "tier2"))
            moved = True

        if moved:
            changed.append(trip)

    if changed:
        # No refresh pass afterwards. This used to re-SELECT every row it
        # had just written; the reasoning that removed that for the
        # *untouched* trips (expire_on_commit=False, so nothing is expired
        # by the commit) applies just as well to the changed ones — their
        # new state was assigned here in Python and flushed from these very
        # objects, so a read-back can only ever return what is already in
        # memory.
        await session.commit()

    # Now safe to run — no open writer transaction on this session while
    # these await real Nokia calls that log through a different one.
    for trip, tier in pending_escalations:
        await _run_escalation(session, trip, tier=tier)
    if pending_escalations:
        await session.commit()

    for trip in newly_tier0:
        await _notify_tier0(session, trip)
    for trip in newly_overdue:
        zone = await get_zone(session, trip.zone_id)
        if zone:
            await notify_overdue(session, wa, trip, zone)


async def _notify_tier0(session: AsyncSession, trip: Trip) -> None:
    """Record that the device itself was asked. There is no message to a
    human here by design — that is the entire point of Tier 0. The app
    picks this up on its next poll (state == TIER0_CHECKING) and prompts
    the traveller directly."""
    notes = list(trip.notifications or [])
    if "tier0" not in notes:
        notes.append("tier0")
        trip.notifications = notes
        await session.commit()


async def contact_reply(session: AsyncSession, *, trip_id: str, reply: str) -> Trip:
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise ValueError(f"unknown trip {trip_id}")
    if reply == "safe" and trip.state in (
        TripState.TIER0_CHECKING, TripState.OVERDUE, TripState.TIER1_ALERTED,
        TripState.TIER2_ESCALATED,
    ):
        await _close_trip(session, trip, signal="contact_reply")
        await session.commit()
        await session.refresh(trip)
    return trip


async def set_battery(session: AsyncSession, *, traveller_id: str, level: int) -> None:
    traveller = await session.get(Traveller, traveller_id)
    if traveller is None:
        raise ValueError(f"unknown traveller {traveller_id}")
    traveller.battery_level = level
    await session.commit()
