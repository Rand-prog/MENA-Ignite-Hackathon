"""Learned corridor times.

The zone registry ships one hand-set `nominal_crossing_min` per zone (75
for Highway 15). That number is the same at 3am on an empty road as it is
at 5pm behind a truck convoy, and every monitoring window in the system is
derived from it — so the whole risk model rests on a constant somebody
typed once.

This module replaces that constant with an observation as soon as there is
enough evidence to justify one. Each completed crossing writes a
CrossingHistory row (see state_machine._record_crossing); this reads them
back per (zone, hour-of-day) and returns a p50 to plan against and a p90 to
size the buffer with.

Two guards keep this honest:

  * Below `corridor_stats_min_samples` clean crossings, it returns None and
    the caller falls back to the registry nominal. A p50 over two samples is
    noise wearing a statistic's clothes.
  * Only `clean=True` rows count — a crossing that went overdue is a real
    duration, but it is evidence about an incident, not about how long the
    road normally takes. Letting those in would ratchet the baseline upward
    after every escalation, which is exactly backwards: the system would
    become *less* likely to alarm the more often it had to.

The hour bucket is deliberately coarse (an exact hour, UTC) rather than a
smooth time-of-day model. There is no dataset here yet to justify anything
cleverer, and a fitted curve over nine crossings would be a decoration.

Both read functions below also window to `corridor_stats_window_days` —
`stats_for` runs on every agent run (i.e. every crossing) and
`zone_summary` on every dashboard bootstrap, and without a window each
call reads more of `crossing_history` than the last, forever, even though
a crossing from a year ago says nothing about how the corridor behaves
today. Windowing is pushed into the WHERE clause rather than filtered in
Python for the obvious reason (let SQLite's index do it), and both
functions select only the columns they actually use rather than full ORM
rows — the percentile math genuinely needs the raw per-crossing durations,
which is why this isn't a single SQL aggregate query the way a plain
COUNT/AVG would be; nearest-rank percentiles over single-digit sample
counts aren't something SQLite has a builtin for anyway (see
`_percentile`'s own docstring on why an interpolated one would be worse).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .clock import clock
from .config import settings
from .models import CrossingHistory


def _window_cutoff() -> datetime:
    return clock.now() - timedelta(days=settings.corridor_stats_window_days)


@dataclass(frozen=True)
class CorridorStats:
    """What history says about this corridor at this hour."""

    zone_id: str
    hour_of_day: int
    samples: int
    p50_min: int
    p90_min: int

    def summary(self) -> str:
        """One line for the agent prompt and the decision record."""
        return (
            f"{self.samples} past crossings at this hour: "
            f"typical {self.p50_min} min, slowest {self.p90_min} min"
        )


def _percentile(sorted_vals: list[int], q: float) -> int:
    """Nearest-rank percentile. Deliberately not interpolating — with
    sample counts in the single digits an interpolated p90 invents a
    number that no crossing ever took."""
    if not sorted_vals:
        raise ValueError("empty")
    k = max(1, min(len(sorted_vals), round(q * len(sorted_vals))))
    return sorted_vals[k - 1]


async def record_crossing(
    session: AsyncSession,
    *,
    zone_id: str,
    entered_at: datetime,
    closed_at: datetime,
    congestion_tier: str | None,
    battery_band: str | None,
    clean: bool,
) -> int:
    """Write one completed crossing. Returns the measured duration in
    minutes so the caller can store it on the trip too.

    Never raises on a nonsensical duration — a demo clock that jumped
    backwards, or a trip closed in the same second it opened, should not
    take a crossing down with it. Such rows are recorded as 0 and filtered
    out on read."""
    minutes = max(0, round((closed_at - entered_at).total_seconds() / 60))
    session.add(
        CrossingHistory(
            zone_id=zone_id,
            hour_of_day=entered_at.hour,
            crossing_min=minutes,
            congestion_tier=congestion_tier,
            battery_band=battery_band,
            clean=clean,
            recorded_at=closed_at,
        )
    )
    return minutes


async def stats_for(
    session: AsyncSession, *, zone_id: str, hour_of_day: int,
) -> CorridorStats | None:
    """p50/p90 for this corridor at this hour, or None when there isn't
    enough clean history to say anything a constant wouldn't say better."""
    result = await session.execute(
        select(CrossingHistory.crossing_min).where(
            CrossingHistory.zone_id == zone_id,
            CrossingHistory.hour_of_day == hour_of_day,
            CrossingHistory.clean.is_(True),
            CrossingHistory.crossing_min > 0,
            CrossingHistory.recorded_at >= _window_cutoff(),
        )
    )
    values = sorted(result.scalars().all())
    if len(values) < settings.corridor_stats_min_samples:
        return None
    return CorridorStats(
        zone_id=zone_id,
        hour_of_day=hour_of_day,
        samples=len(values),
        p50_min=round(statistics.median(values)),
        p90_min=_percentile(values, 0.9),
    )


async def zone_summary(session: AsyncSession, *, zone_id: str) -> dict:
    """Everything *recent* history knows about one zone, for the
    dashboard's corridor-stats panel. Buckets with too few samples are
    still listed — a dispatcher benefits from seeing that 04:00 has one
    crossing behind it, which is exactly why the system is still using
    the nominal there.

    Windowed the same way stats_for is (corridor_stats_window_days) and
    for the same reason: this used to load every crossing this zone had
    ever recorded, on every dashboard bootstrap. total_crossings and
    clean_crossings below now describe the window, not all of history —
    the more honest number anyway, since a count that includes crossings
    from a year ago isn't telling a dispatcher anything about the
    corridor's current behaviour. Selects only the three columns the
    bucketing actually needs, rather than full CrossingHistory rows."""
    cutoff = _window_cutoff()
    result = await session.execute(
        select(
            CrossingHistory.hour_of_day,
            CrossingHistory.crossing_min,
            CrossingHistory.clean,
        ).where(
            CrossingHistory.zone_id == zone_id,
            CrossingHistory.recorded_at >= cutoff,
        )
    )
    rows = result.all()
    by_hour: dict[int, list[int]] = {}
    clean_crossings = 0
    for hour_of_day, crossing_min, clean in rows:
        if clean and crossing_min > 0:
            by_hour.setdefault(hour_of_day, []).append(crossing_min)
            clean_crossings += 1

    buckets = []
    for hour in sorted(by_hour):
        vals = sorted(by_hour[hour])
        buckets.append({
            "hour_of_day": hour,
            "samples": len(vals),
            "p50_min": round(statistics.median(vals)),
            "p90_min": _percentile(vals, 0.9),
            "in_use": len(vals) >= settings.corridor_stats_min_samples,
        })
    return {
        "zone_id": zone_id,
        "total_crossings": len(rows),
        "clean_crossings": clean_crossings,
        "min_samples": settings.corridor_stats_min_samples,
        "buckets": buckets,
    }
