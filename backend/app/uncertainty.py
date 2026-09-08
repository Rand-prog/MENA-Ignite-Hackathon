"""Where the traveller might be — as a range, not a point.

The dashboard used to draw a single dot at a position interpolated from
elapsed time. That dot is an estimate, and the map note said so in small
print at the bottom of the panel, but the marker itself was pixel-identical
to a real GPS fix. A dispatcher glancing at the screen during an escalation
reads a dot as "we know where they are." They do not.

This module computes what the system can actually defend: a range along the
corridor, bounded by the slowest and fastest a vehicle plausibly covers
that road, widening as time since the last real fix grows. The dashboard
draws that as an arc.

Why this matters beyond honesty: a Tier 2 escalation hands the emergency
centre a *search area*. A point is not a search area — it is a false one,
and a search team sent to a false point wastes the only resource that
matters. The arc is directly actionable in a way the dot never was.

The one real position this system ever holds is the Location Retrieval
snapshot taken at the entry gate. Everything after it is dead reckoning
against the clock, because the network cannot see inside a dead zone
either — that is the entire premise of the product. So the anchor is
always the entry point, and the uncertainty only ever grows.
"""
from __future__ import annotations

from dataclasses import dataclass

# Plausible sustained speeds on an inter-urban highway corridor, km/h.
# Deliberately wide. The slow bound is not "traffic" — it is "stopped for
# 20 minutes and then drove on", which is the single most common reason a
# crossing runs long and must stay inside the cone rather than falling out
# of it and reading as an emergency.
MIN_SPEED_KMH = 25.0
MAX_SPEED_KMH = 120.0

# Below this the cone is not drawn as a cone at all — within a few minutes
# of the entry gate the traveller is, to any useful precision, at the entry
# gate, and an arc a few hundred metres wide is visual noise.
MIN_SPREAD_KM = 2.0


@dataclass(frozen=True)
class PositionEstimate:
    """A range along the corridor, expressed as fractions of the entry ->
    exit line so the client can project it without knowing the geometry."""

    # Best guess, [0, 1] along the corridor.
    progress: float
    # Bounds of the plausible range, both [0, 1].
    progress_min: float
    progress_max: float
    # How wide that range is on the ground, for the dispatcher-facing label.
    spread_km: float
    # Minutes since the last real position fix (the entry-gate snapshot).
    minutes_since_fix: float
    # False once the traveller is overdue: past the predicted crossing time
    # the "best guess" is no longer a guess about a moving vehicle, it is
    # just the far end of the corridor, and labelling it as a position
    # would be the exact overclaim this module exists to prevent.
    best_guess_meaningful: bool

    def to_dict(self) -> dict:
        return {
            "progress": round(self.progress, 4),
            "progress_min": round(self.progress_min, 4),
            "progress_max": round(self.progress_max, 4),
            "spread_km": round(self.spread_km, 1),
            "minutes_since_fix": round(self.minutes_since_fix, 1),
            "best_guess_meaningful": self.best_guess_meaningful,
        }


def estimate(
    *,
    elapsed_min: float | None,
    corridor_km: int,
    predicted_crossing_min: int | None,
) -> PositionEstimate | None:
    """Position range for a trip that is `elapsed_min` into a
    `corridor_km` corridor. None when there isn't enough to say anything.

    The bounds come from distance, not from the predicted time: a vehicle
    that has been travelling 40 minutes has covered somewhere between
    25*40/60 and 120*40/60 km, whatever the model predicted. Deriving the
    cone from the prediction instead would make it narrow exactly when the
    prediction is wrong, which is precisely when a dispatcher needs it
    wide.
    """
    if elapsed_min is None or corridor_km <= 0:
        return None
    elapsed_h = max(0.0, elapsed_min) / 60.0

    km_min = MIN_SPEED_KMH * elapsed_h
    km_max = MAX_SPEED_KMH * elapsed_h

    progress_min = _clamp01(km_min / corridor_km)
    progress_max = _clamp01(km_max / corridor_km)

    # Widen a too-tight cone up to the floor, centred, so a trip a few
    # minutes in shows a small blob rather than a hairline.
    if (progress_max - progress_min) * corridor_km < MIN_SPREAD_KM:
        half = (MIN_SPREAD_KM / corridor_km) / 2
        mid = (progress_min + progress_max) / 2
        progress_min = _clamp01(mid - half)
        progress_max = _clamp01(mid + half)

    predicted = predicted_crossing_min or 0
    meaningful = predicted > 0 and elapsed_min <= predicted
    progress = _clamp01(elapsed_min / predicted) if predicted > 0 else progress_max
    # Keep the best guess inside its own bounds — with a wildly optimistic
    # prediction the two can otherwise disagree on screen, which looks like
    # a bug and undermines the thing this is trying to communicate.
    progress = min(max(progress, progress_min), progress_max)

    return PositionEstimate(
        progress=progress,
        progress_min=progress_min,
        progress_max=progress_max,
        spread_km=(progress_max - progress_min) * corridor_km,
        minutes_since_fix=max(0.0, elapsed_min),
        best_guess_meaningful=meaningful,
    )


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))
