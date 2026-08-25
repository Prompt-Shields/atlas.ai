"""Pydantic schemas for the Developer API surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.developer import DeveloperScope, EventSeverity

# ──────────────────────────────────────────────────────────────────────
# Developer API Keys (extends existing api_keys with scopes)
# ──────────────────────────────────────────────────────────────────────


class DeveloperAPIKeyCreate(BaseModel):
    """Body for POST /developer/keys."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    scopes: list[DeveloperScope] = Field(
        default_factory=lambda: [DeveloperScope.EVENTS_WRITE],
        description="Permissions to grant. Defaults to events:write.",
    )

    @field_validator("scopes")
    @classmethod
    def at_least_one_scope(cls, v: list[DeveloperScope]) -> list[DeveloperScope]:
        if not v:
            raise ValueError("At least one scope is required")
        return v


class DeveloperAPIKeyResponse(BaseModel):
    """A key as returned to the UI (full plaintext key is returned only once
    on create — see DeveloperAPIKeyCreateResponse)."""

    id: str
    name: str
    description: str | None
    key_prefix: str
    is_active: bool
    scopes: list[DeveloperScope]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DeveloperAPIKeyCreateResponse(DeveloperAPIKeyResponse):
    """Returned by POST /developer/keys — includes the plaintext key, shown once."""

    full_key: str = Field(..., description="Plaintext key — store immediately, shown only once.")


class DeveloperAPIKeyListResponse(BaseModel):
    keys: list[DeveloperAPIKeyResponse]
    total: int


class DeveloperAPIKeyScopesUpdate(BaseModel):
    """Body for PATCH /developer/keys/{id}/scopes."""

    scopes: list[DeveloperScope]

    @field_validator("scopes")
    @classmethod
    def at_least_one_scope(cls, v: list[DeveloperScope]) -> list[DeveloperScope]:
        if not v:
            raise ValueError("At least one scope is required")
        return v


# ──────────────────────────────────────────────────────────────────────
# Event Definitions
# ──────────────────────────────────────────────────────────────────────


class EventDefinitionCreate(BaseModel):
    # `protected_namespaces=()` silences Pydantic's warning about `schema`
    # shadowing BaseModel.schema; we genuinely want a `schema` field on the
    # wire because that's what callers will write in their API requests.
    model_config = {"protected_namespaces": ()}

    name: str = Field(..., min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9._-]*$")
    description: str | None = None
    schema: dict[str, Any] | None = Field(
        default=None,
        description="Advisory schema — keys are field names, values are types ('string', 'integer', etc.).",
    )
    default_severity: EventSeverity = EventSeverity.INFO


class EventDefinitionUpdate(BaseModel):
    model_config = {"protected_namespaces": ()}

    description: str | None = None
    schema: dict[str, Any] | None = None
    is_enabled: bool | None = None
    default_severity: EventSeverity | None = None


class EventDefinitionResponse(BaseModel):
    model_config = {"protected_namespaces": (), "from_attributes": True}

    id: str
    name: str
    description: str | None
    schema: dict[str, Any] | None
    is_enabled: bool
    default_severity: EventSeverity
    created_at: datetime
    updated_at: datetime


class EventDefinitionListResponse(BaseModel):
    definitions: list[EventDefinitionResponse]
    total: int


# ──────────────────────────────────────────────────────────────────────
# Event Ingestion + Listing
# ──────────────────────────────────────────────────────────────────────


class EventIngestItem(BaseModel):
    """A single event in an ingestion batch."""

    event_type: str = Field(..., min_length=1, max_length=120)
    severity: EventSeverity | None = None
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    user_external_id: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=120)
    occurrences: int = Field(default=1, ge=1, le=10_000)


class EventIngestRequest(BaseModel):
    """POST /developer/events — batched ingestion."""

    events: list[EventIngestItem] = Field(..., min_length=1, max_length=500)


class EventIngestResponse(BaseModel):
    ingested: int
    skipped: int = 0
    skipped_reasons: list[str] = Field(default_factory=list)


class EventResponse(BaseModel):
    id: str
    event_type: str
    severity: EventSeverity
    occurred_at: datetime
    payload: dict[str, Any]
    source: str | None
    session_id: str | None
    user_external_id: str | None
    correlation_id: str | None
    occurrences: int
    created_at: datetime

    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    events: list[EventResponse]
    total: int
    has_more: bool
    next_cursor: str | None = None
