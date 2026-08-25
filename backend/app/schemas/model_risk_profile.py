"""Model risk profile (read-mostly curated catalogue)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ModelRiskProfileResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    name: str
    provider: str | None
    category: str | None
    parameters: str | None
    lifecycle_status: str | None
    overall_risk: int | None
    hallucination_risk: int | None
    bias_risk: int | None
    toxicity_risk: int | None
    privacy_risk: int | None
    security_risk: int | None
    compliance_risk: int | None
    key_risks: list[str] | None
    approved_use_cases: list[str] | None
    risk_alerts: list[dict] | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class ModelRiskProfileListResponse(BaseModel):
    profiles: list[ModelRiskProfileResponse]
    total: int
    page: int
    page_size: int
