"""Blob ingestion Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BlobIngestRequest(BaseModel):
    """Manual adapter blob ingestion request.

    Content is capped at ~500 KB of text to prevent abuse.
    """

    title: str | None = Field(None, max_length=500)
    content: str = Field(..., min_length=1, max_length=500_000)
    source_type: str = Field(default="manual", max_length=50)
    source_id: str | None = Field(None, max_length=255)
    metadata: dict | None = Field(None, max_length=50)


class BlobResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    source_type: str
    source_id: str | None
    title: str | None
    content_preview: str  # First 200 chars
    word_count: int
    processing_status: str
    batch_id: str | None
    adapter_name: str
    created_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class BlobListResponse(BaseModel):
    blobs: list[BlobResponse]
    total: int
    page: int
    page_size: int
