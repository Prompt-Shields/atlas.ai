"""AI Asset Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AIAssetResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    name: str
    description: str | None
    deployment_status: str
    hosting_location: str | None
    model_type: str | None
    is_third_party: bool
    owner_id: str | None
    risk_score: float | None
    gdpr_status: str | None
    eu_ai_act_status: str | None
    nist_ai_rmf_status: str | None
    eu_ai_act_risk_tier: str | None
    accuracy: float | None
    latency_ms: int | None
    drift_score: float | None
    monthly_calls: int | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class AIAssetCreate(BaseModel):
    name: str
    description: str | None = None
    deployment_status: str = "active"
    hosting_location: str | None = None
    model_type: str | None = None
    is_third_party: bool = False
    owner_id: str | None = None


class AIAssetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    deployment_status: str | None = None
    hosting_location: str | None = None
    model_type: str | None = None
    is_third_party: bool | None = None
    owner_id: str | None = None


class AIAssetListResponse(BaseModel):
    assets: list[AIAssetResponse]
    total: int
    page: int
    page_size: int
