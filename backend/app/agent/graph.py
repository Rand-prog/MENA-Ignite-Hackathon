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

import json
import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

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
    tools_called: list[str]
    decision: RiskDecision
    qod_requested: bool


async def _gather_signals(state: AgentState) -> AgentState:
    ctx = state["ctx"]
    tools_called = state.setdefault("tools_called", [])

    zp = await tools.get_zone_profile(ctx, ctx.zone.zone_id)
    tools_called.append("get_zone_profile")
    state["zone_profile"] = zp

    congestion = await tools.get_congestion_insights(ctx, ctx.zone.zone_id)
    tools_called.append("get_congestion_insights")
    state["congestion_tier"] = congestion.get("tier", "unknown")

    location = await tools.get_location(ctx, ctx.trip.id)
    tools_called.append("get_location")
    state["location"] = location

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
        prompt = RISK_PROMPT.format(
            corridor_km=state["zone_profile"]["corridor_km"],
            nominal_min=state["zone_profile"]["nominal_crossing_min"],
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
        logger.warning("Gemini call failed, falling back to deterministic model: %s", exc)
        return None


async def _reason(state: AgentState) -> AgentState:
    decision: RiskDecision | None = None
    if state.get("model_enabled", True):
        decision = await _call_gemini(state)

    if decision is None:
        decision = deterministic_fallback(
            nominal_crossing_min=state["zone_profile"]["nominal_crossing_min"],
            congestion_tier=state["congestion_tier"],
            battery_pct=state["battery_pct"],
        )
    state["decision"] = decision
    return state


async def _decide_qod(state: AgentState) -> AgentState:
    ctx = state["ctx"]
    decision = state["decision"]
    tools_called = state["tools_called"]
    qod_requested = False
    if decision.qod_warranted:
        result = await tools.request_qod_session(ctx, ctx.trip.id)
        tools_called.append("request_qod_session")
        qod_requested = bool(result.get("requested"))
    state["qod_requested"] = qod_requested
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
    trip.decision_record = build_decision_record(
        congestion_tier=state["congestion_tier"],
        battery_pct=state["battery_pct"],
        hour=state["hour"],
        corridor_km=state["zone_profile"]["corridor_km"],
        decision=decision,
        tools_called=state["tools_called"],
        qod_requested=state["qod_requested"],
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
        "hour": datetime.now(timezone.utc).hour,
    }
    result = await get_graph().ainvoke(state)
    return result["decision"]
