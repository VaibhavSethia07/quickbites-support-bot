"""Integration tests for the sessions API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Return a test client with mocked settings and simulator."""
    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    os.environ.setdefault("CANDIDATE_TOKEN", "test-token")

    from main import app
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_healthz_returns_ok(self, client):
        response = client.get("/api/v1/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "quickbites" in data["service"].lower()


class TestRunSession:
    def test_run_session_bad_mode_rejected(self, client):
        """Invalid mode should return 422."""
        response = client.post("/api/v1/session/run", json={"mode": "staging"})
        assert response.status_code == 422

    def test_run_session_simulator_unavailable(self, client):
        """Should return 502/503 when simulator is unreachable."""
        import httpx

        with patch(
            "app.services.simulator.SimulatorClient.start_session",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            response = client.post("/api/v1/session/run", json={"mode": "dev"})
        assert response.status_code in (502, 503)

    def test_run_session_success(self, client):
        """A full session should return a SessionResult."""
        from app.schemas.session import SessionResult

        mock_result = SessionResult(
            session_id="test-session",
            mode="dev",
            scenario_id=101,
            turns=[],
            close_reason="bot_closed",
            score=None,
        )

        with patch(
            "app.services.simulator.SessionRunner.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = client.post("/api/v1/session/run", json={"mode": "dev"})

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session"
        assert data["mode"] == "dev"


class TestAsyncRunSession:
    def test_run_async_returns_202(self, client):
        response = client.post("/api/v1/session/run-async", json={"mode": "dev"})
        assert response.status_code == 202
        data = response.json()
        assert "session_key" in data
        assert data["status"] == "running"

    def test_get_status_returns_404_for_unknown(self, client):
        response = client.get("/api/v1/session/nonexistent-key/status")
        assert response.status_code == 404
