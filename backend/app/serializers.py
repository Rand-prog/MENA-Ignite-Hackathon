from __future__ import annotations

from .clock import clock
from .config import settings
from .models import Trip
from .uncertainty import estimate


def trip_to_dict(trip: Trip) -> dict:
    """The demo/product contract shape — run_demo.py depends on these exact
    keys existing. Extra keys are additive and safe; don't remove any."""
    return {
        "trip_id": trip.id,
        "state": trip.state.value if hasattr(trip.state, "value") else trip.state,
        "risk": trip.risk,
        "battery_at_entry": trip.battery_at_entry,
        "battery_band": trip.battery_band,
        "congestion_tier": trip.congestion_tier,
        "entry_point": trip.entry_point,
        "last_known_location": trip.last_known_location,
        "predicted_crossing_min": trip.predicted_crossing_min,
        "monitoring_window_min": trip.monitoring_window_min,
        "decision_record": trip.decision_record,
        "notifications": list(trip.notifications or []),
        "escalation_record": trip.escalation_record,
        # -- additive ---------------------------------------------------------
        # The app needs the actual wall-clock moment a contact would be
        # texted, not just a duration. "We'd text Omar at 15:40" is the one
        # sentence that makes the whole product legible to a traveller;
        # "115 min window" is a number they then have to do arithmetic on
        # while driving.
        "window_deadline": _iso(trip.window_deadline),
        "tier0_deadline": _iso(trip.tier0_deadline),
        "tier1_deadline": _iso(trip.tier1_deadline),
        "entered_at": _iso(trip.entered_at),
        "server_now": _iso(clock.now()),
        "planned_stop_min": trip.planned_stop_min or 0,
        "tier0_grace_sec": settings.tier0_grace_sec,
        "exit_signal": trip.exit_signal,
        "qod_warranted": bool(trip.qod_warranted),
        "qod_edge": trip.qod_edge,
        "actual_crossing_min": trip.actual_crossing_min,
    }


def dashboard_trip_to_dict(trip: Trip, *, corridor_km: int | None = None) -> dict:
    """Richer shape for the emergency-centre dashboard — needs traveller
    identity, elapsed time, and the position *range* the network can
    actually defend. Requires trip.traveller to already be eager-loaded
    (see routers/real.py's selectinload) — touching a lazy relationship
    inside an async session without it raises MissingGreenlet."""
    base = trip_to_dict(trip)
    elapsed_min = None
    if trip.entered_at is not None:
        elapsed_min = round((clock.now() - trip.entered_at).total_seconds() / 60, 1)
    base.update({
        "zone_id": trip.zone_id,
        "traveller_id": trip.traveller_id,
        "traveller_name": trip.traveller.name if trip.traveller else None,
        "traveller_msisdn": trip.traveller.msisdn if trip.traveller else None,
        "entered_at": _iso(trip.entered_at),
        "elapsed_min": elapsed_min,
        "convoy_id": trip.convoy_id,
    })

    # A range, not a point. The dashboard used to draw a dot at an
    # interpolated position that was visually identical to a real GPS fix,
    # with the caveat relegated to small print under the map. A dispatcher
    # glancing at that during an escalation reads "we know where they
    # are" — and a Tier 2 handoff needs a search *area*, which a point is
    # not. See uncertainty.py.
    if corridor_km:
        est = estimate(
            elapsed_min=elapsed_min,
            corridor_km=corridor_km,
            predicted_crossing_min=trip.predicted_crossing_min,
        )
        base["position_estimate"] = est.to_dict() if est else None
    else:
        base["position_estimate"] = None
    return base


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None
