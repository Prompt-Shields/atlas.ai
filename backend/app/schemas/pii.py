"""Pydantic schemas for the PII Shield router (`/api/v1/pii`, issue #245).

Stateless by design — the placeholder map produced by ``anonymize`` is
returned to the caller rather than persisted, and ``reidentify`` takes that
same map back in. There is no tenant-scoped storage here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.pii_service import PIIClass


class PIISpan(BaseModel):
    """One detected PII occurrence within the input text."""

    pii_class: PIIClass
    start: int
    end: int
    text: str
    confidence: float


class PIIDetectRequest(BaseModel):
    text: str = Field(min_length=1)


class PIIDetectResponse(BaseModel):
    spans: list[PIISpan] = Field(default_factory=list)


class PIIAnonymizeRequest(BaseModel):
    text: str = Field(min_length=1)


class PIIAnonymizeResponse(BaseModel):
    redacted_text: str
    placeholder_map: dict[str, str] = Field(default_factory=dict)
    spans: list[PIISpan] = Field(default_factory=list)


class PIIReidentifyRequest(BaseModel):
    text: str = Field(min_length=1)
    placeholder_map: dict[str, str] = Field(default_factory=dict)


class PIIReidentifyResponse(BaseModel):
    text: str
