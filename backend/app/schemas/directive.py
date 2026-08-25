from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class DirectiveKind(str, enum.Enum):
    nudge = "nudge"


class DirectiveOrigin(str, enum.Enum):
    admin = "admin"
    risk_engine = "risk_engine"  # Phase 4 producer


class DirectiveStatus(str, enum.Enum):
    pending = "pending"
    delivered = "delivered"
    acknowledged = "acknowledged"
    applied = "applied"
    rejected = "rejected"
    expired = "expired"


class NudgeSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CoachingTag(str, enum.Enum):
    shortcut = "shortcut"
    automation = "automation"
    feature_tip = "feature_tip"
    wellbeing = "wellbeing"
    # Emitted by the risk engine for policy-violation nudges (Phase 4). Wire
    # value is part of the on-device contract: clients match on the string and
    # must tolerate tags they don't recognise.
    policy_violation = "policy_violation"


class NudgeCreateIn(BaseModel):
    """Admin-authored nudge. extra='forbid' + length caps because even admin
    free text is rendered on-device — treat as bounded display data (spec §5)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=280)
    severity: NudgeSeverity
    coaching_tag: CoachingTag


class DirectiveOut(BaseModel):
    id: str
    device_id: str
    kind: DirectiveKind
    origin: DirectiveOrigin
    status: DirectiveStatus
    payload: dict
    expires_at: str | None = None
    created_at: str
