"""
SQLite data access layer for the QuickBites support bot.

All queries are synchronous (SQLite + threading is fine for this use case).
Each method returns plain dicts so the rest of the codebase stays free of ORM
models and can be serialised to JSON without ceremony.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@contextmanager
def _get_connection(db_path: Path):
    """Yield a read-only SQLite connection with row-factory set."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


class DatabaseRepository:
    """All database queries needed by the support bot."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or get_settings().db_path

    # ------------------------------------------------------------------
    # Order queries
    # ------------------------------------------------------------------

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        """Fetch a single order with its restaurant and rider names."""
        sql = """
            SELECT
                o.id, o.customer_id, o.restaurant_id, o.rider_id,
                o.placed_at, o.delivered_at, o.status,
                o.subtotal_inr, o.delivery_fee_inr, o.total_inr,
                o.payment_method, o.promo_code, o.address,
                r.name  AS restaurant_name,
                r.cuisine AS restaurant_cuisine,
                ri.name AS rider_name,
                ri.phone AS rider_phone
            FROM orders o
            JOIN restaurants r  ON r.id  = o.restaurant_id
            LEFT JOIN riders ri ON ri.id = o.rider_id
            WHERE o.id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (order_id,)).fetchone()
        return _row_to_dict(row)

    def get_order_items(self, order_id: int) -> list[dict[str, Any]]:
        """Fetch all line items for an order."""
        sql = "SELECT item_name, qty, price_inr FROM order_items WHERE order_id = ? ORDER BY id"
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (order_id,)).fetchall()
        return _rows_to_dicts(rows)

    # ------------------------------------------------------------------
    # Customer queries
    # ------------------------------------------------------------------

    def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        """Fetch customer profile."""
        sql = """
            SELECT id, name, email, city, joined_at, loyalty_tier, wallet_balance_inr
            FROM customers WHERE id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (customer_id,)).fetchone()
        return _row_to_dict(row)

    def get_customer_order_stats(self, customer_id: int) -> dict[str, Any]:
        """Return aggregate order statistics for a customer."""
        sql = """
            SELECT
                COUNT(*)                              AS total_orders,
                SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered_orders,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_orders,
                MAX(placed_at)                        AS last_order_at
            FROM orders WHERE customer_id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (customer_id,)).fetchone()
        return dict(row) if row else {}

    def get_customer_complaints(self, customer_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Return a customer's complaint history (most recent first)."""
        sql = """
            SELECT id, order_id, target_type, raised_at, description,
                   status, resolution, resolution_amount_inr
            FROM complaints
            WHERE customer_id = ?
            ORDER BY raised_at DESC
            LIMIT ?
        """
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (customer_id, limit)).fetchall()
        return _rows_to_dicts(rows)

    def get_customer_complaint_rate(self, customer_id: int) -> dict[str, Any]:
        """Calculate the customer's overall complaint rate."""
        sql = """
            SELECT
                (SELECT COUNT(*) FROM orders WHERE customer_id = ?)         AS total_orders,
                (SELECT COUNT(*) FROM complaints WHERE customer_id = ?)      AS total_complaints,
                (SELECT COUNT(*) FROM complaints WHERE customer_id = ? AND status = 'rejected')
                                                                             AS rejected_complaints,
                CASE
                    WHEN (SELECT COUNT(*) FROM orders WHERE customer_id = ?) = 0 THEN 0.0
                    ELSE ROUND(
                        1.0 * (SELECT COUNT(*) FROM complaints WHERE customer_id = ?)
                            / (SELECT COUNT(*) FROM orders WHERE customer_id = ?),
                        3
                    )
                END AS complaint_rate
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(
                sql,
                (customer_id, customer_id, customer_id, customer_id, customer_id, customer_id),
            ).fetchone()
        return dict(row) if row else {}

    def get_customer_recent_refunds(self, customer_id: int, days: int = 30) -> list[dict[str, Any]]:
        """Return refunds issued to a customer in the last N days."""
        sql = """
            SELECT id, order_id, amount_inr, type, issued_at, reason
            FROM refunds
            WHERE customer_id = ?
              AND issued_at >= date('2026-04-13', ? || ' days')
            ORDER BY issued_at DESC
        """
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (customer_id, f"-{days}")).fetchall()
        return _rows_to_dicts(rows)

    def get_order_existing_refunds(self, order_id: int) -> list[dict[str, Any]]:
        """Return all refunds already issued for a specific order."""
        sql = """
            SELECT id, customer_id, amount_inr, type, issued_at, reason
            FROM refunds WHERE order_id = ?
            ORDER BY issued_at DESC
        """
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (order_id,)).fetchall()
        return _rows_to_dicts(rows)

    def get_order_existing_complaints(self, order_id: int) -> list[dict[str, Any]]:
        """Return complaints already filed against a specific order."""
        sql = """
            SELECT id, customer_id, target_type, raised_at, status, resolution
            FROM complaints WHERE order_id = ?
            ORDER BY raised_at DESC
        """
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (order_id,)).fetchall()
        return _rows_to_dicts(rows)

    # ------------------------------------------------------------------
    # Rider queries
    # ------------------------------------------------------------------

    def get_rider_incidents(self, rider_id: int) -> list[dict[str, Any]]:
        """Return all incidents for a rider."""
        sql = """
            SELECT id, order_id, type, reported_at, verified, notes
            FROM rider_incidents
            WHERE rider_id = ?
            ORDER BY reported_at DESC
        """
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(sql, (rider_id,)).fetchall()
        return _rows_to_dicts(rows)

    def get_rider_incident_summary(self, rider_id: int) -> dict[str, Any]:
        """Return aggregate incident counts for a rider."""
        sql = """
            SELECT
                COUNT(*)                                                       AS total_incidents,
                COALESCE(SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END), 0)   AS verified_incidents,
                COALESCE(SUM(CASE WHEN type = 'theft_claim' THEN 1 ELSE 0 END), 0) AS theft_claims,
                COALESCE(SUM(CASE WHEN type = 'rude' AND verified = 1 THEN 1 ELSE 0 END), 0) AS verified_rude,
                COALESCE(SUM(CASE WHEN type = 'late' THEN 1 ELSE 0 END), 0)  AS late_incidents,
                COALESCE(SUM(CASE WHEN type = 'damaged' THEN 1 ELSE 0 END), 0) AS damaged_incidents
            FROM rider_incidents
            WHERE rider_id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (rider_id,)).fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # Restaurant queries
    # ------------------------------------------------------------------

    def get_restaurant_rating_summary(self, restaurant_id: int) -> dict[str, Any]:
        """Return aggregate review statistics for a restaurant."""
        sql = """
            SELECT
                COUNT(*)             AS total_reviews,
                ROUND(AVG(rating), 2) AS avg_rating,
                SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS low_ratings,
                SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS high_ratings
            FROM reviews
            WHERE restaurant_id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (restaurant_id,)).fetchone()
        return dict(row) if row else {}

    def get_restaurant_complaint_summary(self, restaurant_id: int) -> dict[str, Any]:
        """Return aggregate complaint statistics for a restaurant."""
        sql = """
            SELECT
                COUNT(*)   AS total_complaints,
                SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM complaints
            WHERE target_type = 'restaurant' AND target_id = ?
        """
        with _get_connection(self._db_path) as conn:
            row = conn.execute(sql, (restaurant_id,)).fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # Composite context builder
    # ------------------------------------------------------------------

    def build_order_context(self, order_id: int) -> dict[str, Any]:
        """
        Build the full context dict for a given order.

        Assembles order + items + customer profile + stats + rider history +
        restaurant ratings into a single dict ready for the agent.
        """
        order = self.get_order(order_id)
        if not order:
            return {"error": f"Order {order_id} not found in database"}

        customer_id = order["customer_id"]
        rider_id = order.get("rider_id")
        restaurant_id = order["restaurant_id"]

        items = self.get_order_items(order_id)
        customer = self.get_customer(customer_id)
        customer_stats = self.get_customer_order_stats(customer_id)
        complaint_rate = self.get_customer_complaint_rate(customer_id)
        recent_refunds = self.get_customer_recent_refunds(customer_id)
        recent_complaints = self.get_customer_complaints(customer_id, limit=10)
        order_refunds = self.get_order_existing_refunds(order_id)
        order_complaints = self.get_order_existing_complaints(order_id)
        restaurant_ratings = self.get_restaurant_rating_summary(restaurant_id)
        restaurant_complaints = self.get_restaurant_complaint_summary(restaurant_id)

        rider_incidents: list[dict] = []
        rider_incident_summary: dict = {}
        if rider_id:
            rider_incidents = self.get_rider_incidents(rider_id)
            rider_incident_summary = self.get_rider_incident_summary(rider_id)

        return {
            "order": order,
            "order_items": items,
            "customer": customer,
            "customer_stats": customer_stats,
            "customer_complaint_rate": complaint_rate,
            "customer_recent_refunds": recent_refunds,
            "customer_recent_complaints": recent_complaints,
            "order_existing_refunds": order_refunds,
            "order_existing_complaints": order_complaints,
            "restaurant_rating_summary": restaurant_ratings,
            "restaurant_complaint_summary": restaurant_complaints,
            "rider_incidents": rider_incidents,
            "rider_incident_summary": rider_incident_summary,
        }
