"""Builds trip.decision_record — a human-readable multi-line string, read on
camera. Never raw JSON. Format per the build prompt."""
from __future__ import annotations

from .fallback import RiskDecision


def build_decision_record(
    *, congestion_tier: str, battery_pct: int, hour: int, corridor_km: int,
    decision: RiskDecision, tools_called: list[str], qod_requested: bool,
) -> str:
    lines = [
        f"signals   congestion={congestion_tier}  battery={battery_pct}%  "
        f"hour={hour}  zone={corridor_km}km",
        f"model     {decision.model_used}",
    ]
    if not decision.reasoning:
        lines.append("          floors/ceilings applied, no reasoning text")
    else:
        wrapped = decision.reasoning.strip()
        first, *rest = wrapped.splitlines() or [wrapped]
        lines.append(f"reasoning {first}")
        for line in rest:
            lines.append(f"          {line}")
    lines.append(
        f"window    {decision.monitoring_window_min} min "
        f"(nominal {decision.predicted_crossing_min}"
        + (" + buffer)" if decision.reasoning else " — deterministic, wider than the model's typical window)")
    )
    lines.append(f"tools     {', '.join(tools_called)}")
    action = "QoD session requested; monitoring window armed" if qod_requested \
        else "No QoD spent; monitoring window armed"
    lines.append(f"action    {action}")
    return "\n".join(lines)
