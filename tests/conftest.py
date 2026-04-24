"""
Shared test fixtures and helpers.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory) -> Path:
    """
    Create an in-memory-backed SQLite database with the QuickBites schema
    and minimal seed data for unit tests.
    """
    tmp = tmp_path_factory.mktemp("db")
    db_path = tmp / "test.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            email TEXT,
            city TEXT,
            joined_at TEXT,
            loyalty_tier TEXT,
            wallet_balance_inr INTEGER
        );

        CREATE TABLE restaurants (
            id INTEGER PRIMARY KEY,
            name TEXT,
            cuisine TEXT,
            city TEXT,
            area TEXT,
            joined_at TEXT
        );

        CREATE TABLE riders (
            id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            city TEXT,
            joined_at TEXT
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            restaurant_id INTEGER,
            rider_id INTEGER,
            placed_at TEXT,
            delivered_at TEXT,
            status TEXT,
            subtotal_inr INTEGER,
            delivery_fee_inr INTEGER,
            total_inr INTEGER,
            payment_method TEXT,
            promo_code TEXT,
            address TEXT
        );

        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER,
            item_name TEXT,
            qty INTEGER,
            price_inr INTEGER
        );

        CREATE TABLE complaints (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_id INTEGER,
            target_type TEXT,
            target_id INTEGER,
            raised_at TEXT,
            description TEXT,
            status TEXT,
            resolution TEXT,
            resolution_amount_inr INTEGER
        );

        CREATE TABLE refunds (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_id INTEGER,
            amount_inr INTEGER,
            type TEXT,
            issued_at TEXT,
            reason TEXT
        );

        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_id INTEGER,
            restaurant_id INTEGER,
            rating INTEGER,
            comment TEXT,
            created_at TEXT
        );

        CREATE TABLE rider_incidents (
            id INTEGER PRIMARY KEY,
            rider_id INTEGER,
            order_id INTEGER,
            type TEXT,
            reported_at TEXT,
            verified INTEGER,
            notes TEXT
        );

        -- Good customer
        INSERT INTO customers VALUES (1, 'Priya Sharma', '+919876543210', 'priya@example.com',
            'Mumbai', '2024-01-15T10:00:00+00:00', 'gold', 500);

        -- High-risk customer (high complaint rate)
        INSERT INTO customers VALUES (2, 'Aarav Banerjee', '+919876543211', 'aarav@example.com',
            'Delhi', '2025-12-01T10:00:00+00:00', 'bronze', 0);

        INSERT INTO restaurants VALUES (1, 'Spice Garden', 'Indian', 'Mumbai', 'Andheri',
            '2023-01-01T00:00:00+00:00');

        INSERT INTO riders VALUES (1, 'Rajesh Kumar', '+919876543212', 'Mumbai',
            '2023-06-01T00:00:00+00:00');
        INSERT INTO riders VALUES (2, 'Bad Rider', '+919876543213', 'Delhi',
            '2024-01-01T00:00:00+00:00');

        -- Normal delivered order
        INSERT INTO orders VALUES (
            100, 1, 1, 1,
            '2026-04-10T12:00:00+00:00', '2026-04-10T12:45:00+00:00',
            'delivered', 800, 50, 850, 'upi', NULL, '123 Main St'
        );
        INSERT INTO order_items VALUES (1, 100, 'Butter Chicken', 1, 500);
        INSERT INTO order_items VALUES (2, 100, 'Naan', 2, 150);

        -- Order for high-risk customer
        INSERT INTO orders VALUES (
            101, 2, 1, 2,
            '2026-04-11T14:00:00+00:00', '2026-04-11T14:50:00+00:00',
            'delivered', 1200, 60, 1260, 'card', NULL, '456 Other St'
        );
        INSERT INTO order_items VALUES (3, 101, 'Biryani', 2, 600);

        -- Complaints for high-risk customer (many rejected)
        INSERT INTO complaints VALUES (1, 2, 90, 'restaurant', 1, '2026-03-01T00:00:00+00:00',
            'Wrong order', 'rejected', 'none', 0);
        INSERT INTO complaints VALUES (2, 2, 91, 'restaurant', 1, '2026-03-10T00:00:00+00:00',
            'Missing item', 'rejected', 'none', 0);
        INSERT INTO complaints VALUES (3, 2, 92, 'rider', 2, '2026-03-20T00:00:00+00:00',
            'Theft claim', 'rejected', 'none', 0);
        INSERT INTO complaints VALUES (4, 2, 93, 'restaurant', 1, '2026-04-01T00:00:00+00:00',
            'Cold food', 'open', 'none', 0);

        -- Rider incidents for bad rider
        INSERT INTO rider_incidents VALUES (1, 2, 95, 'theft_claim', '2026-03-15T00:00:00+00:00', 0, 'Customer claims theft');
        INSERT INTO rider_incidents VALUES (2, 2, 96, 'rude', '2026-03-20T00:00:00+00:00', 1, 'Verified rude behavior');

        -- Reviews for restaurant
        INSERT INTO reviews VALUES (1, 1, 100, 1, 4, 'Good food', '2026-04-10T13:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def db_repo(test_db_path):
    """DatabaseRepository pointing at the test database."""
    from app.repositories.database import DatabaseRepository
    return DatabaseRepository(db_path=test_db_path)


@pytest.fixture
def app_client():
    """FastAPI test client with mocked settings."""
    from unittest.mock import patch

    from main import app

    with patch("app.core.config.get_settings") as mock_settings:
        from app.core.config import Settings
        mock_settings.return_value = Settings(
            anthropic_api_key="test-key",
            simulator_base_url="http://test-simulator",
            candidate_token="test-token",
            database_path="app.db",
        )
        with TestClient(app) as client:
            yield client
