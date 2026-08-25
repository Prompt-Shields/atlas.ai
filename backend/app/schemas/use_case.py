"""UseCase Pydantic schemas — request bodies + list/detail responses.

The schemas mirror `app.models.use_case.UseCase` but flatten the
JSON-as-Text `data_classes` into a native `list[str]` for clients. The
router does the serialisation roundtrip via `_safe_json_loads` /
`json.dumps` so consumers never see a JSON string.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.use_case import (
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)

# ─── Common fields ───────────────────────────────────────────────────


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# ─── Create ──────────────────────────────────────────────────────────


class UseCaseCreate(BaseModel):
    """Payload for POST /api/v1/use-cases.

    `source` is required because it tells us how the record arrived
    (form / bot / shadow-promote). The frontend form sets `FORM`. PR
    B's survey-completion service sets `BOT`. The Discover-page
    promotion handler sets `SHADOW_PROMOTE`.
    """

    title: str = Field(..., min_length=1, max_length=255)
    tool: str = Field(..., min_length=1, max_length=100)
    department: str = Field(..., min_length=1, max_length=255)
    owner_user_id: uuid.UUID | None = None
    status: UseCaseStatus = UseCaseStatus.DRAFT
    risk_tier: UseCaseRiskTier = UseCaseRiskTier.LOW
    source: UseCaseSource
    data_classes: list[str] = Field(default_factory=list)
    frequency: str | None = Field(None, max_length=100)
    notes: str | None = None
    dispatched_from_response_id: uuid.UUID | None = None

    @field_validator("title", "tool", "department")
    @classmethod
    def _trim_required(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be empty after trimming")
        return trimmed

    @field_validator("frequency", "notes")
    @classmethod
    def _trim_optional(cls, value: str | None) -> str | None:
        return _strip_or_none(value)

    @field_validator("data_classes")
    @classmethod
    def _normalise_data_classes(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalised: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("data_classes entries must be strings")
            cleaned = item.strip().lower()
            if not cleaned:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                normalised.append(cleaned)
        return normalised


# ─── Update ──────────────────────────────────────────────────────────


class UseCaseUpdate(BaseModel):
    """Partial update for PATCH /api/v1/use-cases/{id}.

    All fields optional. The router applies provided fields only —
    setting a field to null *does* clear it (so PATCH with
    `{"owner_user_id": null}` unassigns the owner).
    """

    title: str | None = Field(None, min_length=1, max_length=255)
    tool: str | None = Field(None, min_length=1, max_length=100)
    department: str | None = Field(None, min_length=1, max_length=255)
    owner_user_id: uuid.UUID | None = None
    status: UseCaseStatus | None = None
    risk_tier: UseCaseRiskTier | None = None
    data_classes: list[str] | None = None
    frequency: str | None = Field(None, max_length=100)
    notes: str | None = None

    @field_validator("title", "tool", "department")
    @classmethod
    def _trim_required(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be empty after trimming")
        return trimmed

    @field_validator("frequency", "notes")
    @classmethod
    def _trim_optional(cls, value: str | None) -> str | None:
        return _strip_or_none(value)

    @field_validator("data_classes")
    @classmethod
    def _normalise_data_classes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        normalised: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("data_classes entries must be strings")
            cleaned = item.strip().lower()
            if not cleaned:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                normalised.append(cleaned)
        return normalised


# ─── Response ────────────────────────────────────────────────────────


class UseCaseResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    tool: str
    department: str
    owner_user_id: str | None
    status: UseCaseStatus
    risk_tier: UseCaseRiskTier
    source: UseCaseSource
    data_classes: list[str]
    frequency: str | None
    notes: str | None
    dispatched_from_response_id: str | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class UseCaseListResponse(BaseModel):
    use_cases: list[UseCaseResponse]
    total: int
    page: int
    page_size: int


class UseCaseCounts(BaseModel):
    """Counts grouped by status — feeds the registry header summary."""

    total: int
    draft: int
    review: int
    active: int
    retired: int


class UseCasePromoteResponse(BaseModel):
    use_case_id: str
    asset_id: str
    ai_use_case_id: str
    created: bool
