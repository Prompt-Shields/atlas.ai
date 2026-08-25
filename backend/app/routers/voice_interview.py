"""Voice-interview intake router — `/api/v1/discover/voice-interview` (M3, issue #242).

  POST /discover/voice-interview/extract   OrgAdmin   parse a scripted
                                                        interview transcript
                                                        into use-case drafts

The frontend plays back a canned agent/employee transcript
(`lib/aispm/voice-interview.ts`) then submits it here; extraction is a
deterministic keyword match (see `services/voice_interview_service.py`)
that lands each hit directly in the use-case registry as a pending
(`DRAFT`) `UseCase`, `source=VOICE_INTERVIEW`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.schemas.voice_interview import (
    VoiceInterviewExtractRequest,
    VoiceInterviewExtractResponse,
)
from app.services import voice_interview_service

router = APIRouter(prefix="/discover/voice-interview", tags=["Voice interview intake"])


@router.post("/extract", response_model=VoiceInterviewExtractResponse)
async def extract_voice_interview(
    payload: VoiceInterviewExtractRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> VoiceInterviewExtractResponse:
    """Extract use-case drafts from a played-back interview transcript."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot extract use cases")
    return await voice_interview_service.extract_use_cases(db, user.tenant_id, payload)
