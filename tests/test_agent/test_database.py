"""Unit tests for the database repository."""

import pytest


class TestDatabaseRepository:
    def test_get_order_returns_correct_data(self, db_repo):
        order = db_repo.get_order(100)
        assert order is not None
        assert order["id"] == 100
        assert order["customer_id"] == 1
        assert order["status"] == "delivered"
        assert order["total_inr"] == 850
        assert order["restaurant_name"] == "Spice Garden"
        assert order["rider_name"] == "Rajesh Kumar"

    def test_get_order_returns_none_for_missing(self, db_repo):
        assert db_repo.get_order(99999) is None

    def test_get_order_items(self, db_repo):
        items = db_repo.get_order_items(100)
        assert len(items) == 2
        names = {i["item_name"] for i in items}
        assert "Butter Chicken" in names
        assert "Naan" in names

    def test_get_customer(self, db_repo):
        customer = db_repo.get_customer(1)
        assert customer is not None
        assert customer["name"] == "Priya Sharma"
        assert customer["loyalty_tier"] == "gold"

    def test_get_customer_returns_none_for_missing(self, db_repo):
        assert db_repo.get_customer(99999) is None

    def test_get_customer_complaint_rate(self, db_repo):
        stats = db_repo.get_customer_complaint_rate(2)
        assert stats["total_complaints"] >= 4
        assert stats["rejected_complaints"] >= 3
        assert stats["complaint_rate"] > 0.5

    def test_get_customer_recent_refunds_empty(self, db_repo):
        refunds = db_repo.get_customer_recent_refunds(1)
        assert isinstance(refunds, list)

    def test_get_rider_incident_summary(self, db_repo):
        summary = db_repo.get_rider_incident_summary(2)
        assert summary["total_incidents"] == 2
        assert summary["verified_incidents"] == 1
        assert summary["theft_claims"] == 1

    def test_get_clean_rider_summary(self, db_repo):
        summary = db_repo.get_rider_incident_summary(1)
        assert summary["total_incidents"] == 0
        assert summary["verified_incidents"] == 0

    def test_build_order_context_returns_complete_context(self, db_repo):
        ctx = db_repo.build_order_context(100)
        assert "error" not in ctx
        assert ctx["order"]["id"] == 100
        assert len(ctx["order_items"]) == 2
        assert ctx["customer"]["name"] == "Priya Sharma"
        assert "customer_complaint_rate" in ctx
        assert "rider_incident_summary" in ctx
        assert "restaurant_rating_summary" in ctx

    def test_build_order_context_for_missing_order(self, db_repo):
        ctx = db_repo.build_order_context(99999)
        assert "error" in ctx

    def test_get_restaurant_rating_summary(self, db_repo):
        summary = db_repo.get_restaurant_rating_summary(1)
        assert "avg_rating" in summary
        assert "total_reviews" in summary
