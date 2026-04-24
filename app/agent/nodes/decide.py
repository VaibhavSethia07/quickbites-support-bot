"""
Decision node: calls Claude with full context and returns structured actions.

Guardrails implemented here:
  1. Prompt injection detection — scans customer message before LLM call.
  2. Structured output via Anthropic tool-use — LLM must call `support_decision`.
  3. Post-LLM validation — Pydantic validates every action.
  4. Hard refund cap — total refund for an order never exceeds its total_inr.
  5. Risk-gated refund ceiling — max refund amount scales inversely with risk.
"""

from __future__ import annotations

import re
from typing import Any

import anthropic

from app.agent.state import SupportAgentState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.prompts.support_agent import (
    TOOL_SCHEMA,
    build_context_block,
    build_system_prompt,
)
from app.schemas.actions import IssueRefund, parse_action
from app.services.rag import get_rag_service

logger = get_logger(__name__)

# Patterns that suggest a customer is trying to override the agent's behaviour
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
        r"forget\s+(everything|all)\s+(you\s+know|instructions?)",
        r"(you\s+are|act\s+as|pretend\s+(to\s+be|you\s+are))\s+(a\s+)?(?!a\s+support)",
        r"jailbreak",
        r"dan\s+mode",
        r"system\s*:\s*you\s+are",
        r"<\s*system\s*>",
        r"override\s+(your\s+)?(instructions?|policy|rules?)",
        r"new\s+instructions?\s*:",
        r"credit\s+me\s+[₹₨rs\.]*\s*[5-9]\d{3,}",  # asking for ₹5000+
    ]
]


def _detect_injection(text: str) -> bool:
    """Return True if the text contains prompt-injection patterns."""
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _fix_hallucinated_name(message: str, correct_first_name: str) -> str:
    """
    Scan the first two sentences of the bot message for a capitalised name that
    follows a greeting/address pattern, and replace it if it doesn't match the
    correct customer first name.

    This catches both "Hi Vikram," and "Thanks for your patience, Vikram." forms.
    We deliberately only scan the first ~200 characters to avoid clobbering restaurant
    or rider names mentioned later in the message.
    """
    if not correct_first_name:
        return message

    scan_region = message[:200]

    # Find a customer name in the greeting portion of the message (first ~120 chars).
    # Strategy: look for a Proper Name (initial capital, 3+ letters) that appears
    # after a greeting keyword + separator. Two sub-patterns:
    #   Direct:   "Hi Vikram" / "Thanks, Vivaan" / "Understood, Vivaan"
    #   Indirect: "Thanks for getting back to me, Vivaan" / "I understand ..., Meera"
    # Case-sensitive name matching is intentional so "for", "you" etc. are not replaced.
    _GREETING_START = (
        r"(?:Hi|Hello|Hey|Dear|Thanks?|Thank\s+you|Understood|Sure|Absolutely|"
        r"Sorry|Apologies?|No\s+worries|Of\s+course)"
    )
    salutation_re = re.compile(
        r"(?:"
        # Direct: greeting + [,.\s] + Name
        rf"{_GREETING_START}[,.\s]\s*([A-Z][a-z]{{2,}})\b"
        r"|"
        # Indirect: starts with a greeting keyword, then up to 60 non-NL chars, then , Name
        rf"(?:^{_GREETING_START}|^I\s+(?:understand|apologize|apologise))"
        r"[^\n,]{0,60},\s*([A-Z][a-z]{2,})\b"
        r")",
        re.MULTILINE,
    )

    def replace_name(m: re.Match) -> str:
        # group(1) = Form A, group(2) = Form B
        grp = next((g for g in (1, 2) if m.group(g)), None)
        if not grp:
            return m.group(0)
        found = m.group(grp)
        if found and found.lower() != correct_first_name.lower():
            logger.warning(
                "Name hallucination corrected: '%s' → '%s'", found, correct_first_name)
            offset_start = m.start(grp) - m.start()
            offset_end = m.end(grp) - m.start()
            return m.group(0)[:offset_start] + correct_first_name + m.group(0)[offset_end:]
        return m.group(0)

    fixed_region = salutation_re.sub(replace_name, scan_region)
    return fixed_region + message[200:]


def _compute_max_refund(
    order_total: int,
    already_refunded: int,
    risk_score: float,
) -> int:
    """
    Compute the maximum additional refund we'll allow.

    - Hard cap: never exceed order_total − already_refunded.
    - Risk scaling: reduce ceiling as risk increases.
    """
    headroom = max(0, order_total - already_refunded)
    if risk_score < 0.35:
        ceiling = headroom  # Low risk: full order value
    elif risk_score < 0.65:
        ceiling = int(headroom * 0.5)  # Medium risk: 50% of remaining
    else:
        ceiling = 0  # High risk: no refund
    return ceiling


