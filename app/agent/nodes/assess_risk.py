"""
Risk assessment node.

Fully rule-based — no LLM call. Computes a risk score [0.0, 1.0] and a list
of human-readable flags based on:
  - Customer complaint rate
  - Rejected complaints
  - Account age vs complaint count
  - Recent refund count and total
  - Whether the claim contradicts delivery evidence

The risk score and flags are injected into the LLM prompt so the model can
reason about them without recomputing them itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent.state import SupportAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)

# Reference date for the DB snapshot (treat as "today")
_DB_TODAY = datetime(2026, 4, 13, tzinfo=timezone.utc)


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        # SQLite stores timestamps as "YYYY-MM-DD HH:MM:SS" or ISO-8601
        date_str = date_str.replace(" ", "T")
        if not date_str.endswith("Z") and "+" not in date_str:
            date_str += "+00:00"
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def assess_risk(state: SupportAgentState) -> dict[str, Any]:
    """
    LangGraph node: compute risk score and flags from customer context.

    Returns partial state updates.
    """
    customer_context = state.get("customer_context")
    customer_message = state.get("current_customer_message", "").lower()

    # No context yet (waiting for order_id) — neutral risk
    if not customer_context or "error" in customer_context:
        return {"risk_score": 0.0, "risk_flags": []}

    flags: list[str] = []
    score = 0.0

    customer = customer_context.get("customer") or {}
    complaint_rate_data = customer_context.get("customer_complaint_rate") or {}
    recent_refunds = customer_context.get("customer_recent_refunds") or []
    recent_complaints = customer_context.get("customer_recent_complaints") or []
    order = customer_context.get("order") or {}
    rider_summary = customer_context.get("rider_incident_summary") or {}

    # ------------------------------------------------------------------
    # 1. Complaint rate
    # ------------------------------------------------------------------
    total_orders = complaint_rate_data.get("total_orders", 0)
    complaint_rate = complaint_rate_data.get("complaint_rate", 0.0)
    rejected_complaints = complaint_rate_data.get("rejected_complaints", 0)

    if total_orders >= 5:
        if complaint_rate >= 0.8:
            score += 0.4
            flags.append(f"Very high complaint rate: {complaint_rate:.0%} on {total_orders} orders")
        elif complaint_rate >= 0.5:
            score += 0.2
            flags.append(f"High complaint rate: {complaint_rate:.0%} on {total_orders} orders")
        elif complaint_rate >= 0.3:
            score += 0.1
            flags.append(f"Elevated complaint rate: {complaint_rate:.0%} on {total_orders} orders")

    # ------------------------------------------------------------------
    # 2. Rejected complaints (strong abuse signal)
    # ------------------------------------------------------------------
    if rejected_complaints >= 3:
        score += 0.25
        flags.append(f"{rejected_complaints} previously rejected complaints")
    elif rejected_complaints >= 1:
        score += 0.1
        flags.append(f"{rejected_complaints} previously rejected complaint(s)")

    # ------------------------------------------------------------------
    # 3. New account with multiple complaints
    # ------------------------------------------------------------------
    joined_at = _parse_date(customer.get("joined_at"))
    total_complaints = complaint_rate_data.get("total_complaints", 0)
    if joined_at:
        account_age_days = (_DB_TODAY - joined_at).days
        if account_age_days < 30 and total_complaints >= 2:
            score += 0.3
            flags.append(
                f"New account ({account_age_days}d old) with {total_complaints} complaints"
            )
        elif account_age_days < 60 and total_complaints >= 3:
            score += 0.15
            flags.append(
                f"Young account ({account_age_days}d old) with {total_complaints} complaints"
            )

    # ------------------------------------------------------------------
    # 4. Recent refund volume
    # ------------------------------------------------------------------
    refund_count = len(recent_refunds)
    refund_total = sum(r.get("amount_inr", 0) for r in recent_refunds)

    if refund_count >= 4:
        score += 0.3
        flags.append(f"{refund_count} refunds in last 30 days (₹{refund_total} total)")
    elif refund_count >= 2:
        score += 0.15
        flags.append(f"{refund_count} refunds in last 30 days (₹{refund_total} total)")

    # ------------------------------------------------------------------
    # 5. Claim contradicts delivery evidence
    # ------------------------------------------------------------------
    is_claiming_non_delivery = any(
        phrase in customer_message
        for phrase in ["never arrived", "not delivered", "didn't arrive", "never received",
                       "not received", "theft", "stolen", "someone else"]
    )
    order_was_delivered = order.get("status") == "delivered"
    rider_has_clean_record = rider_summary.get("verified_incidents", 0) == 0
    rider_theft_claims = rider_summary.get("theft_claims", 0)

    if is_claiming_non_delivery and order_was_delivered:
        if rider_has_clean_record and rider_theft_claims == 0:
            score += 0.25
            flags.append(
                "Claimed non-delivery but order marked delivered; rider has clean record"
            )
        elif rider_has_clean_record:
            score += 0.15
            flags.append(
                "Claimed non-delivery but order marked delivered (unverified theft claims exist)"
            )

    # ------------------------------------------------------------------
    # 6. High-value refund requests with weak history
    # ------------------------------------------------------------------
    order_total = order.get("total_inr", 0)
    loyalty_tier = customer.get("loyalty_tier", "bronze")
    if order_total > 1500 and loyalty_tier == "bronze" and complaint_rate > 0.3:
        score += 0.1
        flags.append(f"High-value order (₹{order_total}) with elevated complaint history")

    # ------------------------------------------------------------------
    # Clamp score to [0.0, 1.0]
    # ------------------------------------------------------------------
    score = min(1.0, max(0.0, score))

    logger.info(
        "Risk assessment: score=%.2f flags=%s",
        score,
        flags if flags else "none",
    )

    return {
        "risk_score": score,
        "risk_flags": flags,
    }
