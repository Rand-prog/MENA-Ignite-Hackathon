"""Convoy mode — ask the person 20 km ahead before you wake the person
400 km away.

The escalation ladder as designed goes device-dark -> personal contact ->
emergency centre. The personal contact is usually at home in another city:
they can confirm nothing, they can only worry and phone around. Meanwhile
there is very often somebody far better placed — another SignalGuard user
who entered the same corridor within the same hour and has already come
out the far side. That person drove the road minutes ago. They know whether
it is blocked, whether there was an accident, whether traffic is crawling.

So this inserts a rung *below* Tier 1: when a trip goes overdue and a
convoy peer has already exited safely, the peer is asked first. If the peer
answers "saw them, traffic is bad", the trip stands down and the contact is
never woken. If the peer doesn't answer inside the Tier 1 grace, the ladder
proceeds exactly as before.

The invariant this must not break: escalation is timer-driven (docs/
SignalGuard_Technical_Feasibility.pdf §3, §6). A convoy peer can *resolve*
a trip, the same way a contact reply can. A convoy peer can never delay
one — the tier1 deadline is not extended by the peer's existence, so the
worst case of a peer who never answers is identical to today's behaviour.

Membership is derived, not declared. Two travellers who arm the same zone
within `convoy_window_min` of each other are travelling together for
practical purposes, and asking them to pair up in an app before setting off
is exactly the kind of setup step this product exists to avoid.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .clock import clock
from .config import settings
from .models import Trip, TripState

# States a peer must be in to be worth asking: they have to have actually
# come out the far side. Someone still inside the corridor knows no more
# than the overdue traveller does, and asking them would just be a second
# alarm about the same dead zone.
PEER_USABLE_STATES = {TripState.EXITED, TripState.RESOLVED}


def convoy_key(zone_id: str, entered_at) -> str:
    """Bucket an entry time into a convoy window. Deriving the key from
    the entry time (rather than storing group membership) means a convoy
    forms and dissolves on its own with no state to keep in sync."""
    bucket = int(entered_at.timestamp() // (settings.convoy_window_min * 60))
    return f"{zone_id}:{bucket}"


async def peers_for(session: AsyncSession, trip: Trip) -> list[Trip]:
    """Other trips in the same convoy, most recently entered first.

    Deliberately queries by convoy_id rather than recomputing the bucket,
    so a trip that straddles a bucket boundary keeps the convoy it was
    assigned at entry instead of silently changing groups mid-crossing."""
    if not trip.convoy_id:
        return []
    result = await session.execute(
        select(Trip)
        .where(Trip.convoy_id == trip.convoy_id, Trip.id != trip.id)
        .order_by(Trip.entered_at.desc())
    )
    return list(result.scalars().all())


async def usable_peer(session: AsyncSession, trip: Trip) -> Trip | None:
    """The best peer to ask about this trip, or None.

    "Best" is the most recent one that has already exited — they were on
    that road most recently, so their answer is the freshest."""
    for peer in await peers_for(session, trip):
        if peer.state in PEER_USABLE_STATES:
            return peer
    return None


async def assign(session: AsyncSession, trip: Trip) -> str | None:
    """Give a freshly-created trip its convoy id, if anyone else is in the
    same corridor at the same time. Returns the id, or None when this
    traveller is alone on the road (the common case)."""
    if trip.entered_at is None:
        return None
    key = convoy_key(trip.zone_id, trip.entered_at)
    window_start = trip.entered_at - timedelta(minutes=settings.convoy_window_min)
    result = await session.execute(
        select(Trip).where(
            Trip.zone_id == trip.zone_id,
            Trip.id != trip.id,
            Trip.entered_at.is_not(None),
            Trip.entered_at >= window_start,
        )
    )
    others = list(result.scalars().all())
    if not others:
        # Still tag it: a *later* trip entering behind this one needs to
        # find a key here to join. A convoy of one is just a key nobody
        # else has used yet.
        trip.convoy_id = key
        return key

    # Join whatever key the earliest neighbour already carries, so a
    # trickle of travellers entering a few minutes apart forms one convoy
    # instead of a chain of overlapping pairs.
    for other in sorted(others, key=lambda t: t.entered_at):
        if other.convoy_id:
            trip.convoy_id = other.convoy_id
            return other.convoy_id
    trip.convoy_id = key
    return key


def peer_summary(peer: Trip) -> dict:
    """What the dashboard and the peer-ask message are allowed to know
    about a convoy peer.

    Nothing identifying beyond the name the peer already registered, and
    no position — this is "somebody else made it through 12 minutes ago",
    not a way to track another user. See docs/SignalGuard_Security_Privacy
    §2."""
    exited_min_ago = None
    if peer.resolved_at is not None:
        exited_min_ago = max(0, round((clock.now() - peer.resolved_at).total_seconds() / 60))
    return {
        "trip_id": peer.trip_id if hasattr(peer, "trip_id") else peer.id,
        "state": peer.state.value if hasattr(peer.state, "value") else peer.state,
        "exited_min_ago": exited_min_ago,
        "crossing_min": peer.actual_crossing_min,
    }
