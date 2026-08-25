"""Dispatch event Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DispatchEventResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    correlation_id: str
    event_type: str
    payload: dict[str, Any]
    severity: str
    delivered: bool
    delivered_at: datetime | None
    created_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class DispatchEventListResponse(BaseModel):
    events: list[DispatchEventResponse]
    total: int
    page: int
    page_size: int


class SSEEvent(BaseModel):
    """Server-Sent Event payload."""

    event: str
    data: dict[str, Any]
    id: str | None = None
    retry: int | None = None
