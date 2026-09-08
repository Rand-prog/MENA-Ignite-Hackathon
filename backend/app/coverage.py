"""Auto-discovered dead zones.

The zone registry is currently one hardcoded Jordanian highway, surveyed by
hand. That does not scale, and more to the point it is unnecessary: every
crossing this system runs already makes a real Location Retrieval call and
a real Device Reachability check. The network is continuously telling
SignalGuard where its own coverage holes are, and until now that was
thrown away after the agent read it once.

This module keeps it. Each (location, reachable?) reading lands as a
CoverageObservation; cells with a repeated, multi-reading pattern of
unreachability are offered as candidate zones for a human to confirm and
promote into the registry.

PRIVACY — this is the part that matters, see docs/SignalGuard_Security_
Privacy §2. An observation store keyed by person would be a location
history of that person, which is emphatically not what this product is
allowed to build. So:

  * No traveller id, no trip id, is written on an observation. Rows are
    not joinable back to a person.
  * Positions are snapped to a coarse grid cell (~5.5 km on a side) before
    they are used for anything; the raw lat/lon is kept only so a promoted
    candidate can be given a sensible centre.
  * A candidate needs observations from several separate readings before it
    surfaces at all, so one person's route never becomes a zone by itself.

Candidates are proposals, never live geofences. Promotion into the zone
registry is a deliberate human action (POST /zones/candidates/{cell}/promote)
because arming a geofence on a guess would page contacts about a corridor
nobody has checked.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import CoverageObservation, Zone

logger = logging.getLogger("signalguard.coverage")

# ~0.05 degrees ≈ 5.5 km of latitude. Coarse on purpose: fine enough to
# distinguish one highway corridor from the next, far too coarse to
# distinguish a person's stops within one.
GRID_DEG = 0.05


def cell_key(lat: float, lon: float) -> str:
    """Snap a position to its grid cell. The key is the cell's south-west
    corner, formatted so it sorts sensibly and round-trips exactly."""
    return f"{int(lat // GRID_DEG)}:{int(lon // GRID_DEG)}"


def cell_centre(cell: str) -> tuple[float, float]:
    """Centre of a grid cell, from its key."""
    ilat, ilon = cell.split(":")
    return (
        (int(ilat) + 0.5) * GRID_DEG,
        (int(ilon) + 0.5) * GRID_DEG,
    )


async def record_observation(
    session: AsyncSession,
    *,
    lat: float | None,
    lon: float | None,
    reachable: bool,
    congestion_level: str | None,
    observed_at: datetime,
) -> None:
    """Store one coverage reading. Silently does nothing without a
    position — a reachability check with no location attached says
    something about a device and nothing about a place, and this table is
    only ever about places."""
    if lat is None or lon is None:
        return
    session.add(
        CoverageObservation(
            lat=lat,
            lon=lon,
            cell=cell_key(lat, lon),
            reachable=reachable,
            congestion_level=congestion_level,
            observed_at=observed_at,
        )
    )


async def candidates(session: AsyncSession) -> list[dict]:
    """Grid cells that look like coverage holes and are not already a
    registered zone.

    One GROUP BY, not a scan-and-bucket in Python — the cell key is
    precomputed on write precisely so this stays a single indexed
    aggregate as the table grows.

    Both thresholds sit in the HAVING clause rather than in a Python
    filter under the loop. A grid cell is ~5.5 km on a side, so a system
    running on real traffic accumulates one for every stretch of road
    anybody has ever crossed — and all but a handful of them are ordinary
    covered road that fails the very first test. Returning every one of
    those to Python just to drop it made this endpoint's cost scale with
    how much of the map has been driven rather than with how many coverage
    holes were actually found.
    """
    dark_count = func.sum(
        # SQLite has no boolean sum; CASE keeps this portable.
        case((CoverageObservation.reachable.is_(False), 1), else_=0)
    )
    total_count = func.count()
    result = await session.execute(
        select(
            CoverageObservation.cell,
            total_count.label("total"),
            dark_count.label("dark"),
            func.avg(CoverageObservation.lat).label("lat"),
            func.avg(CoverageObservation.lon).label("lon"),
            func.max(CoverageObservation.observed_at).label("last_seen"),
        )
        .group_by(CoverageObservation.cell)
        .having(dark_count >= settings.coverage_candidate_min_obs)
        .having(dark_count >= settings.coverage_candidate_min_ratio * total_count)
    )
    rows = result.all()

    known = await _known_zone_cells(session)

    out = []
    for row in rows:
        total = int(row.total or 0)
        dark = int(row.dark or 0)
        ratio = dark / total if total else 0.0
        if row.cell in known:
            continue
        c_lat, c_lon = cell_centre(row.cell)
        out.append({
            "cell": row.cell,
            "observations": total,
            "dark_observations": dark,
            "dark_ratio": round(ratio, 2),
            "centre": {"lat": round(c_lat, 4), "lon": round(c_lon, 4)},
            # Mean of the actual readings, which is a better centre than the
            # cell's geometric middle when a corridor clips a cell's edge.
            "observed_centre": {
                "lat": round(float(row.lat), 4),
                "lon": round(float(row.lon), 4),
            },
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        })
    out.sort(key=lambda c: (-c["dark_observations"], c["cell"]))
    return out


async def _known_zone_cells(session: AsyncSession) -> set[str]:
    """Cells already covered by a registered zone's gates — a candidate
    that just rediscovers Highway 15 is noise, not a finding."""
    # Four floats per zone, not whole ORM Zone rows — all this needs is the
    # two gate positions, to walk the line between them.
    result = await session.execute(
        select(Zone.entry_lat, Zone.entry_lon, Zone.exit_lat, Zone.exit_lon)
    )
    cells: set[str] = set()
    for entry_lat, entry_lon, exit_lat, exit_lon in result.all():
        cells.add(cell_key(entry_lat, entry_lon))
        cells.add(cell_key(exit_lat, exit_lon))
        # Also mask the cells the corridor line passes through, so a long
        # corridor doesn't keep proposing its own middle as a new zone.
        for step in range(1, 20):
            t = step / 20
            cells.add(cell_key(
                entry_lat + (exit_lat - entry_lat) * t,
                entry_lon + (exit_lon - entry_lon) * t,
            ))
    return cells
