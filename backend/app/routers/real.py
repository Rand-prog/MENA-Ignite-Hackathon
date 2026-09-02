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
from sqlalchemy.orm import selectinload

from .. import state_machine as sm
from ..db import get_session
from ..models import Traveller, Trip, TripState, Zone
from ..nac_singleton import nac_client
from ..schemas import TravellerIn
from ..serializers import dashboard_trip_to_dict, trip_to_dict
from ..whatsapp_singleton import whatsapp_client

# The live dashboard queue — trips that are still someone's job to watch.
# EXITED/RESOLVED trips clear from the queue automatically, per
# docs/SignalGuard_User_Flow's Emergency Center Flow §4.
DASHBOARD_STATES = {
    TripState.BUFFER, TripState.ACTIVE, TripState.OVERDUE,
    TripState.TIER1_ALERTED, TripState.TIER2_ESCALATED,
}

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
        raise HTTPException(409, "a traveller with this phone number is already registered")
    return {"traveller_id": traveller.id, "auth_token": traveller.auth_token}


@router.post("/travellers/me/battery")
async def report_battery(
    level: int, traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    await sm.set_battery(session, traveller_id=traveller.id, level=level)
    return {"ok": True}


@router.get("/travellers/me/trip")
async def my_trip(
    traveller: Traveller = Depends(require_traveller),
    session: AsyncSession = Depends(get_session),
):
    await sm.tick(session, whatsapp_client)
    trip = await sm.active_trip_for(session, traveller.id)
    if trip is None:
        return None
    return trip_to_dict(trip)


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
    traveller_id = body.get("traveller_id")
    reachable = body.get("reachable")
    if traveller_id is None or reachable is None:
        raise HTTPException(400, "expected {traveller_id, reachable}")
    await sm.simulate_reachability(session, whatsapp_client, traveller_id=traveller_id, reachable=bool(reachable))
    return {}


# -- dashboard reads ---------------------------------------------------------

@router.get("/dashboard/trips")
async def dashboard_trips(session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    result = await session.execute(
        select(Trip)
        .where(Trip.state.in_(DASHBOARD_STATES))
        .options(selectinload(Trip.traveller))
        .order_by(Trip.created_at.desc())
    )
    return [dashboard_trip_to_dict(t) for t in result.scalars().all()]


@router.get("/dashboard/trips/{trip_id}")
async def dashboard_trip_detail(trip_id: str, session: AsyncSession = Depends(get_session)):
    await sm.tick(session, whatsapp_client)
    result = await session.execute(
        select(Trip).where(Trip.id == trip_id).options(selectinload(Trip.traveller))
    )
    trip = result.scalar_one_or_none()
    if trip is None:
        raise HTTPException(404, "unknown trip")
    return dashboard_trip_to_dict(trip)


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
    return dashboard_trip_to_dict(trip)
