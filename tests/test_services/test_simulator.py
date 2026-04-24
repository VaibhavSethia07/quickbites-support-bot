"""Unit tests for the simulator client (no real HTTP calls)."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def sim_client():
    from app.services.simulator import SimulatorClient
    return SimulatorClient(
        base_url="http://test-simulator",
        token="test-token",
        timeout=5,
    )


class TestSimulatorClient:
    @pytest.mark.asyncio
    async def test_start_session_sends_correct_payload(self, sim_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session_id": "abc123",
            "mode": "dev",
            "scenario_id": 101,
            "customer_message": "My order is missing!",
            "max_turns": 8,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await sim_client.start_session(mode="dev", scenario_id=101)

        assert result["session_id"] == "abc123"
        assert result["customer_message"] == "My order is missing!"
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["mode"] == "dev"
        assert call_kwargs.kwargs["json"]["scenario_id"] == 101

    @pytest.mark.asyncio
    async def test_reply_sends_bot_message_and_actions(self, sim_client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "customer_message": "Thanks!",
            "done": False,
            "close_reason": None,
            "score": None,
            "turns_remaining": 6,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            actions = [{"type": "file_complaint", "order_id": 100, "target_type": "restaurant"}]
            result = await sim_client.reply("abc123", "I'm looking into it.", actions)

        assert result["customer_message"] == "Thanks!"
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["bot_message"] == "I'm looking into it."
        assert payload["actions"] == actions

    @pytest.mark.asyncio
    async def test_start_session_dev_omits_scenario_for_prod(self, sim_client):
        """Scenario ID should not be sent in prod mode."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session_id": "xyz",
            "mode": "prod",
            "scenario_id": 1,
            "customer_message": "...",
            "max_turns": 8,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            result = await sim_client.start_session(mode="prod", scenario_id=999)

        payload = mock_client.post.call_args.kwargs["json"]
        assert "scenario_id" not in payload
        assert payload["mode"] == "prod"


class TestSchemas:
    """Test that Pydantic schemas validate and reject correctly."""

    def test_issue_refund_valid(self):
        from app.schemas.actions import IssueRefund
        a = IssueRefund(order_id=100, amount_inr=200, method="wallet_credit")
        assert a.type == "issue_refund"
        assert a.amount_inr == 200

    def test_issue_refund_rejects_zero_amount(self):
        from pydantic import ValidationError
        from app.schemas.actions import IssueRefund
        with pytest.raises(ValidationError):
            IssueRefund(order_id=100, amount_inr=0, method="cash")

    def test_file_complaint_valid(self):
        from app.schemas.actions import FileComplaint
        a = FileComplaint(order_id=100, target_type="rider")
        assert a.type == "file_complaint"

    def test_close_requires_outcome_summary(self):
        from pydantic import ValidationError
        from app.schemas.actions import CloseSession
        with pytest.raises(ValidationError):
            CloseSession()

    def test_parse_action_dispatches_correctly(self):
        from app.schemas.actions import parse_action, IssueRefund, FileComplaint
        refund = parse_action({"type": "issue_refund", "order_id": 1, "amount_inr": 100, "method": "cash"})
        assert isinstance(refund, IssueRefund)

        complaint = parse_action({"type": "file_complaint", "order_id": 1, "target_type": "app"})
        assert isinstance(complaint, FileComplaint)

    def test_parse_action_raises_for_unknown_type(self):
        from app.schemas.actions import parse_action
        with pytest.raises(ValueError):
            parse_action({"type": "give_cash", "amount": 500})
