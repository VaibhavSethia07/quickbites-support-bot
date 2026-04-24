"""Unit tests for the risk assessment node."""

import pytest

from app.agent.nodes.assess_risk import assess_risk


def _make_state(customer_context: dict, message: str = "food was cold") -> dict:
    return {
        "session_id": "test",
        "mode": "dev",
        "turn_count": 1,
        "messages": [],
        "current_customer_message": message,
        "order_id": 100,
        "customer_context": customer_context,
        "risk_score": 0.0,
        "risk_flags": [],
        "policy_context": "",
        "bot_message": "",
        "actions": [],
        "needs_order_id": False,
        "done": False,
    }


def _base_context(**overrides) -> dict:
    ctx = {
        "order": {
            "id": 100,
            "status": "delivered",
            "total_inr": 850,
            "payment_method": "upi",
        },
        "customer": {
            "id": 1,
            "name": "Good Customer",
            "loyalty_tier": "gold",
            "joined_at": "2024-01-01T00:00:00+00:00",
        },
        "customer_complaint_rate": {
            "total_orders": 20,
            "total_complaints": 2,
            "rejected_complaints": 0,
            "complaint_rate": 0.10,
        },
        "customer_recent_refunds": [],
        "customer_recent_complaints": [],
        "order_existing_refunds": [],
        "rider_incident_summary": {
            "total_incidents": 0,
            "verified_incidents": 0,
            "theft_claims": 0,
        },
        "restaurant_rating_summary": {"avg_rating": 4.2, "total_reviews": 50},
    }
    ctx.update(overrides)
    return ctx


class TestAssessRisk:
    def test_low_risk_good_customer(self):
        ctx = _base_context()
        result = assess_risk(_make_state(ctx))
        assert result["risk_score"] < 0.35
        assert result["risk_flags"] == []

    def test_high_complaint_rate_raises_score(self):
        ctx = _base_context(
            customer_complaint_rate={
                "total_orders": 10,
                "total_complaints": 9,
                "rejected_complaints": 0,
                "complaint_rate": 0.9,
            }
        )
        result = assess_risk(_make_state(ctx))
        assert result["risk_score"] >= 0.35
        assert any("complaint rate" in f.lower() for f in result["risk_flags"])

    def test_many_rejected_complaints_raises_score(self):
        ctx = _base_context(
            customer_complaint_rate={
                "total_orders": 10,
                "total_complaints": 5,
                "rejected_complaints": 4,
                "complaint_rate": 0.5,
            }
        )
        result = assess_risk(_make_state(ctx))
        assert result["risk_score"] >= 0.2
        assert any("rejected" in f.lower() for f in result["risk_flags"])

    def test_new_account_with_multiple_complaints(self):
        ctx = _base_context(
            customer={
                "id": 2,
                "loyalty_tier": "bronze",
                "joined_at": "2026-04-01T00:00:00+00:00",  # 12 days old
            },
            customer_complaint_rate={
                "total_orders": 3,
                "total_complaints": 2,
                "rejected_complaints": 0,
                "complaint_rate": 0.67,
            },
        )
        result = assess_risk(_make_state(ctx))
        assert result["risk_score"] >= 0.3
        assert any("new account" in f.lower() or "account" in f.lower() for f in result["risk_flags"])

    def test_many_recent_refunds_raises_score(self):
        ctx = _base_context(
            customer_recent_refunds=[
                {"amount_inr": 500, "type": "wallet_credit"},
                {"amount_inr": 300, "type": "cash"},
                {"amount_inr": 700, "type": "wallet_credit"},
                {"amount_inr": 200, "type": "cash"},
            ]
        )
        result = assess_risk(_make_state(ctx))
        assert result["risk_score"] >= 0.2
        assert any("refund" in f.lower() for f in result["risk_flags"])

    def test_non_delivery_claim_on_delivered_clean_rider(self):
        ctx = _base_context(
            order={"id": 100, "status": "delivered", "total_inr": 850, "payment_method": "upi"},
            rider_incident_summary={"total_incidents": 0, "verified_incidents": 0, "theft_claims": 0},
        )
        state = _make_state(ctx, message="My order 100 never arrived")
        result = assess_risk(state)
        assert result["risk_score"] >= 0.2
        assert any("non-delivery" in f.lower() or "delivered" in f.lower() for f in result["risk_flags"])

    def test_no_context_returns_zero_risk(self):
        state = _make_state(None)
        result = assess_risk(state)
        assert result["risk_score"] == 0.0
        assert result["risk_flags"] == []

    def test_score_clamps_at_one(self):
        """Even with multiple risk factors, score should not exceed 1.0."""
        ctx = _base_context(
            customer={
                "id": 2,
                "loyalty_tier": "bronze",
                "joined_at": "2026-04-10T00:00:00+00:00",  # 3 days old
            },
            customer_complaint_rate={
                "total_orders": 5,
                "total_complaints": 5,
                "rejected_complaints": 3,
                "complaint_rate": 1.0,
            },
            customer_recent_refunds=[
                {"amount_inr": 500} for _ in range(6)
            ],
            rider_incident_summary={"total_incidents": 0, "verified_incidents": 0, "theft_claims": 0},
        )
        result = assess_risk(_make_state(ctx, message="order 100 never arrived"))
        assert result["risk_score"] <= 1.0
