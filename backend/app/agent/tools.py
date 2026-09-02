"""Fixed-parameter tool wrappers — the agent's only reach into the network.

Each wraps exactly one CAMARA call or one internal action. The agent selects
a tool and supplies an identifier; it never constructs HTTP requests, picks
an endpoint, or widens a query. That bound is enforced here, in the tool
layer — not by prompting the model to behave.
See docs/SignalGuard_Technical_Feasibility.pdf §5.3.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Trip, Zone
from ..nokia_client import NokiaCallError, NokiaClient
from ..zones import ZoneSeed


@dataclass
class AgentContext:
    """Everything a tool call needs to resolve trip_id/zone_id into a real
    request, without the agent itself ever touching these objects."""

    session: AsyncSession
    nac: NokiaClient
    trip: Trip
    zone: Zone
    nac_device: str = settings.nac_device


def zone_seed_from_row(zone: Zone) -> ZoneSeed:
    return ZoneSeed(
        zone_id=zone.zone_id, label=zone.label,
        entry_lat=zone.entry_lat, entry_lon=zone.entry_lon,
        exit_lat=zone.exit_lat, exit_lon=zone.exit_lon,
        gate_radius_m=zone.gate_radius_m, corridor_km=zone.corridor_km,
        nominal_crossing_min=zone.nominal_crossing_min,
    )


async def get_zone_profile(ctx: AgentContext, zone_id: str) -> dict:
    """Internal action — zone registry lookup, no network call."""
    z = ctx.zone
    return {
        "zone_id": z.zone_id,
        "label": z.label,
        "corridor_km": z.corridor_km,
        "nominal_crossing_min": z.nominal_crossing_min,
    }


async def get_congestion_insights(ctx: AgentContext, zone_id: str) -> dict:
    """CAMARA Congestion Insights — once, at the entry gate. The sandbox
    returns a list of recent time-bucketed readings, most recent first
    (see docs/nokia-api-catalog.md's sample doesn't show this — confirmed
    against a live sandbox response); we use the most recent one."""
    try:
        result = await ctx.nac.query_congestion(
            device_phone=ctx.nac_device,
            webhook_url=f"{settings.signalguard_webhook_base}/hooks/congestion",
        )
    except NokiaCallError:
        return {"tier": "unknown", "error": True}

    reading = result[0] if isinstance(result, list) and result else result
    if not isinstance(reading, dict):
        return {"tier": "unknown", "error": True, "raw": result}

    raw_level = reading.get("congestionLevel") or reading.get("tier") or "light"
    # CAMARA's own vocabulary is Low/Medium/High; the fallback risk model
    # (and the decision record) speak light/moderate/heavy — translate once,
    # here, rather than teaching the risk model CAMARA's casing.
    tier = {"low": "light", "medium": "moderate", "high": "heavy"}.get(
        raw_level.lower() if isinstance(raw_level, str) else "", "light"
    )
    return {"tier": tier, "raw": result}


async def get_location(ctx: AgentContext, trip_id: str) -> dict:
    """CAMARA Location Retrieval — one last-known-position snapshot."""
    try:
        result = await ctx.nac.retrieve_location(device_phone=ctx.nac_device)
    except NokiaCallError:
        return {"error": True}
    area = result.get("area") or {}
    center = area.get("center") or {}
    lat, lon = center.get("latitude"), center.get("longitude")
    return {
        "lat": lat, "lon": lon,
        "label": f"{lat}, {lon}" if lat is not None else "unavailable",
        "raw": result,
    }


async def check_device_reachability(ctx: AgentContext, trip_id: str) -> dict:
    """CAMARA Device Reachability Status — one-shot check. Ongoing
    verification during the crossing uses the subscription form instead."""
    try:
        result = await ctx.nac.retrieve_reachability(device_phone=ctx.nac_device)
    except NokiaCallError:
        return {"status": "unknown", "error": True}
    return {"status": result.get("connectivityStatus", "unknown"), "raw": result}


async def request_qod_session(ctx: AgentContext, trip_id: str) -> dict:
    """CAMARA Quality on Demand — only called when the agent judges it
    warranted. Not every crossing spends one."""
    try:
        result = await ctx.nac.create_qod_session(device_phone=ctx.nac_device)
    except NokiaCallError:
        return {"requested": False, "error": True}
    return {"requested": True, "session_id": result.get("sessionId"), "raw": result}


async def notify_contact(ctx: AgentContext, trip_id: str, tier: str) -> dict:
    """Internal action — Tier 1/2 notification. No SMS gateway is wired up
    for the prototype; the message is composed and recorded on the trip so
    the demo's second-handset step and the dashboard both have something
    real to show. Wiring an actual SMS provider is out of scope here."""
    notes = list(ctx.trip.notifications or [])
    notes.append(tier)
    ctx.trip.notifications = notes
    return {"tier": tier, "recorded": True}


async def escalate_to_dashboard(ctx: AgentContext, trip_id: str) -> dict:
    """Internal action — Tier 2. The trip row *is* the dashboard's data
    source; escalating just means the state transition has already made
    this trip visible as 'action needed'."""
    notes = list(ctx.trip.notifications or [])
    if "tier2" not in notes:
        notes.append("tier2")
    ctx.trip.notifications = notes
    return {"escalated": True}


async def manage_geofence_subscription(
    ctx: AgentContext, *, action: str, gate: str = "entry",
    sink: str | None = None,
) -> dict:
    """Internal action — create/renew/delete the CAMARA Geofencing
    Subscription for this trip's zone. Used at arm-time and by the renewal
    scheduler, not during a normal crossing."""
    z = ctx.zone
    lat, lon = (z.entry_lat, z.entry_lon) if gate == "entry" else (z.exit_lat, z.exit_lon)
    if action == "create":
        result = await ctx.nac.create_geofence_subscription(
            device_phone=ctx.nac_device,
            sink=sink or f"{settings.signalguard_webhook_base}/hooks/geofence",
            lat=lat, lon=lon, radius_m=z.gate_radius_m,
        )
        return {"id": result.get("id"), "raw": result}
    if action == "delete" and sink:
        await ctx.nac.delete_geofence_subscription(sink)
        return {"deleted": True}
    return {"noop": True}
