"""Zone registry seed. One zone to start, per the build prompt.

Coordinates match scripts/run_demo.py's ZONES dict exactly — the demo
conductor and the backend must agree on the zone or /demo/zones/{id}/arm
has nothing to arm. Real surveyed coordinates replace these placeholders
before the live dry run; see the module docstring in run_demo.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoneSeed:
    zone_id: str
    label: str
    entry_lat: float
    entry_lon: float
    exit_lat: float
    exit_lon: float
    gate_radius_m: int
    corridor_km: int
    nominal_crossing_min: int


ZONE_SEEDS: list[ZoneSeed] = [
    ZoneSeed(
        zone_id="JO-H15-MUDAWWARA",
        label="Highway 15 — Desert Highway, Al Mudawwara approach",
        entry_lat=29.8320,
        entry_lon=35.9910,
        exit_lat=29.3350,
        exit_lon=36.0240,
        gate_radius_m=8000,
        corridor_km=104,
        nominal_crossing_min=75,
    )
]
