"""LangGraph agent runtime for the escalation moment — Tier 1 and Tier 2.

Briefly reverted out of state_machine.tick() during a slowness
investigation, then re-wired once the real cause was confirmed to be
Gemini's free tier swinging wildly in latency (1.7s to 44s across a few
minutes, no stable baseline) — see graph.py's _call_gemini. This module
never calls Gemini, only Nokia (measured under 1s in isolation), so it
wasn't the bottleneck.

Separate graph from graph.py's entry-time one. Runs whenever
state_machine.tick() moves a trip into TIER1_ALERTED or TIER2_ESCALATED.

The invariant this must never violate: escalation is timer-driven, not
agent-driven (docs/SignalGuard_Technical_Feasibility.pdf §3, §6 — "the
transition out of ACTIVE is driven by a timer, not by the agent"). This
graph runs *after* tick() has already decided the state changes; it never
gets a vote on whether to escalate, only on how the escalation is composed
and recorded. If it raised an exception, the caller must still be able to
fire the escalation — see run_escalation_agent's try/except.

Two of the four tools that were dead code before this (check_device_reach-
ability, notify_contact, escalate_to_dashboard) get real call sites here.
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .. import convoy
from .decision_record import build_convoy_record, build_escalation_record
from .tools import AgentContext, check_device_reachability, escalate_to_dashboard, notify_contact

logger = logging.getLogger("signalguard.agent.escalation")

REACHABLE_STATUSES = {"CONNECTED_DATA", "CONNECTED_SMS"}


class EscalationState(TypedDict, total=False):
    ctx: AgentContext
    tier: str  # "tier1" | "tier2"
    reachability_status: str
    note: str
    tools_called: list[str]
    convoy_peer: Any


async def _check_reachability(state: EscalationState) -> EscalationState:
    ctx = state["ctx"]
    tools_called = state.setdefault("tools_called", [])
    result = await check_device_reachability(ctx, ctx.trip.id)
    tools_called.append("check_device_reachability")
    state["reachability_status"] = result.get("status", "unknown")
    return state


def _route_on_reachability(state: EscalationState) -> str:
    if state.get("reachability_status") in REACHABLE_STATUSES:
        return "reachable_race"
    return "confirmed_dark"


async def _reachable_race(state: EscalationState) -> EscalationState:
    state["note"] = (
        f"device reports {state['reachability_status']} at escalation time — "
        "possibly reconnected just as the window expired. Escalation proceeds "
        "unchanged: it is timer-driven, not suppressed by this reading. Worth "
        "a dispatcher double-check before dispatching further."
    )
    return state


async def _confirmed_dark(state: EscalationState) -> EscalationState:
    state["note"] = "device remains unreachable — consistent with the timer firing."
    return state


async def _find_convoy_peer(state: EscalationState) -> EscalationState:
    """Look for someone who drove this road minutes ago.

    A personal contact is usually in another city: they can confirm
    nothing, they can only worry. A convoy peer who entered the same
    corridor in the same window and is already out the far side knows
    whether it is blocked, whether traffic is crawling, whether there was
    an accident. That is a strictly better first check, and it costs one
    indexed query.

    This never votes on whether to escalate — the escalation has already
    happened by the time this node runs (see the module docstring). It
    only adds context to the record and to the dashboard."""
    ctx = state["ctx"]
    tools_called = state.setdefault("tools_called", [])
    try:
        peer = await convoy.usable_peer(ctx.session, ctx.trip)
    except Exception:  # noqa: BLE001 — context is a bonus, never a blocker
        logger.warning("convoy peer lookup failed for trip %s", ctx.trip.id, exc_info=True)
        peer = None
    if peer is not None:
        tools_called.append("find_convoy_peer")
        state["convoy_peer"] = convoy.peer_summary(peer)
    return state


async def _record(state: EscalationState) -> EscalationState:
    ctx = state["ctx"]
    tier = state["tier"]
    tools_called = state["tools_called"]

    if tier == "tier1":
        await notify_contact(ctx, ctx.trip.id, "tier1")
        tools_called.append("notify_contact")
    elif tier == "tier2":
        await escalate_to_dashboard(ctx, ctx.trip.id)
        tools_called.append("escalate_to_dashboard")

    record = build_escalation_record(
        tier=tier,
        reachability_status=state["reachability_status"],
        note=state["note"],
        tools_called=tools_called,
    )
    blocks = [record]
    peer = state.get("convoy_peer")
    if peer:
        blocks.append(build_convoy_record(
            peer_state=peer["state"],
            exited_min_ago=peer["exited_min_ago"],
            crossing_min=peer["crossing_min"],
        ))

    existing = ctx.trip.escalation_record
    joined = "\n\n".join(blocks)
    ctx.trip.escalation_record = f"{existing}\n\n{joined}" if existing else joined
    return state


def build_escalation_graph():
    graph = StateGraph(EscalationState)
    graph.add_node("check_reachability", _check_reachability)
    graph.add_node("reachable_race", _reachable_race)
    graph.add_node("confirmed_dark", _confirmed_dark)
    graph.add_node("find_convoy_peer", _find_convoy_peer)
    graph.add_node("record", _record)

    graph.set_entry_point("check_reachability")
    graph.add_conditional_edges(
        "check_reachability",
        _route_on_reachability,
        {"reachable_race": "reachable_race", "confirmed_dark": "confirmed_dark"},
    )
    # Both branches converge on the convoy lookup before recording — a peer
    # is useful context whether the handset came back at the last second or
    # is still dark, and it is the dark case where it matters most.
    graph.add_edge("reachable_race", "find_convoy_peer")
    graph.add_edge("confirmed_dark", "find_convoy_peer")
    graph.add_edge("find_convoy_peer", "record")
    graph.add_edge("record", END)
    return graph.compile()


_compiled_escalation_graph = None


def get_escalation_graph():
    global _compiled_escalation_graph
    if _compiled_escalation_graph is None:
        _compiled_escalation_graph = build_escalation_graph()
    return _compiled_escalation_graph


async def run_escalation_agent(ctx: AgentContext, *, tier: str) -> None:
    """Entry point tick() calls. Mutates ctx.trip in place (notifications +
    escalation_record) — the caller is responsible for committing.

    Any failure here must never block the escalation itself firing: if the
    graph blows up, the caller's own notifications append (already done by
    tick() before this runs — see state_machine.py) still stands. This
    function only adds richer tool-orchestration evidence on top."""
    state: EscalationState = {"ctx": ctx, "tier": tier}
    try:
        await get_escalation_graph().ainvoke(state)
    except Exception:  # noqa: BLE001 — escalation itself must never depend on this
        logger.warning("Escalation agent failed for trip %s tier %s", ctx.trip.id, tier, exc_info=True)
