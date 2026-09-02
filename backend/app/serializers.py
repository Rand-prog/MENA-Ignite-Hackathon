from __future__ import annotations

from .clock import clock
from .models import Trip


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
    }


def dashboard_trip_to_dict(trip: Trip) -> dict:
    """Richer shape for the emergency-centre dashboard — needs traveller
    identity and elapsed time, which the product/demo contract above never
    required. Requires trip.traveller to already be eager-loaded (see
    routers/real.py's selectinload) — touching a lazy relationship inside
    an async session without it raises MissingGreenlet."""
    base = trip_to_dict(trip)
    elapsed_min = None
    if trip.entered_at is not None:
        elapsed_min = round((clock.now() - trip.entered_at).total_seconds() / 60, 1)
    base.update({
        "zone_id": trip.zone_id,
        "traveller_id": trip.traveller_id,
        "traveller_name": trip.traveller.name if trip.traveller else None,
        "traveller_msisdn": trip.traveller.msisdn if trip.traveller else None,
        "entered_at": trip.entered_at.isoformat() if trip.entered_at else None,
        "elapsed_min": elapsed_min,
    })
    return base
