"""Compliance assessment schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class ComplianceAssessmentResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    asset_id: str
    framework: str
    risk_level: str | None
    passed: bool
    rationale: str | None
    review_date: date | None
    live_from: datetime | None
    live_to: datetime | None
    approval_status: str | None
    approved_by_id: str | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class ComplianceAssessmentCreate(BaseModel):
    asset_id: str
    framework: str
    risk_level: str | None = None
    passed: bool = False
    rationale: str | None = None
    review_date: date | None = None
    live_from: datetime | None = None
    live_to: datetime | None = None
    approval_status: str | None = None
    approved_by_id: str | None = None


class ComplianceAssessmentUpdate(BaseModel):
    risk_level: str | None = None
    passed: bool | None = None
    rationale: str | None = None
    review_date: date | None = None
    live_from: datetime | None = None
    live_to: datetime | None = None
    approval_status: str | None = None
    approved_by_id: str | None = None


class ComplianceAssessmentListResponse(BaseModel):
    assessments: list[ComplianceAssessmentResponse]
    total: int
    page: int
    page_size: int
