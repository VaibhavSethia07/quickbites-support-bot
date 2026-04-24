"""
LangGraph state schema for the QuickBites support agent.

Each node receives the full state and returns a partial dict of updates.
Fields with Annotated[list, operator.add] accumulate across turns;
all other fields overwrite on update.
"""

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class SupportAgentState(TypedDict):
    # --- Session metadata ---
    session_id: str
    mode: str  # "dev" | "prod"
    turn_count: int

    # --- Conversation history (accumulates across turns) ---
    messages: Annotated[list[dict[str, str]], operator.add]

    # --- Current turn input ---
    current_customer_message: str

    # --- Context gathered from DB ---
    order_id: int | None
    customer_context: dict[str, Any] | None

    # --- Risk assessment ---
    risk_score: float
    risk_flags: Annotated[list[str], operator.add]

    # --- Policy retrieval ---
    policy_context: str

    # --- Agent output for this turn ---
    bot_message: str
    actions: list[dict[str, Any]]

    # --- Accumulated actions across the whole session (carries forward) ---
    # Used to prevent double-refunding or double-filing within a session.
    session_actions_taken: list[dict[str, Any]]

    # --- Control flags ---
    needs_order_id: bool   # True when we don't have an order_id yet
    done: bool             # True when the session should end
