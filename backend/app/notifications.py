"""WhatsApp copy for the three moments a contact hears from SignalGuard,
per the user's explicit choice to message on every crossing (entry,
overdue, and safe exit) — a deliberate deviation from the docs' silent
happy-path design (see docs/SignalGuard_Security_Privacy §7: "they hear
from SignalGuard because a crossing went overdue, and not otherwise").
Flagged to the user at the time; this is the chosen behaviour now.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Traveller, Trip, Zone
from .whatsapp_client import WhatsAppClient


async def _contacts_for(session: AsyncSession, traveller_id: str) -> tuple[Traveller | None, list]:
    result = await session.execute(
        select(Traveller)
        .where(Traveller.id == traveller_id)
        .options(selectinload(Traveller.contacts))
    )
    traveller = result.scalar_one_or_none()
    return traveller, list(traveller.contacts) if traveller else []


async def _broadcast(wa: WhatsAppClient, contacts: list, body: str) -> None:
    for contact in contacts:
        await wa.send(to_msisdn=contact.msisdn, body=body)


async def notify_entry(session: AsyncSession, wa: WhatsAppClient, trip: Trip, zone: Zone) -> None:
    traveller, contacts = await _contacts_for(session, trip.traveller_id)
    if not traveller or not contacts:
        return
    window = trip.monitoring_window_min or zone.nominal_crossing_min
    body = (
        f"SignalGuard: {traveller.name} just entered a low-coverage zone on "
        f"{zone.label}. Expected to reconnect within ~{window} min. "
        f"No action needed unless you don't hear from us again by then."
    )
    await _broadcast(wa, contacts, body)


async def notify_overdue(session: AsyncSession, wa: WhatsAppClient, trip: Trip, zone: Zone) -> None:
    traveller, contacts = await _contacts_for(session, trip.traveller_id)
    if not traveller or not contacts:
        return
    entry_time = trip.entered_at.strftime("%H:%M") if trip.entered_at else "—"
    body = (
        f"SignalGuard: {traveller.name} entered a low-coverage zone on {zone.label} "
        f"at {entry_time} and hasn't reconnected yet. Last known location: "
        f"{trip.last_known_location or 'unavailable'}. Battery was {trip.battery_band or 'unknown'} "
        f"at entry, {trip.congestion_tier or 'unknown'} traffic. "
        f"Reply here or on the dashboard if you've heard from them."
    )
    await _broadcast(wa, contacts, body)


async def notify_safe_exit(session: AsyncSession, wa: WhatsAppClient, trip: Trip, zone: Zone) -> None:
    traveller, contacts = await _contacts_for(session, trip.traveller_id)
    if not traveller or not contacts:
        return
    body = (
        f"SignalGuard: {traveller.name} made it through {zone.label} safely "
        f"and reconnected. Trip complete."
    )
    await _broadcast(wa, contacts, body)
