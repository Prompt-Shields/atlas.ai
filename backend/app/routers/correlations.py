"""Correlation and Action Plan router."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, get_tenant_db_session
from app.models.correlation import CorrelationActionPlan
from app.schemas.correlation import CorrelationListResponse, CorrelationResponse


def _safe_json_loads(raw: str | None, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = []
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


router = APIRouter(prefix="/correlations", tags=["Correlations"])


@router.get("", response_model=CorrelationListResponse)
async def list_correlations(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> CorrelationListResponse:
    """List correlation/action plan records (tenant-scoped)."""
    query = select(CorrelationActionPlan)
    count_query = select(func.count(CorrelationActionPlan.id))

    if not user.is_super_admin():
        query = query.where(CorrelationActionPlan.tenant_id == user.tenant_id)
        count_query = count_query.where(CorrelationActionPlan.tenant_id == user.tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(CorrelationActionPlan.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    correlations = list(result.scalars().all())

    return CorrelationListResponse(
        correlations=[
            CorrelationResponse(
                id=str(c.id),
                tenant_id=str(c.tenant_id),
                org_id=str(c.org_id),
                risk_mitigation_ids=_safe_json_loads(c.risk_mitigation_ids),
                batch_id=c.batch_id,
                correlation_title=c.correlation_title,
                correlation_summary=c.correlation_summary,
                correlation_type=c.correlation_type,
                overall_risk_score=c.overall_risk_score,
                confidence_score=c.confidence_score,
                action_plan_title=c.action_plan_title,
                action_plan_description=c.action_plan_description,
                action_steps=_safe_json_loads(c.action_steps),
                priority=c.priority,
                estimated_effort=c.estimated_effort,
                citations=_safe_json_loads(c.citations),
                reasoning=c.reasoning,
                status=c.status,
                model_used=c.model_used,
                created_at=c.created_at,
                is_test_data=c.is_test_data,
            )
            for c in correlations
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
