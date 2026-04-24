"""
Simulator API client and session runner.

The runner drives the full conversation loop:
  1. POST /v1/session/start  → get first customer message
  2. Run LangGraph agent turn
  3. POST /v1/session/{id}/reply  → send bot reply + actions
  4. Repeat until done

All HTTP calls use httpx with a configurable timeout.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.agent.graph import run_agent_turn
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.session import RunSessionRequest, SessionResult, TurnRecord

logger = get_logger(__name__)


class SimulatorClient:
    """Thin async client wrapping the simulator REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.simulator_base_url).rstrip("/")
        self._token = token or settings.candidate_token
        self._timeout = timeout or settings.request_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-Candidate-Token"] = self._token
        return headers

    async def start_session(
        self, mode: str = "dev", scenario_id: int | None = None
    ) -> dict[str, Any]:
        """Open a new chat session with the simulator."""
        payload: dict[str, Any] = {"mode": mode}
        if scenario_id is not None and mode == "dev":
            payload["scenario_id"] = scenario_id

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/session/start",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def reply(
        self,
        session_id: str,
        bot_message: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Send the bot's reply and actions; receive the customer's next message."""
        payload = {"bot_message": bot_message, "actions": actions}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/session/{session_id}/reply",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_transcript(self, session_id: str) -> dict[str, Any]:
        """Fetch the session transcript (dev mode only)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base_url}/v1/session/{session_id}/transcript",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_candidate_summary(self) -> dict[str, Any]:
        """Fetch the candidate's aggregate prod score."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._base_url}/v1/candidate/summary",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def healthz(self) -> dict[str, Any]:
        """Check simulator liveness."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self._base_url}/healthz")
            response.raise_for_status()
            return response.json()


class SessionRunner:
    """
    Drives a complete support conversation session.

    Maintains per-session agent state across turns so context (order_id,
    customer profile, risk score) is preserved without re-fetching.
    """

    def __init__(self, simulator: SimulatorClient | None = None) -> None:
        self._sim = simulator or SimulatorClient()

    async def run(self, request: RunSessionRequest) -> SessionResult:
        """Run a full session from start to close."""
        settings = get_settings()

        # ------------------------------------------------------------------
        # 1. Start the session
        # ------------------------------------------------------------------
        start_data = await self._sim.start_session(
            mode=request.mode, scenario_id=request.scenario_id
        )

        session_id = start_data["session_id"]
        scenario_id = start_data.get("scenario_id")
        mode = start_data.get("mode", request.mode)
        max_turns = start_data.get("max_turns", settings.max_turns)

        logger.info(
            "Session %s started (mode=%s, scenario=%s, max_turns=%d)",
            session_id, mode, scenario_id, max_turns,
        )

        # ------------------------------------------------------------------
        # 2. Conversation loop
        # ------------------------------------------------------------------
        turns: list[TurnRecord] = []
        agent_state: dict[str, Any] | None = None
        customer_message = start_data["customer_message"]
        close_reason: str | None = None
        score: Any = None

        for turn_num in range(1, max_turns + 1):
            logger.info("Turn %d/%d | Customer: %.100s…", turn_num, max_turns, customer_message)

            # Run one agent turn (synchronous; run in thread pool to not block event loop)
            agent_state = await asyncio.get_event_loop().run_in_executor(
                None,
                run_agent_turn,
                session_id,
                mode,
                customer_message,
                turn_num,
                agent_state,
            )

            bot_message = agent_state.get("bot_message", "")
            actions = agent_state.get("actions", [])

            logger.info(
                "Turn %d | Bot: %.100s… | Actions: %s",
                turn_num,
                bot_message,
                [a.get("type") for a in actions],
            )

            # Record this turn
            turns.append(
                TurnRecord(
                    turn=turn_num,
                    customer_message=customer_message,
                    bot_message=bot_message,
                    actions=actions,
                )
            )

            # ------------------------------------------------------------------
            # 3. Send reply to simulator
            # ------------------------------------------------------------------
            reply_data = await self._sim.reply(
                session_id=session_id,
                bot_message=bot_message,
                actions=actions,
            )

            done = reply_data.get("done", False)
            close_reason = reply_data.get("close_reason")
            score = reply_data.get("score")
            next_message = reply_data.get("customer_message")

            if done:
                logger.info(
                    "Session %s ended (close_reason=%s, score=%s)",
                    session_id, close_reason, score,
                )
                break

            if next_message is None:
                # Simulator ended unexpectedly
                logger.warning("Session %s: next message is None but done=False", session_id)
                break

            customer_message = next_message

        else:
            # Turn cap reached without clean close
            logger.warning("Session %s hit turn cap (%d)", session_id, max_turns)

        return SessionResult(
            session_id=session_id,
            mode=mode,
            scenario_id=scenario_id,
            turns=turns,
            close_reason=close_reason,
            score=score,
        )
