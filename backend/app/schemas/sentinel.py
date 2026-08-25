"""Pydantic schemas for the Microsoft Sentinel connect router (issue #247)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.sentinel_service import SentinelEventType, SentinelSeverity


class SentinelConnectRequest(BaseModel):
    """Connect-wizard payload — workspace label + the event-type mapping.

    v1 makes no live call to Azure Monitor; this just records where the
    tenant intends to send events and which Prompt Shields event types
    should be included.
    """

    workspace_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Customer-facing Sentinel workspace label, e.g. 'Acme Corp SOC'",
    )
    table_name: str = Field(
        default="PromptShieldsActivity_CL",
        min_length=1,
        max_length=255,
        description="Target Sentinel custom table",
    )
    enabled_event_types: list[SentinelEventType] = Field(
        default_factory=lambda: list(SentinelEventType),
        description="Prompt Shields event types mapped into the stream",
    )


class SentinelEvent(BaseModel):
    """One row shaped like the PromptShieldsActivity_CL custom table."""

    time_generated: datetime
    event_id: str
    user: str
    ai_tool: str
    is_shadow_ai: bool
    event_type: SentinelEventType
    sensitive_type: str | None
    severity: SentinelSeverity
    detail: str
    prompt_hash: str


class SentinelEventStreamResponse(BaseModel):
    connected: bool
    workspace_name: str | None = None
    table_name: str | None = None
    enabled_event_types: list[SentinelEventType] = Field(default_factory=list)
    events: list[SentinelEvent] = Field(default_factory=list)
