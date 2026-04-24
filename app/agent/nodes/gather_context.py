"""
Context gathering node.

Responsibilities:
  1. Extract order_id from the customer message (regex + simple heuristics).
  2. If no order_id is available yet, signal that we need to ask for it.
  3. Load the full order/customer/rider/restaurant context from SQLite.
  4. Retrieve relevant policy sections for the current message via RAG.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.state import SupportAgentState
from app.core.logging import get_logger
from app.repositories.database import DatabaseRepository
from app.services.rag import get_rag_service

logger = get_logger(__name__)

# Matches things like "order 123", "order #456", "#789", "order id: 42", "order no. 42"
_ORDER_ID_RE = re.compile(
    r"(?:order\s*(?:id|#|no\.?|number)?[:# ]*|(?<!\w)#)(\d{1,6})\b",
    re.IGNORECASE,
)

# Strip currency amounts so we don't confuse "₹3000" with order id 3000
_CURRENCY_RE = re.compile(r"[₹₨]\s*\d+|\bINR\s*\d+|\bRs\.?\s*\d+", re.IGNORECASE)

# Injection patterns — if present, skip standalone-number fallback extraction
_INJECTION_SIGNAL_RE = re.compile(
    r"system\s*override|forget\s+(your|prior|all)|ignore\s+(previous|prior|your)|jailbreak|"
    r"approve\s+a?\s*[₹₨]\d+|credit\s+me\s+[₹₨]\s*\d{4}",
    re.IGNORECASE,
)


def _extract_order_id(text: str, explicit_only: bool = False) -> int | None:
    """
    Extract an order id from free-form customer text.

    Args:
        text: The customer message.
        explicit_only: If True, only return an ID from an explicit "order #NNN" pattern.
            When False, also uses a standalone-number fallback (only safe when there is
            no existing order_id to protect).

    Avoids false positives from currency amounts and injection payloads.
    """
    # Remove currency-looking numbers before any extraction
    cleaned = _CURRENCY_RE.sub("", text)

    # Primary: look for explicit "order #NNN" pattern
    match = _ORDER_ID_RE.search(cleaned)
    if match:
        return int(match.group(1))

    if explicit_only:
        return None

    # If the message looks like an injection attempt, don't use the numeric fallback
    if _INJECTION_SIGNAL_RE.search(text):
        return None

    # Fallback: standalone number in a plausible order-id range (1–9999)
    # Deliberately narrow range to reduce false positives
    standalone = re.findall(r"\b(\d{1,4})\b", cleaned)
    for candidate in standalone:
        val = int(candidate)
        if 1 <= val <= 9999:
            return val

    return None


def gather_context(
    state: SupportAgentState,
    db_repo: DatabaseRepository | None = None,
) -> dict[str, Any]:
    """
    LangGraph node: extract order context and relevant policy.

    Returns partial state updates.
    """
    message = state.get("current_customer_message", "")
    existing_order_id: int | None = state.get("order_id")

    # Try to extract order_id from the current message.
    # If we already have a confirmed order_id, only accept an EXPLICIT "order #NNN"
    # pattern as an override (to allow customers to correct themselves).
    # The standalone-number fallback is only safe when we have no existing context
    # to protect — otherwise "100%" or similar snippets corrupt the session.
    if existing_order_id:
        extracted = _extract_order_id(message, explicit_only=True)
    else:
        extracted = _extract_order_id(message, explicit_only=False)

    if extracted and extracted != existing_order_id:
        order_id = extracted
        if existing_order_id:
            logger.info(
                "Customer provided new order_id %d (was %d)", extracted, existing_order_id
            )
    else:
        order_id = existing_order_id or extracted

    if not order_id:
        logger.debug("No order_id found in message, will ask customer")
        return {
            "order_id": None,
            "customer_context": None,
            "needs_order_id": True,
        }

    # Load DB context
    repo = db_repo or DatabaseRepository()
    customer_context = repo.build_order_context(order_id)

    if "error" in customer_context:
        logger.warning("Order %d not found in DB: %s", order_id, customer_context["error"])
        # Still pass the order_id so the agent can tell the customer it's invalid
        return {
            "order_id": order_id,
            "customer_context": customer_context,
            "needs_order_id": False,
        }

    # RAG: find relevant policy sections for this customer message
    rag = get_rag_service()
    policy_context = rag.get_relevant_context(message, top_k=3)

    logger.info(
        "Context loaded for order %d (customer_id=%s)",
        order_id,
        customer_context.get("customer", {}).get("id"),
    )

    return {
        "order_id": order_id,
        "customer_context": customer_context,
        "policy_context": policy_context,
        "needs_order_id": False,
    }
