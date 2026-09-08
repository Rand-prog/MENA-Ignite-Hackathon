"""Builds trip.decision_record — a human-readable multi-line string, read on
camera. Never raw JSON. Format per the build prompt."""
from __future__ import annotations

from .fallback import RiskDecision


def build_decision_record(
    *, congestion_tier: str, battery_pct: int, hour: int, corridor_km: int,
    decision: RiskDecision, tools_called: list[str], qod_requested: bool,
    corridor_history: str | None = None, planned_stop_min: int = 0,
) -> str:
    lines = [
        f"signals   congestion={congestion_tier}  battery={battery_pct}%  "
        f"hour={hour}  zone={corridor_km}km",
        f"model     {decision.model_used}",
    ]
    # What the corridor's own history said, shown as its own line rather
    # than folded into "signals" — a dispatcher reading this at 2am needs
    # to be able to tell at a glance whether the window rests on measured
    # crossings or on the registry's hand-set constant.
    if corridor_history:
        lines.append(f"history   {corridor_history}")
    else:
        lines.append("history   none yet — planning against the registry nominal")
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
    if planned_stop_min:
        lines.append(
            f"declared  traveller declared a {planned_stop_min} min stop — "
            f"window extended by that much on top of the agent's judgement"
        )
    lines.append(f"tools     {', '.join(tools_called)}")
    # QoD is judged here and spent on the reconnection edge, not at entry —
    # see agent/graph.py's _decide_qod. The record says which of those two
    # things happened so nobody reads "QoD warranted" as "QoD already
    # spent."
    if decision.qod_warranted:
        action = (
            "QoD warranted — session held for the reconnection edge, "
            "not spent at entry; monitoring window armed"
        )
    else:
        action = "No QoD warranted; monitoring window armed"
    lines.append(f"action    {action}")
    return "\n".join(lines)


def build_tier0_record(*, asked_at: str, grace_sec: int, answer: str | None) -> str:
    """Tier 0 — the record of asking the traveller before telling anyone.

    Worth writing down precisely because the successful case leaves no
    other trace: nobody was messaged, no dashboard entry was raised, and
    without this the trip would close looking like nothing ever went past
    its window. A dispatcher reviewing a corridor's history needs to be
    able to see how often the window was too tight."""
    outcome = {
        "safe": "traveller confirmed safe on their own handset — no human contacted",
        None: f"no answer within {grace_sec}s — escalation proceeded to Tier 1",
    }.get(answer, f"answered {answer!r} — escalation proceeded to Tier 1")
    return "\n".join([
        "escalation tier0",
        f"signals    asked_at={asked_at}  grace={grace_sec}s",
        "note       handset was reachable again, so it was asked before any "
        "human was told",
        "tools      check_device_reachability, ask_traveller",
        f"action     {outcome}",
    ])


def build_convoy_record(*, peer_state: str, exited_min_ago: int | None,
                        crossing_min: int | None) -> str:
    """Convoy — what a peer who already came out the far side contributes.

    This never decides whether to escalate. It adds the one piece of
    context a contact 400km away cannot: somebody drove this exact road
    minutes ago, and here is how long it took them."""
    when = f"{exited_min_ago} min ago" if exited_min_ago is not None else "recently"
    took = f"{crossing_min} min" if crossing_min else "unknown"
    return "\n".join([
        "escalation convoy",
        f"signals    peer_state={peer_state}  peer_exited={when}  peer_crossing={took}",
        "note       another traveller who entered this corridor in the same "
        "window is already out the far side — a far better first check than "
        "a contact in another city",
        "tools      find_convoy_peer",
        "action     peer surfaced to the dashboard alongside the escalation",
    ])


def build_escalation_record(
    *, tier: str, reachability_status: str, note: str, tools_called: list[str],
) -> str:
    """Escalation-time record — a second, separate decision trace from the
    entry-time one above. Appended to trip.escalation_record each time a
    trip crosses into TIER1_ALERTED or TIER2_ESCALATED, so a dispatcher can
    see what the agent actually checked at escalation, not just at entry.

    This never overrides *whether* to escalate — that stays timer-driven,
    per docs/SignalGuard_Technical_Feasibility.pdf §3, §6. It only records
    what the agent observed and which tools it called while composing the
    escalation."""
    action = {
        "tier1": "contact notified (Tier 1)",
        "tier2": "escalated to emergency-centre dashboard (Tier 2)",
    }.get(tier, tier)
    lines = [
        f"escalation {tier}",
        f"signals    device_reachability={reachability_status}",
        f"note       {note}",
        f"tools      {', '.join(tools_called)}",
        f"action     {action}",
    ]
    return "\n".join(lines)
