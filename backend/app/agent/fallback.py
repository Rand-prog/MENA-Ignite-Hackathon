"""The deterministic risk model. Runs whenever Gemini is slow, unavailable,
disabled, or returns something out of bounds.

This is the single most important behavioural guarantee in the whole
system: escalation is timer-driven, not model-driven. A model failure
degrades the quality of the judgement, never the existence of the alarm.
See docs/SignalGuard_Technical_Feasibility.pdf §5.4, §6.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings

CONGESTION_ADDEND_MIN = {"light": 5, "moderate": 15, "heavy": 25, "unknown": 15}
BATTERY_ADDEND_MIN = [(20, 12), (40, 6)]  # (threshold_pct, addend) — first match wins


def _battery_addend(battery_pct: int) -> int:
    for threshold, addend in BATTERY_ADDEND_MIN:
        if battery_pct < threshold:
            return addend
    return 0


def clamp_window(window_min: int) -> int:
    """The floor/ceiling both paths obey. The model can widen or narrow
    within these; it cannot set a window of zero and cannot switch the
    safety net off."""
    return max(settings.window_floor_min, min(settings.window_ceiling_min, window_min))


@dataclass
class RiskDecision:
    predicted_crossing_min: int
    monitoring_window_min: int
    risk: str
    qod_warranted: bool
    reasoning: str  # empty for the deterministic path — "no reasoning text"
    model_used: str


def model_weighted_window(
    *, nominal_crossing_min: int, congestion_tier: str, battery_pct: int,
) -> tuple[int, int]:
    """The formula an LLM-quality judgement approximates when reasoning
    normally: risk-adjusted buffer from congestion + battery. Used both as
    the deterministic-when-the-model-agrees baseline and to sanity-bound
    whatever Gemini returns."""
    congestion_addend = CONGESTION_ADDEND_MIN.get(congestion_tier, CONGESTION_ADDEND_MIN["unknown"])
    battery_addend = _battery_addend(battery_pct)
    predicted = nominal_crossing_min
    window = clamp_window(predicted + congestion_addend + battery_addend)
    return predicted, window


def deterministic_fallback(
    *, nominal_crossing_min: int, congestion_tier: str, battery_pct: int,
    observed_p50_min: int | None = None, observed_p90_min: int | None = None,
) -> RiskDecision:
    """No LLM involved at all. Wider than the model's typical window by
    construction — a conservative floor a human can trust when the
    reasoning layer is dark. No reasoning text: the whole point is that
    none was produced.

    When corridor_stats has enough clean history for this hour, that
    history replaces the registry's hand-set nominal on BOTH sides of the
    calculation: plan against the observed p50, and size the buffer with
    the observed p90 rather than a flat 1.4x multiplier. This is the path
    that runs when Gemini is unavailable, which is precisely when the
    window most needs to rest on something measured rather than on a
    constant somebody typed once — a 1.4x on a wrong nominal is wrong by
    1.4x too.

    The p90 is a floor on the buffer, never a ceiling: whichever of
    (observed p90) and (1.4x planned) is wider wins, so learning about a
    fast corridor can tighten the plan but can never narrow the safety
    margin below what the un-learned model would have given.
    """
    if observed_p50_min:
        predicted = observed_p50_min
        flat = round(predicted * 1.4)
        window = clamp_window(max(flat, observed_p90_min or 0))
    else:
        predicted = nominal_crossing_min
        window = clamp_window(round(nominal_crossing_min * 1.4))
    risk = "ELEVATED" if battery_pct < 25 else "LOW"
    # QoD trigger: low battery — the boost matters more when there's less
    # time left in which a handset can be found.
    qod_warranted = battery_pct < 30
    return RiskDecision(
        predicted_crossing_min=predicted,
        monitoring_window_min=window,
        risk=risk,
        qod_warranted=qod_warranted,
        reasoning="",
        model_used=(
            "UNAVAILABLE -> deterministic risk model (observed history)"
            if observed_p50_min
            else "UNAVAILABLE -> deterministic risk model"
        ),
    )
