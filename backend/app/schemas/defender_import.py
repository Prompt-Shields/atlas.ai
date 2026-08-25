"""Pydantic schemas for the Defender for Cloud Apps import router (#243).

Wire format for `app.models.defender_import.DiscoveredDefenderApp`. Enum
values mirror the ORM enum (snake_case), matching `schemas/mcp_discovery.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.defender_import import DefenderImportStatus


class DiscoveredDefenderAppResponse(BaseModel):
    """An imported app candidate as returned by the API."""

    id: str
    canonical_name: str
    name: str
    category: str
    users_count: int
    data_volume_mb: int
    risk_score: int
    certifications: list[str]
    compliance_status: dict[str, str]
    status: DefenderImportStatus
    promoted_vendor_id: str | None = None
    imported_at: datetime

    class Config:
        from_attributes = True


class DefenderImportListResponse(BaseModel):
    """Imported app candidates, ready to render."""

    apps: list[DiscoveredDefenderAppResponse] = Field(default_factory=list)


class DefenderImportRequest(BaseModel):
    """A pasted Defender for Cloud Apps export (no real OCR in v1).

    An empty/omitted `raw_text` runs the extractor against the bundled
    seed export, matching the AISPM prototype's demo-data convention.
    """

    raw_text: str | None = Field(default=None, max_length=20_000)


class DefenderImportResponse(BaseModel):
    """Result of an import: matched lines plus the current candidate set."""

    matched_lines: int
    created: int
    updated: int
    apps: list[DiscoveredDefenderAppResponse]


class DefenderAppStatusUpdate(BaseModel):
    """Dismiss (or reset) an imported app candidate."""

    status: DefenderImportStatus


class DefenderAppAcceptRequest(BaseModel):
    """Optional overrides when accepting a candidate into the inventory."""

    department: str = Field(default="Unknown", min_length=1, max_length=255)


class DefenderAppAcceptResponse(BaseModel):
    """Result of accepting a candidate: the row plus the new vendor id."""

    app: DiscoveredDefenderAppResponse
    vendor_id: str
