"""Pydantic schemas for the survey engine.

Three groups:

  - SurveyTemplate* — read-only for v0.1 (only the foundation default
    is supported; customer-authored templates land in v0.2). Listed
    so the frontend can populate the template dropdown.
  - SurveyDelivery* — POST to dispatch; GET to list with completion
    counts.
  - SurveyResponse* — GET to inspect; POST .../answer to submit one
    question's answer (the bot's primary interaction); DELETE for
    GDPR erasure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.survey import (
    DeliveryChannel,
    DeliveryStatus,
    ResponseStatus,
)

# ─── Templates ───────────────────────────────────────────────────────


class SurveyTemplateResponse(BaseModel):
    id: str
    tenant_id: str
    slug: str
    name: str
    version: str
    description: str | None
    questions: list[dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SurveyTemplateListResponse(BaseModel):
    templates: list[SurveyTemplateResponse]
    total: int


# ─── Deliveries ──────────────────────────────────────────────────────


class AudienceFilter(BaseModel):
    """Audience filter shape stored as JSON on the delivery row.

    Matches the frontend modal in /dashboard/registry (PR #25).
    """

    mode: Literal["all", "departments", "tools", "custom"]
    values: list[str] = Field(default_factory=list)

    @field_validator("values")
    @classmethod
    def _normalise(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]


class DispatchCreate(BaseModel):
    """POST /api/v1/surveys/dispatches body."""

    # Use slug+version for forward-compat as templates evolve.
    # In v0.1 only `foundation-use-case@v1` exists.
    template_slug: str = Field(..., min_length=1, max_length=100)
    template_version: str = Field("v1", min_length=1, max_length=20)
    audience: AudienceFilter
    channel: DeliveryChannel = DeliveryChannel.SLACK
    # Optional human label; defaults server-side to "<template name> — <date>".
    name: str | None = Field(None, max_length=255)


class DispatchResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    template_slug: str
    template_version: str
    name: str
    audience_label: str
    audience: AudienceFilter
    channel: DeliveryChannel
    status: DeliveryStatus
    sent_by_user_id: str | None
    recipient_count: int
    completed_count: int
    new_registration_count: int
    created_at: datetime
    dispatched_at: datetime | None
    completed_at: datetime | None


class DispatchListResponse(BaseModel):
    dispatches: list[DispatchResponse]
    total: int
    page: int
    page_size: int


# ─── Responses ───────────────────────────────────────────────────────


class SurveyResponseRecord(BaseModel):
    """A single recipient's response state + collected answers."""

    id: str
    tenant_id: str
    delivery_id: str
    user_id: str | None
    slack_user_id: str | None
    recipient_email: str | None
    status: ResponseStatus
    answers: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    registered_as_use_case_id: str | None
    created_at: datetime
    updated_at: datetime


class SurveyResponseListResponse(BaseModel):
    responses: list[SurveyResponseRecord]
    total: int
    page: int
    page_size: int


class AnswerSubmit(BaseModel):
    """POST /api/v1/surveys/responses/{id}/answer body.

    The bot DM interaction handler (PR C) calls this on every Block
    Kit submission. The value type depends on the question type:
      - yesNo / singleSelect → string
      - multiSelect          → list[string]
      - freeText             → string (or null to skip if optional)
    """

    question_id: str = Field(..., min_length=1, max_length=100)
    value: Any  # validated against the question schema by survey_flow


class NextQuestionResponse(BaseModel):
    """Result of submit-answer or peek-next.

    If `is_complete`, the survey is done — no further question. The
    bot DM should send the closing "thanks" message.
    """

    response_id: str
    is_complete: bool
    next_question: dict[str, Any] | None
    answered_count: int
    total_applicable: int
