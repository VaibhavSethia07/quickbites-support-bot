from typing import Any, Literal

from pydantic import BaseModel, Field


class RunSessionRequest(BaseModel):
    """Request to start a new support bot session against the simulator."""

    mode: Literal["dev", "prod"] = "dev"
    scenario_id: int | None = Field(
        default=None,
        description="Dev mode only: specific rehearsal scenario (101–105). Omit for random.",
    )


class TurnRecord(BaseModel):
    """A single conversation turn (customer + bot)."""

    turn: int
    customer_message: str
    bot_message: str
    actions: list[dict[str, Any]]


class SessionResult(BaseModel):
    """Final result of a completed support bot session."""

    session_id: str
    mode: str
    scenario_id: int | None
    turns: list[TurnRecord]
    close_reason: str | None
    score: Any | None = None  # populated in prod mode


class SessionStatusResponse(BaseModel):
    """Response for GET /api/v1/session/{session_id}."""

    session_id: str
    status: Literal["running", "completed", "failed"]
    result: SessionResult | None = None
    error: str | None = None
