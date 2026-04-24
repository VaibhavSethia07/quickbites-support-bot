"""
LangGraph StateGraph for the QuickBites support agent.

Graph topology:
  START → gather_context → assess_risk → decide → END

The graph is compiled once and reused across requests (it is stateless;
all state lives in the SupportAgentState dict passed at invocation time).
"""

from __future__ import annotations

import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.assess_risk import assess_risk
from app.agent.nodes.decide import decide
from app.agent.nodes.gather_context import gather_context
from app.agent.state import SupportAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build_graph():
    """Build and compile the support agent graph."""
    builder = StateGraph(SupportAgentState)

    builder.add_node("gather_context", gather_context)
    builder.add_node("assess_risk", assess_risk)
    builder.add_node("decide", decide)

    builder.add_edge(START, "gather_context")
    builder.add_edge("gather_context", "assess_risk")
    builder.add_edge("assess_risk", "decide")
    builder.add_edge("decide", END)

    return builder.compile()


# Module-level compiled graph (thread-safe; compiled once)
_graph = None
_graph_lock = threading.Lock()


def get_graph():
    """Return the compiled LangGraph graph (lazy singleton)."""
    global _graph
    if _graph is None:
        with _graph_lock:
            if _graph is None:
                logger.info("Compiling LangGraph support agent…")
                _graph = _build_graph()
                logger.info("LangGraph agent compiled")
    return _graph


def run_agent_turn(
    session_id: str,
    mode: str,
    customer_message: str,
    turn_count: int,
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run a single turn of the support agent.

    Args:
        session_id: Simulator session identifier.
        mode: "dev" or "prod".
        customer_message: The customer's latest message.
        turn_count: Current turn number (1-indexed).
        previous_state: Carried-over state from the last turn (order_id, context, etc.).

    Returns:
        Updated state dict with bot_message, actions, and done flag.
    """
    graph = get_graph()

    # Merge previous state into the initial state for this turn
    base: dict[str, Any] = {
        "session_id": session_id,
        "mode": mode,
        "turn_count": turn_count,
        "messages": [],
        "current_customer_message": customer_message,
        "order_id": None,
        "customer_context": None,
        "risk_score": 0.0,
        "risk_flags": [],
        "policy_context": "",
        "bot_message": "",
        "actions": [],
        "session_actions_taken": [],
        "needs_order_id": False,
        "done": False,
    }

    # Carry over persistent state from previous turns
    if previous_state:
        for key in ("order_id", "customer_context", "risk_score", "policy_context",
                    "session_actions_taken"):
            if previous_state.get(key) is not None:
                base[key] = previous_state[key]
        if previous_state.get("messages"):
            base["messages"] = previous_state["messages"]

    result = graph.invoke(base)

    # Accumulate this turn's actions into the session log for next turn
    new_actions = result.get("actions", [])
    previous_taken = result.get("session_actions_taken") or []
    result["session_actions_taken"] = previous_taken + new_actions

    # Append this turn's exchange to the message history for next turn
    if result.get("bot_message"):
        result["messages"] = result.get("messages", []) + [
            {"role": "user", "content": customer_message},
            {"role": "assistant", "content": result["bot_message"]},
        ]

    return result
