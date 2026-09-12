"""Real product surface — what would actually ship. Kept intentionally thin
for the prototype: register + token, battery reporting, trip status, a
webhook receiver for real Nokia notifications (the production path, once
operator approval exists — the sandbox path goes through /demo/* for now),
and dashboard reads.

No OIDC/CIBA flow here — out of scope per the build prompt. auth_token is a
plain bearer issued at registration, checked via a simple header dependency.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from .. import api_activity, convoy, corridor_stats, coverage
from .. import state_machine as sm
from ..config import settings
from ..db import get_session
from ..models import Traveller, Trip, TripState, Zone
from ..nac_singleton import nac_client
from ..notifications import notify_test
from ..responses import json_ok
from ..schemas import BatteryReportIn, PlannedStopIn, PromoteCandidateIn, Tier0ResponseIn, TravellerIn
from ..serializers import dashboard_trip_to_dict, trip_to_dict
from ..whatsapp_singleton import whatsapp_client

# The live dashboard queue — trips that are still someone's job to watch.
# EXITED/RESOLVED trips clear from the queue automatically, per
# docs/SignalGuard_User_Flow's Emergency Center Flow §4.
DASHBOARD_STATES = {
    TripState.BUFFER, TripState.ACTIVE, TripState.TIER0_CHECKING, TripState.OVERDUE,
    TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
}

# Closed trips the history view reads. Deliberately separate from the live
# queue: an emergency centre needs the record of what happened as much as
# the list of what is happening, and the queue must never grow into an
# archive that a dispatcher has to scroll past to find a live escalation.
HISTORY_STATES = {TripState.EXITED, TripState.RESOLVED}

router = APIRouter(tags=["real"])


async def require_traveller(
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Traveller:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    result = await session.execute(select(Traveller).where(Traveller.auth_token == token))
    traveller = result.scalar_one_or_none()
    if traveller is None:
        raise HTTPException(401, "invalid token")
    return traveller


@router.post("/travellers")
async def register_traveller(body: TravellerIn, session: AsyncSession = Depends(get_session)):
    try:
        traveller = await sm.create_traveller(
            session, msisdn=body.msisdn, name=body.name,
            contacts=[c.model_dump() for c in body.contacts],
        )
    except IntegrityError:
        # msisdn is unique — re-registering the same number (e.g. reinstalling
        # the app without clearing backend state) hit the DB constraint raw
        # and surfaced as an opaque 500. A re-registration attempt is a
        # normal, expected case, not a server fault.
        await session.rollback()
        if settings.signalguard_demo_mode:
            # Demo-mode convenience: the conductor (scripts/run_demo.py)
            # and a test app both wanting the same fixed msisdn as a
            # shared identity is a normal, common workflow, not an error —
            # whichever registered first still "owns" the row, but the
            # second registration attaches to it instead of bouncing.
            # Never happens outside demo mode — a real deployment keeps
            # the strict 409 below, since a phone number silently handing
            # out a token to anyone who claims it is not something to
            # allow once real users are on the other end of it.
            result = await session.execute(
                select(Traveller).where(Traveller.msisdn == body.msisdn)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return {"traveller_id": existing.id, "auth_token": existing.auth_token}
        raise HTTPException(409, "a traveller with this phone number is already registered")
    return {"traveller_id": traveller.id, "auth_token": traveller.auth_token}


@router.post("/travellers/me/battery")
async def report_battery(
    body: BatteryReportIn | None = None,
    level: int | None = None,
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    """Battery telemetry, in the body.

    This used to be `?level=42` in the query string. A URL is the most
    widely logged part of an HTTP request — reverse proxies, gateways and
    access logs all keep it by default — so putting a person's device
    telemetry there quietly copies it into several places nobody audits.
    The `level` query parameter is still accepted so an app build from
    before this change keeps reporting rather than silently going dark
    (a battery report that stops arriving looks, to the risk model, like a
    phone that never had a low battery)."""
    reported = body.level if body is not None else level
    if reported is None:
        raise HTTPException(422, "expected {level}")
    await sm.set_battery(session, traveller_id=traveller.id, level=int(reported))
    return {"ok": True}


@router.post("/travellers/me/planned-stop")
async def declare_planned_stop(
    body: PlannedStopIn,
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    """"I'm stopping for lunch."

    The commonest reason a crossing runs past its window is not an
    emergency, it is a person who stopped. Every one of those produces a
    Tier 1 text to a contact who then phones around about somebody sitting
    in a roadside cafe — and the real cost is not the one text, it is that
    after a few of them the contact starts ignoring the texts. That is the
    only way this product actually fails."""
    return await sm.declare_planned_stop(
        session, traveller_id=traveller.id, minutes=body.minutes,
    )


@router.post("/travellers/me/tier0-response")
async def tier0_response(
    body: Tier0ResponseIn,
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    """The traveller answering the Tier 0 ping on their own handset.

    Answering "safe" here closes the trip with no human ever contacted.
    Anything else, or no answer at all before tier0_deadline, and the timer
    proceeds to Tier 1 exactly as it would have — see state_machine.tick."""
    trip = await sm.tier0_answer(session, traveller_id=traveller.id, answer=body.answer)
    return trip_to_dict(trip) if trip is not None else None


@router.post("/travellers/me/test-alert")
async def send_test_alert(
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    """Prove the escalation path reaches a real human, at setup time.

    A wrong contact number is the largest silent failure this product has:
    every tier still fires, every record is written, and the message goes
    nowhere. Nothing else checks it. See notifications.notify_test — the
    response reports the real delivery state, including "not_configured",
    rather than claiming success for a no-op."""
    return await notify_test(session, whatsapp_client, traveller.id)


@router.get("/travellers/me/convoy")
async def my_convoy(
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    """Who else is in this corridor right now.

    Membership is derived from entry time, never declared — see convoy.py.
    Returns only what a peer is allowed to know about a peer: that somebody
    is there and roughly where they are in the crossing, never a position."""
    trip = await sm.active_trip_for(session, traveller.id)
    if trip is None:
        return {"convoy_id": None, "peers": []}
    peers = await convoy.peers_for(session, trip)
    return {
        "convoy_id": trip.convoy_id,
        "peers": [convoy.peer_summary(p) for p in peers],
    }


@router.get("/travellers/me/trip")
async def my_trip(
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    await sm.tick(session, whatsapp_client)
    trip = await sm.active_trip_for(session, traveller.id)
    if trip is None:
        return json_ok(None)
    return json_ok(trip_to_dict(trip))


@router.get("/zones")
async def list_zones(session: AsyncSession = Depends(get_session)):
    """Zone registry, read-only. The Flutter app uses this for the local
    on-device geofence fallback and to draw the offline corridor map — it
    never hardcodes zone geometry."""
    result = await session.execute(select(Zone))
    zones = result.scalars().all()
    return [
        {
            "zone_id": z.zone_id, "label": z.label,
            "entry_gate": {"lat": z.entry_lat, "lon": z.entry_lon},
            "exit_gate": {"lat": z.exit_lat, "lon": z.exit_lon},
            "gate_radius_m": z.gate_radius_m,
            "corridor_km": z.corridor_km,
            "nominal_crossing_min": z.nominal_crossing_min,
        }
        for z in zones
    ]


@router.get("/zones/candidates")
async def zone_candidates(session: AsyncSession = Depends(get_session)):
    """Coverage holes the network told us about, that nobody has surveyed.

    The registry ships one hand-surveyed corridor. But every crossing
    already makes a real Location Retrieval call and a real reachability
    check, so the network has been describing its own blind spots this
    whole time — see coverage.py. These are proposals for a human to
    confirm, never live geofences: arming one on a guess would page real
    contacts about a corridor nobody has checked."""
    return await coverage.candidates(session)


@router.post("/zones/candidates/{cell}/promote")
async def promote_candidate(
    cell: str, body: PromoteCandidateIn, session: AsyncSession = Depends(get_session),
):
    """Turn a candidate into a real zone. Deliberately a separate, explicit
    action — see zone_candidates above on why this is never automatic."""
    from ..coverage import cell_centre

    try:
        lat, lon = cell_centre(cell)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, f"malformed cell key {cell!r}") from exc

    existing = await session.get(Zone, body.zone_id)
    if existing is not None:
        raise HTTPException(409, f"zone {body.zone_id} already exists")

    # A candidate is one grid cell, which describes where the hole is but
    # not how long the corridor through it runs. The gates are placed on
    # the cell's own extent and the operator is expected to correct them —
    # so the label says plainly that this came from observation, not survey.
    half = coverage.GRID_DEG / 2
    session.add(Zone(
        zone_id=body.zone_id,
        label=body.label or f"Auto-discovered corridor near {lat:.3f}, {lon:.3f} (unsurveyed)",
        entry_lat=lat - half, entry_lon=lon,
        exit_lat=lat + half, exit_lon=lon,
        gate_radius_m=body.gate_radius_m,
        corridor_km=body.corridor_km,
        nominal_crossing_min=body.nominal_crossing_min,
    ))
    await session.commit()
    return {"zone_id": body.zone_id, "promoted_from": cell}


@router.post("/hooks/geofence")
async def geofence_webhook(body: dict, session: AsyncSession = Depends(get_session)):
    """Production path for a real CAMARA area-entered/area-left event.
    During the Prototype Phase the sandbox can't move a device, so this is
    exercised via /demo/simulate-gate-event instead — the same state
    machine code runs either way."""
    traveller_id = body.get("traveller_id")
    zone_id = body.get("zone_id")
    gate = body.get("gate")
    if not (traveller_id and zone_id and gate):
        raise HTTPException(400, "expected {traveller_id, zone_id, gate}")
    try:
        await sm.simulate_gate_event(
            session, nac_client, whatsapp_client,
            traveller_id=traveller_id, zone_id=zone_id, gate=gate,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {}


@router.post("/hooks/reachability")
async def reachability_webhook(body: dict, session: AsyncSession = Depends(get_session)):
    """Sink for the CAMARA Device Reachability Status subscription created
    in state_machine.arm_zone — the notification the operator pushes when
    a line's reachability changes, which is the core safety signal
    (docs/SignalGuard_Technical_Feasibility.pdf §2).

    Nokia's sandbox can't take a device in or out of coverage, so during
    the Prototype Phase this is exercised via /demo/simulate-reachability;
    both land in the same handler, which records the pushed state and runs
    the same state machine either way."""
    traveller_id = body.get("traveller_id")
    reachable = body.get("reachable")
    if traveller_id is None or reachable is None:
        raise HTTPException(400, "expected {traveller_id, reachable}")
    await sm.simulate_reachability(session, whatsapp_client, traveller_id=traveller_id, reachable=bool(reachable))
    return {}


# -- dashboard reads ---------------------------------------------------------

def _trips_with_corridor_km(where, order_by):
    """Trips plus their zone's corridor length, in one statement.

    Serializing a trip needs corridor_km to size its uncertainty cone (see
    uncertainty.py). Looking that up per trip would be N queries; looking
    it up as a separate zone-registry read was one extra round trip on
    every poll from every dashboard, for a table that changes when an
    operator promotes a candidate — i.e. almost never. An outer join
    carries it on the row the trip is already being read from, and stays
    correct the moment the registry does change. Outer, not inner, so a
    trip whose zone was removed still appears in the queue rather than
    silently vanishing from the one screen that is supposed to show every
    trip somebody is still watching."""
    return (
        select(Trip, Zone.corridor_km)
        .outerjoin(Zone, Zone.zone_id == Trip.zone_id)
        .where(where)
        .options(joinedload(Trip.traveller))
        .order_by(order_by)
    )


@router.get("/dashboard/trips")
async def dashboard_trips(session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    result = await session.execute(
        _trips_with_corridor_km(Trip.state.in_(DASHBOARD_STATES), Trip.created_at.desc())
    )
    return json_ok([
        dashboard_trip_to_dict(trip, corridor_km=corridor_km)
        for trip, corridor_km in result.all()
    ])


@router.get("/dashboard/history")
async def dashboard_history(limit: int = 50, session: AsyncSession = Depends(get_session)):
    """Closed trips, most recent first.

    An emergency centre that forgets every trip the moment it is resolved
    cannot answer the questions it exists to answer — how often this
    corridor runs long, whether last week's escalation was this same
    traveller, what the crossing actually took. The live queue stays clean
    (resolved trips leave it immediately, per the User Flow doc); this is
    where they go."""
    limit = max(1, min(500, limit))
    result = await session.execute(
        _trips_with_corridor_km(
            Trip.state.in_(HISTORY_STATES), Trip.resolved_at.desc()
        ).limit(limit)
    )
    return json_ok([
        dashboard_trip_to_dict(trip, corridor_km=corridor_km)
        for trip, corridor_km in result.all()
    ])


@router.get("/dashboard/zones/{zone_id}/stats")
async def dashboard_zone_stats(zone_id: str, session: AsyncSession = Depends(get_session)):
    """What this corridor has actually done, by hour of day.

    The registry's nominal_crossing_min is one hand-set constant for the
    whole day. This is the measured alternative — and showing which hours
    have enough samples to be in use is the point, not a footnote: a
    dispatcher should be able to see when the system is planning against
    evidence and when it is still planning against a guess."""
    return await corridor_stats.zone_summary(session, zone_id=zone_id)


@router.get("/dashboard/api-activity")
async def dashboard_api_activity(
    limit: int = 40, hours: int = 24, session: AsyncSession = Depends(get_session)
):
    """The CAMARA call layer this console's queue is actually made of.

    Not a demo aid — `/demo/api-log` is that, and it is mounted only in
    demo mode. This is the operational view: every trip on this screen
    exists because a Geofencing notification arrived, and every number in
    its detail panel came out of Congestion Insights, Location Retrieval or
    a reachability check. When one of those five APIs starts failing the
    queue does not turn red, it goes quiet — which on a screen whose whole
    job is telling somebody whether a corridor is calm looks exactly like a
    calm corridor. See api_activity.py."""
    return json_ok({
        **await api_activity.summary(session, hours=max(1, min(168, hours))),
        "calls": await api_activity.recent(session, limit=limit, hours=hours),
    })


@router.get("/dashboard/trips/{trip_id}")
async def dashboard_trip_detail(trip_id: str, session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    result = await session.execute(
        select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.traveller))
    )
    trip = result.scalar_one_or_none()
    if trip is None:
        raise HTTPException(404, "unknown trip")
    zone = await session.get(Zone, trip.zone_id)
    detail = dashboard_trip_to_dict(trip, corridor_km=zone.corridor_km if zone else None)
    # The exact CAMARA requests behind the figures above. A dispatcher
    # reading "risk 0.62, congestion light" is being asked to trust two
    # numbers whose provenance is a third-party API call; this is that
    # call, with its status and latency.
    detail["api_calls"] = await api_activity.recent(session, limit=40, trip_id=trip.id)
    return detail


@router.post("/dashboard/trips/{trip_id}/resolve")
async def dashboard_resolve_trip(trip_id: str, session: AsyncSession = Depends(get_session)):
    try:
        await sm.contact_reply(session, trip_id=trip_id, reply="safe")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    result = await session.execute(
        select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.traveller))
    )
    trip = result.scalar_one()
    zone = await session.get(Zone, trip.zone_id)
    return dashboard_trip_to_dict(trip, corridor_km=zone.corridor_km if zone else None)
