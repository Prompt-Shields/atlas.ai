"""Voice-interview transcript → use-case draft extraction (#242).

Ports the AI-SPM "voice-interview" prototype's scripted transcript → use
case pipeline: `lib/voice-interview-data.ts` plays back a canned
agent/employee interview client-side, then a client-only extractor turns
it into use-case drafts. Here that extraction lands server-side and
persists real `UseCase` rows.

`extract_use_cases()` is intentionally a keyword-matched heuristic (the
"seeded extractor" the issue calls for) rather than an LLM call: every
employee turn is scanned against `KNOWN_AI_TOOLS`; each match writes one
draft, `status=DRAFT` / `source=VOICE_INTERVIEW`, landing directly in the
use-case registry for IT-lead review — the same DRAFT queue `BOT` and
`FORM` submissions land in.
"""

from __future__ import annotations

import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.use_case import UseCase, UseCaseSource, UseCaseStatus
from app.schemas.use_case import UseCaseResponse
from app.schemas.voice_interview import (
    VoiceInterviewExtractRequest,
    VoiceInterviewExtractResponse,
)

# Ordered longest-first so "GitHub Copilot" matches before a bare "Copilot".
KNOWN_AI_TOOLS: list[str] = [
    "GitHub Copilot",
    "Microsoft Copilot",
    "Notion AI",
    "Otter.ai",
    "Midjourney",
    "Grammarly",
    "ChatGPT",
    "Claude",
    "Gemini",
    "Jasper",
    "DALL-E",
]

_PURPOSE_RE = re.compile(r"\b(?:to|for)\s+(.+?)[.?!]?$", re.IGNORECASE)


def _find_tool(text: str) -> str | None:
    lowered = text.lower()
    for tool in KNOWN_AI_TOOLS:
        if tool.lower() in lowered:
            return tool
    return None


def _title_for(tool: str, text: str) -> str:
    match = _PURPOSE_RE.search(text)
    if match:
        purpose = match.group(1).strip()
        if purpose:
            return f"{tool} — {purpose[0].upper()}{purpose[1:]}"
    return tool


def _serialise(record: UseCase) -> UseCaseResponse:
    return UseCaseResponse(
        id=str(record.id),
        tenant_id=str(record.tenant_id),
        title=record.title,
        tool=record.tool,
        department=record.department,
        owner_user_id=str(record.owner_user_id) if record.owner_user_id else None,
        status=record.status,
        risk_tier=record.risk_tier,
        source=record.source,
        data_classes=json.loads(record.data_classes or "[]"),
        frequency=record.frequency,
        notes=record.notes,
        dispatched_from_response_id=(
            str(record.dispatched_from_response_id) if record.dispatched_from_response_id else None
        ),
        created_at=record.created_at,
        updated_at=record.updated_at,
        is_test_data=record.is_test_data,
    )


async def extract_use_cases(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: VoiceInterviewExtractRequest,
) -> VoiceInterviewExtractResponse:
    """Scan employee turns for a known AI tool mention; draft a UseCase each."""
    drafts: list[UseCase] = []
    for turn in payload.transcript:
        if turn.role != "employee":
            continue
        tool = _find_tool(turn.text)
        if tool is None:
            continue
        record = UseCase(
            tenant_id=tenant_id,
            title=_title_for(tool, turn.text),
            tool=tool,
            department=turn.department or payload.default_department or "Unknown",
            status=UseCaseStatus.DRAFT,
            source=UseCaseSource.VOICE_INTERVIEW,
            data_classes="[]",
            notes=f'Extracted from voice-interview transcript — "{turn.text}" ({turn.speaker})',
        )
        db.add(record)
        drafts.append(record)

    await db.commit()
    for record in drafts:
        await db.refresh(record)

    return VoiceInterviewExtractResponse(
        drafts=[_serialise(r) for r in drafts],
        created_count=len(drafts),
    )
