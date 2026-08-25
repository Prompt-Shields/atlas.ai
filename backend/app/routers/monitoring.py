"""Monitoring and LLM usage router."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantAdmin, get_tenant_db_session
from app.models.llm_usage import LLMUsageRecord
from app.schemas.monitoring import LLMUsageListResponse, LLMUsageResponse, LLMUsageSummary

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


@router.get("/llm-usage", response_model=LLMUsageListResponse)
async def list_llm_usage(
    user: TenantAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> LLMUsageListResponse:
    """List LLM usage records (tenant-scoped)."""
    query = select(LLMUsageRecord)
    count_query = select(func.count(LLMUsageRecord.id))

    if not user.is_super_admin():
        query = query.where(LLMUsageRecord.tenant_id == user.tenant_id)
        count_query = count_query.where(LLMUsageRecord.tenant_id == user.tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(LLMUsageRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = list(result.scalars().all())

    return LLMUsageListResponse(
        records=[
            LLMUsageResponse(
                id=str(r.id),
                tenant_id=str(r.tenant_id),
                org_id=str(r.org_id) if r.org_id else None,
                user_id=str(r.user_id) if r.user_id else None,
                job_id=r.job_id,
                model_name=r.model_name,
                model_provider=r.model_provider,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                total_tokens=r.total_tokens,
                estimated_cost_usd=r.estimated_cost_usd,
                operation_type=r.operation_type,
                created_at=r.created_at,
            )
            for r in records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/llm-usage/summary", response_model=LLMUsageSummary)
async def get_llm_usage_summary(
    user: TenantAdmin,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> LLMUsageSummary:
    """Get LLM usage summary with cost breakdown."""
    from datetime import timedelta

    period_start = datetime.now(UTC) - timedelta(days=days)
    period_end = datetime.now(UTC)

    query = select(LLMUsageRecord).where(LLMUsageRecord.created_at >= period_start)

    tenant_id = user.tenant_id
    if not user.is_super_admin() and tenant_id:
        query = query.where(LLMUsageRecord.tenant_id == tenant_id)

    result = await db.execute(query)
    records = list(result.scalars().all())

    by_model: dict[str, dict] = {}
    by_operation: dict[str, dict] = {}

    total_prompt = 0
    total_completion = 0
    total_cost = 0.0

    for r in records:
        total_prompt += r.prompt_tokens
        total_completion += r.completion_tokens
        total_cost += r.estimated_cost_usd

        if r.model_name not in by_model:
            by_model[r.model_name] = {"requests": 0, "tokens": 0, "cost": 0.0}
        by_model[r.model_name]["requests"] += 1
        by_model[r.model_name]["tokens"] += r.total_tokens
        by_model[r.model_name]["cost"] += r.estimated_cost_usd

        if r.operation_type not in by_operation:
            by_operation[r.operation_type] = {"requests": 0, "tokens": 0, "cost": 0.0}
        by_operation[r.operation_type]["requests"] += 1
        by_operation[r.operation_type]["tokens"] += r.total_tokens
        by_operation[r.operation_type]["cost"] += r.estimated_cost_usd

    return LLMUsageSummary(
        tenant_id=str(tenant_id) if tenant_id else "all",
        period_start=period_start,
        period_end=period_end,
        total_requests=len(records),
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_prompt + total_completion,
        total_estimated_cost_usd=round(total_cost, 4),
        by_model=by_model,
        by_operation=by_operation,
    )
