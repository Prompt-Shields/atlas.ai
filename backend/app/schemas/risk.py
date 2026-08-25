"""Risk and Mitigation Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RiskMitigationResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    blob_id: str
    batch_id: str | None
    risk_title: str
    risk_description: str
    risk_category: str
    risk_severity: str
    risk_likelihood: str
    risk_score: float
    confidence_score: float
    mitigation_title: str
    mitigation_description: str
    mitigation_steps: list[str]
    citations: list[dict[str, Any]]
    model_used: str
    processing_status: str
    created_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class RiskListResponse(BaseModel):
    risks: list[RiskMitigationResponse]
    total: int
    page: int
    page_size: int
