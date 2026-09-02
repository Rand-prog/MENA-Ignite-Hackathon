"""Virtual clock. All trip timers key off this when the demo has advanced it;
otherwise it tracks wall time. One process-wide instance — the demo is a
single backend process, per run_demo.py's contract.

Every timer in the trip lifecycle (monitoring window, Tier 1/2 grace) must
read time through here, never through datetime.now() directly, or
/demo/clock/advance stops meaning anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class VirtualClock:
    """Returns naive UTC datetimes. SQLite drops tzinfo on round-trip, so
    every timestamp in this system is naive-UTC by convention — never mix
    in an aware datetime, or comparisons raise TypeError."""

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None) + self._offset

    def advance(self, seconds: int) -> datetime:
        self._offset += timedelta(seconds=seconds)
        return self.now()

    def reset(self) -> None:
        self._offset = timedelta(0)


clock = VirtualClock()
