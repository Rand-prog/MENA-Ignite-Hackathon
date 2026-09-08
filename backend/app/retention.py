"""Retention / pruning for the three tables nothing else in this codebase
ever shrinks: `api_log`, `crossing_history`, `coverage_observations` (see
models.py) — each written once per event/crossing/observation and never
updated, so without this they grow for as long as the process lives.

None of the three gate anything a live trip depends on. `api_log` rows are
a call-by-call operational trace of already-completed Nokia calls;
`crossing_history` and `coverage_observations` are both written once, at
the moment a trip closes or a reachability check lands — by the time a row
is old enough to prune, whatever trip produced it is long since resolved.
corridor_stats.py's own read window (settings.corridor_stats_window_days)
is deliberately narrower than retention_crossing_history_days below, so a
prune can never delete a row a live agent run is still entitled to read.

`coverage_observations` gets the shortest window of the three on purpose.
It is the closest thing this system keeps to a location record — a
(position, reachable?) reading — even though it is snapped to a coarse
grid and carries no traveller or trip id (see coverage.py's own privacy
note). docs/SignalGuard_Security_Privacy (1).pdf §6/§12 names an
undecided-but-assumed 30-day retention window for ordinary trip records;
there is no argument for a table that is *more* sensitive than a trip
record to outlive that number.

No scheduler exists in this prototype (it's a single demo process, see
clock.py), so pruning runs opportunistically from state_machine.tick() —
called on every app/dashboard poll — rather than on its own timer.
Throttled to retention_prune_interval_min so the common case stays a
cheap in-memory timestamp check instead of three DELETE statements per
poll. /demo/reset is deliberately NOT a hook here: it already drops and
recreates every table (see db.reset_db), so a prune immediately after
would always run against empty tables.

Uses the virtual clock (clock.now()), like every other timer in this
system — a demo that fast-forwards hours at a time via
/demo/clock/advance ages data out on the same schedule a real deployment
would use wall-clock time for; comparing against real time here would
make the prune's cadence depend on how the process happened to be driven.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .clock import clock
from .config import settings
from .models import ApiLogEntry, CoverageObservation, CrossingHistory

# Process-wide — this prototype runs as one demo backend process (see
# clock.py's own docstring for the same assumption), so a module-level
# timestamp is enough to throttle without needing a DB round trip just to
# decide whether to prune.
_last_pruned_at: datetime | None = None


async def maybe_prune(session: AsyncSession) -> None:
    """Called from tick() on every poll. A no-op unless
    retention_prune_interval_min has actually elapsed on the virtual
    clock since the last real prune."""
    global _last_pruned_at
    now = clock.now()
    if _last_pruned_at is not None and (
        now - _last_pruned_at < timedelta(minutes=settings.retention_prune_interval_min)
    ):
        return
    _last_pruned_at = now
    await _prune(session, now=now)


async def _prune(session: AsyncSession, *, now: datetime) -> None:
    """The actual deletes. Self-contained (opens no transaction that
    outlives this call), so it's safe to run wherever tick() calls it —
    including before tick()'s own read/network/write phases."""
    await session.execute(
        delete(ApiLogEntry).where(
            ApiLogEntry.ts < now - timedelta(days=settings.retention_api_log_days)
        )
    )
    await session.execute(
        delete(CrossingHistory).where(
            CrossingHistory.recorded_at
            < now - timedelta(days=settings.retention_crossing_history_days)
        )
    )
    await session.execute(
        delete(CoverageObservation).where(
            CoverageObservation.observed_at
            < now - timedelta(days=settings.retention_coverage_observations_days)
        )
    )
    await session.commit()
