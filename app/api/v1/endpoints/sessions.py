"""
Session management endpoints.

POST /api/v1/session/run
    Start a new support bot session against the simulator and run it to
    completion. Returns the full conversation transcript and any actions taken.

GET  /api/v1/session/{session_id}/summary
    Fetch the candidate's aggregate prod score.
"""

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel

from app.core.logging import get_logger
from app.schemas.session import RunSessionRequest, SessionResult, SessionStatusResponse
from app.services.simulator import SessionRunner, SimulatorClient

logger = get_logger(__name__)
router = APIRouter(prefix="/session", tags=["sessions"])

# In-memory store for async background runs (session_id → result/error)
_session_store: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Synchronous run (waits for completion — suitable for short sessions)
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=SessionResult,
    status_code=status.HTTP_200_OK,
    summary="Run a complete support session",
)
async def run_session(request: RunSessionRequest) -> SessionResult:
    """
    Start a new session with the simulator and drive the conversation to
    completion. The response contains the full transcript and score (prod only).

    - **mode**: `dev` for unscored rehearsal; `prod` when ready for evaluation.
    - **scenario_id**: Dev mode only — pin to a specific rehearsal scenario (101–105).
    """
    runner = SessionRunner()
    try:
        result = await runner.run(request)
    except httpx.HTTPStatusError as exc:
        logger.error("Simulator HTTP error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Simulator returned {exc.response.status_code}: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        logger.error("Simulator connection error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach simulator: {exc}",
        )
    return result


# ---------------------------------------------------------------------------
# Async background run (fire-and-forget for long sessions)
# ---------------------------------------------------------------------------


async def _run_background(session_key: str, request: RunSessionRequest) -> None:
    """Background task: run session and store result."""
    runner = SessionRunner()
    try:
        result = await runner.run(request)
        _session_store[session_key] = {"status": "completed", "result": result}
    except Exception as exc:
        logger.error("Background session failed: %s", exc)
        _session_store[session_key] = {"status": "failed", "error": str(exc)}


@router.post(
    "/run-async",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a session asynchronously",
)
async def run_session_async(
    request: RunSessionRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Queue a session to run in the background. Poll `GET /session/{key}/status`
    for the result. Useful when you want non-blocking prod runs.
    """
    import uuid

    key = str(uuid.uuid4())
    _session_store[key] = {"status": "running"}
    background_tasks.add_task(_run_background, key, request)
    return {"session_key": key, "status": "running"}


@router.get(
    "/{session_key}/status",
    response_model=SessionStatusResponse,
    summary="Poll async session status",
)
async def get_session_status(session_key: str) -> SessionStatusResponse:
    """Return the status of an async session started with /run-async."""
    entry = _session_store.get(session_key)
    if not entry:
        raise HTTPException(status_code=404, detail="Session key not found")
    return SessionStatusResponse(
        session_id=session_key,
        status=entry["status"],
        result=entry.get("result"),
        error=entry.get("error"),
    )


# ---------------------------------------------------------------------------
# Candidate summary (prod scores)
# ---------------------------------------------------------------------------


@router.get(
    "/candidate/summary",
    summary="Fetch candidate's aggregate prod score",
)
async def candidate_summary() -> dict:
    """Proxy the simulator's candidate summary endpoint."""
    client = SimulatorClient()
    try:
        return await client.get_candidate_summary()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        )