def _sanitise_actions(
    actions: list[dict[str, Any]],
    order_id: int | None,
    order_total: int,
    already_refunded: int,
    risk_score: float,
    session_complaints_filed: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Validate and sanitise actions returned by the LLM.

    Returns (clean_actions, warnings).
    """
    from app.schemas.actions import FileComplaint

    clean: list[dict[str, Any]] = []
    warnings: list[str] = []
    max_refund = _compute_max_refund(order_total, already_refunded, risk_score)
    total_refund_this_turn = 0
    complaints_filed_this_session = set(session_complaints_filed or set())
    complaints_filed_this_turn: set[str] = set()

    for raw in actions:
        try:
            action = parse_action(raw)
        except Exception as exc:
            warnings.append(f"Invalid action dropped ({exc}): {raw}")
            continue

        if isinstance(action, IssueRefund):
            # Enforce order_id
            if order_id and action.order_id != order_id:
                warnings.append(
                    f"Refund order_id mismatch ({action.order_id} vs {order_id}), corrected"
                )
                action = action.model_copy(update={"order_id": order_id})

            # Enforce refund cap
            available = max(0, max_refund - total_refund_this_turn)
            if action.amount_inr > available:
                if available == 0:
                    warnings.append(
                        f"Refund of ₹{action.amount_inr} dropped — cap reached "
                        f"(max={max_refund}, already_refunded={already_refunded}, risk={risk_score:.2f})"
                    )
                    continue
                warnings.append(
                    f"Refund capped from ₹{action.amount_inr} to ₹{available}"
                )
                action = action.model_copy(update={"amount_inr": available})
            total_refund_this_turn += action.amount_inr

        elif isinstance(action, FileComplaint):
            # Enforce order_id
            if order_id and action.order_id != order_id:
                action = action.model_copy(update={"order_id": order_id})

            # Deduplicate: don't file the same complaint type twice in same session
            key = action.target_type
            if key in complaints_filed_this_session or key in complaints_filed_this_turn:
                warnings.append(
                    f"Duplicate complaint ({key}) dropped — already filed this session"
                )
                continue
            complaints_filed_this_turn.add(key)

        clean.append(action.model_dump())

    return clean, warnings


def decide(state: SupportAgentState) -> dict[str, Any]:
    """
    LangGraph node: call the LLM and return the bot message + actions.

    This is the only node that makes an LLM call.
    """
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # ------------------------------------------------------------------
    # If we don't have an order_id yet, ask the customer politely
    # ------------------------------------------------------------------
    if state.get("needs_order_id"):
        return {
            "bot_message": (
                "I'd be happy to help you with that! Could you please share your order number "
                "so I can look into it right away?"
            ),
            "actions": [],
            "done": False,
        }

    # ------------------------------------------------------------------
    # Build system prompt (policy embedded)
    # ------------------------------------------------------------------
    rag = get_rag_service()
    system_prompt = build_system_prompt(rag.full_text)

    # ------------------------------------------------------------------
    # Build context block (structured data)
    # ------------------------------------------------------------------
    customer_context = state.get("customer_context") or {}
    order_id = state.get("order_id")
    risk_score = state.get("risk_score", 0.0)
    risk_flags = state.get("risk_flags") or []
    policy_context = state.get("policy_context") or rag.full_text

    # Handle "order not found" case
    if "error" in customer_context:
        error_msg = customer_context.get("error", "Order not found")
        return {
            "bot_message": (
                f"I'm sorry, I couldn't find order #{state.get('order_id')} in our system. "
                "Could you double-check the order number? If the issue persists, I'm happy to "
                "connect you with a support agent."
            ),
            "actions": [],
            "done": False,
        }

    session_actions_taken = state.get("session_actions_taken") or []

    context_block = build_context_block(
        customer_context=customer_context,
        risk_score=risk_score,
        risk_flags=risk_flags,
        policy_relevant_sections=policy_context,
        session_actions_taken=session_actions_taken,
    )

    # ------------------------------------------------------------------
    # Detect prompt injection in customer message
    # ------------------------------------------------------------------
    customer_message = state.get("current_customer_message", "")
    injection_detected = _detect_injection(customer_message)

    if injection_detected:
        logger.warning("Prompt injection detected: %.100s", customer_message)
        risk_score = min(1.0, risk_score + 0.3)
        risk_flags = list(risk_flags) + ["Suspected prompt injection attempt"]
        # Don't process the injection payload as a real complaint.
        # Return a neutral response that invites a legitimate complaint instead.
        return {
            "bot_message": (
                "Hello! I'm here to help with any genuine issues with your QuickBites order. "
                "Could you please share your order number and describe what went wrong?"
            ),
            "actions": [],
            "done": False,
        }

    # ------------------------------------------------------------------
    # Assemble conversation messages for the API call
    # ------------------------------------------------------------------
    history = state.get("messages") or []

    # Anchor header: shown FIRST so the LLM cannot misattribute customer or order
    customer = customer_context.get("customer") or {}
    customer_name = customer.get("name", "Unknown")
    risk_label = "LOW" if risk_score < 0.35 else (
        "MEDIUM" if risk_score < 0.65 else "HIGH")
    anchor = (
        f"[GROUNDING: Customer={customer_name} | Order=#{order_id} | "
        f"Turn={state.get('turn_count', 0)} | Risk={risk_label}]"
    )

    user_content = f"{anchor}\n\n{context_block}\n\n---\n\nCustomer message:\n{customer_message}"

    api_messages: list[dict[str, str]] = []
    for msg in history:
        api_messages.append({"role": msg["role"], "content": msg["content"]})
    api_messages.append({"role": "user", "content": user_content})

    # ------------------------------------------------------------------
    # LLM call with tool-use for structured output
    # ------------------------------------------------------------------
    logger.info(
        "Calling LLM (model=%s, turn=%d, risk=%.2f)",
        settings.anthropic_model,
        state.get("turn_count", 0),
        risk_score,
    )

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            temperature=0.0,  # Deterministic: grounded facts, minimal hallucination
            system=system_prompt,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "any"},  # Force tool use
            messages=api_messages,
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        return {
            "bot_message": (
                "I'm experiencing a technical issue right now. Please try again in a moment, "
                "or I can escalate this to a human agent."
            ),
            "actions": [{"type": "escalate_to_human", "reason": f"API error: {exc}"}],
            "done": False,
        }

    # ------------------------------------------------------------------
    # Parse structured output from tool call
    # ------------------------------------------------------------------
    bot_message = ""
    raw_actions: list[dict[str, Any]] = []

    for block in response.content:
        if block.type == "tool_use" and block.name == "support_decision":
            tool_input = block.input
            bot_message = tool_input.get("bot_message", "")
            raw_actions = tool_input.get("actions", [])
            break
        elif block.type == "text":
            # Fallback if tool wasn't called (shouldn't happen with tool_choice=any)
            bot_message = block.text
            logger.warning("LLM responded with text instead of tool call")

    if not bot_message:
        bot_message = "I'm looking into this for you. Could you give me a moment?"

    # ------------------------------------------------------------------
    # Post-processing: validate + cap refund amounts
    # ------------------------------------------------------------------
    order = customer_context.get("order") or {}
    order_total = order.get("total_inr", 0)

    # Sum refunds from DB (prior sessions) + in-session refunds already issued
    db_refunded = sum(
        r.get("amount_inr", 0)
        for r in customer_context.get("order_existing_refunds", [])
    )
    session_refunded = sum(
        a.get("amount_inr", 0)
        for a in session_actions_taken
        if a.get("type") == "issue_refund" and a.get("order_id") == order_id
    )
    already_refunded = db_refunded + session_refunded

    # Find complaint targets already filed this session to prevent duplicates
    session_complaints_filed = {
        a.get("target_type")
        for a in session_actions_taken
        if a.get("type") == "file_complaint" and a.get("order_id") == order_id
    }

    clean_actions, warnings = _sanitise_actions(
        raw_actions,
        order_id=order_id,
        order_total=order_total,
        already_refunded=already_refunded,
        risk_score=risk_score,
        session_complaints_filed=session_complaints_filed,
    )

    for w in warnings:
        logger.warning("Action sanitisation: %s", w)

    # Guardrail: escalate_to_human must always be paired with close in the same turn.
    action_types = {a.get("type") for a in clean_actions}
    if "escalate_to_human" in action_types and "close" not in action_types:
        logger.warning(
            "Auto-injecting 'close' action — escalation lacked close")
        escalation_reason = next(
            (a.get("reason", "")
             for a in clean_actions if a.get("type") == "escalate_to_human"),
            "",
        )
        clean_actions.append({
            "type": "close",
            "outcome_summary": f"Escalated to human agent. {escalation_reason[:200]}".strip(),
        })

    # ------------------------------------------------------------------
    # Determine if the session is done
    # ------------------------------------------------------------------
    is_done = any(a.get("type") in ("close", "escalate_to_human")
                  for a in clean_actions)

    # Guardrail: if the LLM hallucinated the wrong customer name, replace it.
    correct_name = (customer_context.get("customer") or {}).get("name", "")
    if correct_name:
        first_name = correct_name.split()[0]
        fixed = _fix_hallucinated_name(bot_message, first_name)
        if fixed != bot_message:
            logger.info("Name guardrail: corrected hallucinated name in bot_message")
            bot_message = fixed

    logger.info(
        "LLM decision: message=%.80s... | actions=%s | done=%s",
        bot_message,
        [a.get("type") for a in clean_actions],
        is_done,
    )

    return {
        "bot_message": bot_message,
        "actions": clean_actions,
        "done": is_done,
    }
