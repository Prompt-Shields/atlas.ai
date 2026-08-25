"""AI Risk Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AIRiskResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str
    use_case_id: str
    name: str
    severity: str | None
    description: str | None
    owasp_ref: str | None
    nist_ref: str | None
    eu_ai_act_ref: str | None
    mitigation_status: str | None
    mitigations: Any | None
    created_at: datetime
    updated_at: datetime
    is_test_data: bool

    class Config:
        from_attributes = True


class AIRiskCreate(BaseModel):
    use_case_id: str
    name: str
    severity: str | None = None
    description: str | None = None
    owasp_ref: str | None = None
    nist_ref: str | None = None
    eu_ai_act_ref: str | None = None
    mitigation_status: str | None = None
    mitigations: Any | None = None


class AIRiskUpdate(BaseModel):
    name: str | None = None
    severity: str | None = None
    description: str | None = None
    owasp_ref: str | None = None
    nist_ref: str | None = None
    eu_ai_act_ref: str | None = None
    mitigation_status: str | None = None
    mitigations: Any | None = None


class AIRiskListResponse(BaseModel):
    risks: list[AIRiskResponse]
    total: int
    page: int
    page_size: int
