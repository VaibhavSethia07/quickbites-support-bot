from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


class IssueRefund(BaseModel):
    """Issue a cash or wallet-credit refund for an order."""

    type: Literal["issue_refund"] = "issue_refund"
    order_id: int
    amount_inr: int = Field(gt=0, description="Refund amount in INR (positive integer)")
    method: Literal["cash", "wallet_credit"]


class FileComplaint(BaseModel):
    """File a formal complaint against a restaurant, rider, or the app."""

    type: Literal["file_complaint"] = "file_complaint"
    order_id: int
    target_type: Literal["restaurant", "rider", "app"]


class EscalateToHuman(BaseModel):
    """Escalate the conversation to a human support agent."""

    type: Literal["escalate_to_human"] = "escalate_to_human"
    reason: str = Field(min_length=5, max_length=500)


class FlagAbuse(BaseModel):
    """Flag a customer for abusive or fraudulent behavior."""

    type: Literal["flag_abuse"] = "flag_abuse"
    reason: str = Field(min_length=5, max_length=500)


class CloseSession(BaseModel):
    """Close the chat session cleanly."""

    type: Literal["close"] = "close"
    outcome_summary: str = Field(min_length=5, max_length=500)


# Discriminated union of all action types
Action = Annotated[
    Union[IssueRefund, FileComplaint, EscalateToHuman, FlagAbuse, CloseSession],
    Field(discriminator="type"),
]


def parse_action(raw: dict) -> Action:
    """Parse a raw dict into a typed action, raising ValueError on invalid input."""
    action_type = raw.get("type")
    type_map = {
        "issue_refund": IssueRefund,
        "file_complaint": FileComplaint,
        "escalate_to_human": EscalateToHuman,
        "flag_abuse": FlagAbuse,
        "close": CloseSession,
    }
    if action_type not in type_map:
        raise ValueError(f"Unknown action type: {action_type!r}")
    return type_map[action_type].model_validate(raw)
