"""AI Use Case Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AIUseCaseResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    asset_id: str
    name: str
    department: str | None
    description: str | None
    business_owner_id: str | None
    data_classification: str | None
    model_ids: list[str] | None
    risk_ids: list[str] | None
    status: str | None
    discovered_via: str | None
    created_at_in_use: datetime | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class AIUseCaseCreate(BaseModel):
    asset_id: str
    name: str
    department: str | None = None
    description: str | None = None
    business_owner_id: str | None = None
    data_classification: str | None = None
    model_ids: list[str] | None = None
    risk_ids: list[str] | None = None
    status: str | None = None
    discovered_via: str | None = None
    created_at_in_use: datetime | None = None


class AIUseCaseUpdate(BaseModel):
    name: str | None = None
    department: str | None = None
    description: str | None = None
    business_owner_id: str | None = None
    data_classification: str | None = None
    model_ids: list[str] | None = None
    risk_ids: list[str] | None = None
    status: str | None = None
    discovered_via: str | None = None
    created_at_in_use: datetime | None = None


class AIUseCaseListResponse(BaseModel):
    use_cases: list[AIUseCaseResponse]
    total: int
    page: int
    page_size: int
