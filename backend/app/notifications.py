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


async def notify_test(
    session: AsyncSession, wa: WhatsAppClient, traveller_id: str,
) -> dict:
    """A one-off "this is what an alert looks like" message, sent from
    onboarding.

    The reason this exists: a wrong digit in a contact's number makes the
    entire escalation ladder terminate in a void, silently, and nobody
    finds out until the one moment it matters. Nothing else in the system
    ever verifies that number. A traveller finishes setup and then —
    correctly — never opens the app again, with no evidence any of it
    works.

    Returns the ACTUAL delivery state per contact rather than a blanket
    "sent". With Twilio unconfigured every send is a logged no-op (see
    whatsapp_client.py), and reporting that as success would be precisely
    the false reassurance this endpoint is supposed to remove.
    """
    traveller, contacts = await _contacts_for(session, traveller_id)
    if not traveller or not contacts:
        return {"delivery": "no_contacts", "contacts": []}

    body = (
        f"SignalGuard test message. {traveller.name} added you as their "
        f"emergency contact. If they ever go quiet crossing a dead zone, "
        f"this is where you'll hear about it. Nothing is wrong right now — "
        f"no reply needed."
    )
    results = []
    for contact in contacts:
        outcome = await wa.send(to_msisdn=contact.msisdn, body=body)
        results.append({
            "name": contact.name,
            "msisdn": contact.msisdn,
            "sent": bool(outcome.get("sent")),
            "reason": outcome.get("reason"),
        })

    return {
        "delivery": "sent" if wa.enabled else "not_configured",
        "contacts": results,
    }
