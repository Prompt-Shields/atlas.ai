"""Risk and Mitigation router."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, get_tenant_db_session
from app.models.risk import RiskMitigation
from app.schemas.risk import RiskListResponse, RiskMitigationResponse


def _safe_json_loads(raw: str | None, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = []
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


router = APIRouter(prefix="/risks", tags=["Risks"])


@router.get("", response_model=RiskListResponse)
async def list_risks(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> RiskListResponse:
    """List risk/mitigation records (tenant-scoped)."""
    query = select(RiskMitigation)
    count_query = select(func.count(RiskMitigation.id))

    if not user.is_super_admin():
        query = query.where(RiskMitigation.tenant_id == user.tenant_id)
        count_query = count_query.where(RiskMitigation.tenant_id == user.tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(RiskMitigation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    risks = list(result.scalars().all())

    return RiskListResponse(
        risks=[
            RiskMitigationResponse(
                id=str(r.id),
                tenant_id=str(r.tenant_id),
                org_id=str(r.org_id),
                blob_id=str(r.blob_id),
                batch_id=r.batch_id,
                risk_title=r.risk_title,
                risk_description=r.risk_description,
                risk_category=r.risk_category,
                risk_severity=r.risk_severity,
                risk_likelihood=r.risk_likelihood,
                risk_score=r.risk_score,
                confidence_score=r.confidence_score,
                mitigation_title=r.mitigation_title,
                mitigation_description=r.mitigation_description,
                mitigation_steps=_safe_json_loads(r.mitigation_steps),
                citations=_safe_json_loads(r.citations),
                model_used=r.model_used,
                processing_status=r.processing_status,
                created_at=r.created_at,
                is_test_data=r.is_test_data,
            )
            for r in risks
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
