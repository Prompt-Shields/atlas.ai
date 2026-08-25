"""Monitoring and LLM usage Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LLMUsageResponse(BaseModel):
    id: str
    tenant_id: str
    org_id: str | None
    user_id: str | None
    job_id: str | None
    model_name: str
    model_provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    operation_type: str
    created_at: datetime

    class Config:
        from_attributes = True


class LLMUsageSummary(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    by_model: dict[str, dict]
    by_operation: dict[str, dict]


class LLMUsageListResponse(BaseModel):
    records: list[LLMUsageResponse]
    total: int
    page: int
    page_size: int


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    redis: str
