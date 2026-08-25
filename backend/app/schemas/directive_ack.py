from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict


class AckOutcome(str, enum.Enum):
    shown = "shown"
    accepted = "accepted"
    applied = "applied"
    rejected = "rejected"


class DirectiveAckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: AckOutcome


class DirectiveAckOut(BaseModel):
    directive_id: str
    status: str
    outcome: str
