"""PII Shield router — `/api/v1/pii` (issue #245).

  POST  /pii/detect        Any   detect PII spans in a block of text
  POST  /pii/anonymize     Any   redact PII, returning the reversal map
  POST  /pii/reidentify    Any   restore original values from that map

Stateless: nothing here reads or writes the database, and the placeholder
map from `anonymize` is not persisted server-side — the caller carries it
to `reidentify`. No tenant scoping is needed as a result.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.dependencies import AuthUser
from app.schemas.pii import (
    PIIAnonymizeRequest,
    PIIAnonymizeResponse,
    PIIDetectRequest,
    PIIDetectResponse,
    PIIReidentifyRequest,
    PIIReidentifyResponse,
    PIISpan,
)
from app.services import pii_service

router = APIRouter(prefix="/pii", tags=["PII Shield"])


@router.post("/detect", response_model=PIIDetectResponse)
async def detect(payload: PIIDetectRequest, user: AuthUser) -> PIIDetectResponse:
    spans = pii_service.detect_pii(payload.text)
    return PIIDetectResponse(
        spans=[
            PIISpan(
                pii_class=s.pii_class,
                start=s.start,
                end=s.end,
                text=s.text,
                confidence=s.confidence,
            )
            for s in spans
        ]
    )


@router.post("/anonymize", response_model=PIIAnonymizeResponse)
async def anonymize(payload: PIIAnonymizeRequest, user: AuthUser) -> PIIAnonymizeResponse:
    redacted_text, placeholder_map, spans = pii_service.anonymize_text(payload.text)
    return PIIAnonymizeResponse(
        redacted_text=redacted_text,
        placeholder_map=placeholder_map,
        spans=[
            PIISpan(
                pii_class=s.pii_class,
                start=s.start,
                end=s.end,
                text=s.text,
                confidence=s.confidence,
            )
            for s in spans
        ],
    )


@router.post("/reidentify", response_model=PIIReidentifyResponse)
async def reidentify(payload: PIIReidentifyRequest, user: AuthUser) -> PIIReidentifyResponse:
    return PIIReidentifyResponse(
        text=pii_service.reidentify_text(payload.text, payload.placeholder_map)
    )
