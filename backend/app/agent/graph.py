"""LangGraph agent runtime. Event-driven: a network geofence event wakes it,
it plans and calls tools, writes a decision record, then sleeps — see
docs/SignalGuard_Technical_Feasibility.pdf §5.4.

Gemini 2.5 (via Google AI Studio) supplies the weighting and the
natural-language reasoning. LangGraph holds the graph. Underneath both sits
the deterministic fallback in fallback.py, which this module falls back to
whenever the model is disabled, unreachable, slow, or returns something
that doesn't parse — never by skipping the agent step.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .. import corridor_stats
from ..clock import clock
from ..config import settings
from . import tools
from .decision_record import build_decision_record
from .fallback import RiskDecision, clamp_window, deterministic_fallback
from .tools import AgentContext

logger = logging.getLogger("signalguard.agent")

RISK_PROMPT = """You are SignalGuard's risk-judgement agent for a highway \
dead-zone crossing. A traveller has just entered a monitored corridor.

Signals:
- corridor length: {corridor_km} km
- nominal crossing time: {nominal_min} min
- observed history for this corridor at this hour: {history}
- congestion tier at entry: {congestion_tier}
- battery at entry: {battery_pct}%
- hour of day: {hour}

Decide:
1. predicted_crossing_min — how long this crossing should take
2. monitoring_window_min — nominal + a risk-adjusted buffer before anyone \
is alarmed (wider for low battery, heavy congestion, night hours)
3. qod_warranted — true only if this crossing genuinely needs a \
connectivity boost (low battery or a long corridor), not every crossing
4. risk — "LOW" or "ELEVATED". Driven by battery, corridor length, and \
hour. Heavier congestion should LOWER risk, not raise it — slower \
speeds and more people nearby make a crossing safer, so treat higher \
congestion as a point toward LOW (it can still widen \
monitoring_window_min, that's independent).
5. reasoning — one or two short sentences a dispatcher could read at 2am

Where observed history is available it beats the nominal: the nominal is a \
single hand-set constant for the whole day, the history is what this road \
actually did at this hour. Plan against the typical figure and size the \
buffer against the slowest one.

Reply with ONLY a JSON object with exactly these keys: \
predicted_crossing_min, monitoring_window_min, qod_warranted, risk, reasoning."""


class AgentState(TypedDict, total=False):
    ctx: AgentContext
    model_enabled: bool
    congestion_tier: str
    location: dict
    zone_profile: dict
    battery_pct: int
    hour: int
    corridor_stats: Any
    tools_called: list[str]
    decision: RiskDecision
    qod_requested: bool


async def _gather_signals(state: AgentState) -> AgentState:
    """Collect everything the risk judgement needs.

    Congestion Insights and Location Retrieval are independent calls to
    independent CAMARA APIs — nothing in either depends on the other's
    result — so they go out concurrently. Measured against the live
    sandbox that is 375ms sequential vs 253ms concurrent (median of 8
    runs), and the gap widens exactly when it matters: Congestion Insights
    was observed at 1296ms during a real escalation run, and that whole
    second used to sit in series ahead of a call that could have
    overlapped it."""
    ctx = state["ctx"]
    tools_called = state.setdefault("tools_called", [])

    zp = await tools.get_zone_profile(ctx, ctx.zone.zone_id)
    tools_called.append("get_zone_profile")
    state["zone_profile"] = zp

    congestion, location = await asyncio.gather(
        tools.get_congestion_insights(ctx, ctx.zone.zone_id),
        tools.get_location(ctx, ctx.trip.id),
    )
    tools_called.append("get_congestion_insights")
    tools_called.append("get_location")
    state["congestion_tier"] = congestion.get("tier", "unknown")
    state["location"] = location

    # What this corridor actually did at this hour, if there is enough
    # clean history to say. None means the registry's hand-set nominal
    # stands — see corridor_stats.py on why a p50 over two samples is
    # worse than the constant it would replace.
    stats = await corridor_stats.stats_for(
        ctx.session, zone_id=ctx.zone.zone_id, hour_of_day=state["hour"],
    )
    state["corridor_stats"] = stats
    if stats is not None:
        tools_called.append("get_corridor_history")

    return state


async def _call_gemini(state: AgentState) -> RiskDecision | None:
    """Real Gemini call via Google AI Studio. Returns None on any failure —
    the caller falls back to the deterministic model, no exception escapes."""
    if not settings.google_api_key:
        return None
    try:
        from langchain_core.messages import HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model=settings.gemini_risk_model or settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=0.2,
        )
        stats = state.get("corridor_stats")
        prompt = RISK_PROMPT.format(
            corridor_km=state["zone_profile"]["corridor_km"],
            nominal_min=state["zone_profile"]["nominal_crossing_min"],
            history=stats.summary() if stats else "none yet — use the nominal",
            congestion_tier=state["congestion_tier"],
            battery_pct=state["battery_pct"],
            hour=state["hour"],
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        text = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        payload = json.loads(text)

        window = clamp_window(int(payload["monitoring_window_min"]))
        predicted = int(payload["predicted_crossing_min"])
        risk = str(payload.get("risk", "LOW")).upper()
        if risk not in ("LOW", "ELEVATED"):
            risk = "LOW"
        return RiskDecision(
            predicted_crossing_min=predicted,
            monitoring_window_min=window,
            risk=risk,
            qod_warranted=bool(payload.get("qod_warranted", False)),
            reasoning=str(payload.get("reasoning", "")).strip(),
            model_used=settings.gemini_risk_model or settings.gemini_model,
        )
    except Exception as exc:  # noqa: BLE001 — any failure here means "fall back"
        # Some exceptions (e.g. asyncio.TimeoutError) have an empty str() —
        # without the type name too, "Gemini call failed: " with nothing
        # after the colon looks like an unexplained crash rather than
        # saying what actually happened.
        logger.warning(
            "Gemini call failed, falling back to deterministic model: %s: %s",
            type(exc).__name__, exc,
        )
        return None


async def _reason(state: AgentState) -> AgentState:
    decision: RiskDecision | None = None
    if state.get("model_enabled", True):
        decision = await _call_gemini(state)

    if decision is None:
        # The deterministic path gets the learned prior too. It is the path
        # that runs when the model is down, which is exactly when the
        # window most needs to rest on something observed rather than on a
        # constant multiplied by 1.4.
        stats = state.get("corridor_stats")
        decision = deterministic_fallback(
            nominal_crossing_min=state["zone_profile"]["nominal_crossing_min"],
            congestion_tier=state["congestion_tier"],
            battery_pct=state["battery_pct"],
            observed_p50_min=stats.p50_min if stats else None,
            observed_p90_min=stats.p90_min if stats else None,
        )
    state["decision"] = decision
    return state


async def _decide_qod(state: AgentState) -> AgentState:
    """Decide whether this crossing warrants a Quality on Demand boost —
    and deliberately do NOT spend it here.

    QoD used to be requested at this point, at the entry gate. That is the
    worst available moment for it: the handset is seconds from losing
    signal entirely, so the boosted session is applied to a device that is
    about to stop using the network at all, and it has usually expired by
    the time the traveller is back. The moment that bandwidth is actually
    worth something is the *reconnection* edge — position upload, the
    all-clear to a waiting contact, queued data flushing. So the judgement
    is recorded on the trip here and state_machine.simulate_reachability
    spends it there. Same API, same one session per crossing, materially
    better placed."""
    ctx = state["ctx"]
    decision = state["decision"]
    ctx.trip.qod_warranted = bool(decision.qod_warranted)
    state["qod_requested"] = False
    return state


async def _write_record(state: AgentState) -> AgentState:
    ctx = state["ctx"]
    decision = state["decision"]
    trip = ctx.trip

    trip.congestion_tier = state["congestion_tier"]
    trip.last_known_location = state["location"].get("label", "unavailable")
    trip.entry_point = trip.entry_point or trip.last_known_location
    trip.predicted_crossing_min = decision.predicted_crossing_min
    trip.monitoring_window_min = decision.monitoring_window_min
    trip.risk = decision.risk
    trip.model_used = decision.model_used
    stats = state.get("corridor_stats")
    trip.decision_record = build_decision_record(
        congestion_tier=state["congestion_tier"],
        battery_pct=state["battery_pct"],
        hour=state["hour"],
        corridor_km=state["zone_profile"]["corridor_km"],
        decision=decision,
        tools_called=state["tools_called"],
        qod_requested=state["qod_requested"],
        corridor_history=stats.summary() if stats else None,
        planned_stop_min=trip.planned_stop_min or 0,
    )
    return state


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("gather_signals", _gather_signals)
    graph.add_node("reason", _reason)
    graph.add_node("decide_qod", _decide_qod)
    graph.add_node("write_record", _write_record)

    graph.set_entry_point("gather_signals")
    graph.add_edge("gather_signals", "reason")
    graph.add_edge("reason", "decide_qod")
    graph.add_edge("decide_qod", "write_record")
    graph.add_edge("write_record", END)
    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_agent(ctx: AgentContext, *, model_enabled: bool, battery_pct: int) -> RiskDecision:
    """Entry point routers call. Runs the graph end to end and leaves
    ctx.trip populated with everything /trips/{id} needs — the caller is
    responsible for the ACTIVE state transition and persisting the row."""
    state: AgentState = {
        "ctx": ctx,
        "model_enabled": model_enabled,
        "battery_pct": battery_pct,
        # Virtual clock, not wall time. corridor_stats writes its
        # history bucket from trip.entered_at, which *is* the virtual
        # clock — reading the bucket back from a wall-clock hour meant
        # that in any run where /demo/clock/advance crossed an hour
        # boundary the two never referred to the same bucket, so the
        # learned corridor time could not be read back at all. Same
        # reason the hour handed to the model has to be the one the
        # crossing actually happened in: night is a risk input.
        "hour": clock.now().hour,
    }
    result = await get_graph().ainvoke(state)
    return result["decision"]
