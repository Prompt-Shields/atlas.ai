"""Pydantic schemas for the voice-interview intake endpoint (#242).

Wire format for `POST /discover/voice-interview/extract`: the frontend
submits the scripted interview transcript it just played back; the
service runs a deterministic keyword extractor over each employee turn
and persists a `UseCase` draft (`status=DRAFT`, `source=VOICE_INTERVIEW`)
per detected tool mention — see `services/voice_interview_service.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.use_case import UseCaseResponse


class VoiceInterviewTurn(BaseModel):
    """One line of the played-back agent/employee transcript."""

    speaker: str = Field(..., max_length=100, description="Display name of the speaker")
    role: str = Field(..., pattern="^(agent|employee)$")
    text: str = Field(..., min_length=1, max_length=2000)
    department: str | None = Field(None, max_length=255)


class VoiceInterviewExtractRequest(BaseModel):
    transcript: list[VoiceInterviewTurn] = Field(..., min_length=1)
    default_department: str | None = Field(None, max_length=255)


class VoiceInterviewExtractResponse(BaseModel):
    """Result of an extraction — the drafts just written to the registry."""

    drafts: list[UseCaseResponse] = Field(default_factory=list)
    created_count: int
