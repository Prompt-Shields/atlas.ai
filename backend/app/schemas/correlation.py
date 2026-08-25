"""Correlation and Action Plan Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CorrelationResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    risk_mitigation_ids: list[str]
    batch_id: str | None
    correlation_title: str
    correlation_summary: str
    correlation_type: str
    overall_risk_score: float
    confidence_score: float
    action_plan_title: str
    action_plan_description: str
    action_steps: list[dict[str, Any]]
    priority: str
    estimated_effort: str | None
    citations: list[dict[str, Any]]
    reasoning: str
    status: str
    model_used: str
    created_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class CorrelationListResponse(BaseModel):
    correlations: list[CorrelationResponse]
    total: int
    page: int
    page_size: int
