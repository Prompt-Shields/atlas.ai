"""Model risk profiles — curated read-only catalogue (Task 2.3)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, get_tenant_db_session
from app.errors import NotFoundError
from app.models.model_risk_profile import ModelRiskProfile
from app.schemas.model_risk_profile import ModelRiskProfileListResponse, ModelRiskProfileResponse

router = APIRouter(prefix="/model-risk", tags=["Model risk"])


def _to_resp(p: ModelRiskProfile) -> ModelRiskProfileResponse:
    return ModelRiskProfileResponse(
        id=str(p.id),
        tenant_id=str(p.tenant_id),
        org_id=str(p.org_id),
        name=p.name,
        provider=p.provider,
        category=p.category,
        parameters=p.parameters,
        lifecycle_status=p.lifecycle_status,
        overall_risk=p.overall_risk,
        hallucination_risk=p.hallucination_risk,
        bias_risk=p.bias_risk,
        toxicity_risk=p.toxicity_risk,
        privacy_risk=p.privacy_risk,
        security_risk=p.security_risk,
        compliance_risk=p.compliance_risk,
        key_risks=p.key_risks,
        approved_use_cases=p.approved_use_cases,
        risk_alerts=p.risk_alerts,
        created_at=p.created_at,
        updated_at=p.updated_at,
        is_test_data=p.is_test_data,
    )


@router.get("", response_model=ModelRiskProfileListResponse)
async def list_model_risk_profiles(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> ModelRiskProfileListResponse:
    q = select(ModelRiskProfile)
    cq = select(func.count(ModelRiskProfile.id))
    if not user.is_super_admin():
        q = q.where(ModelRiskProfile.tenant_id == user.tenant_id)
        cq = cq.where(ModelRiskProfile.tenant_id == user.tenant_id)

    total = (await db.execute(cq)).scalar() or 0
    rows = await db.execute(
        q.order_by(ModelRiskProfile.name.asc()).offset((page - 1) * page_size).limit(page_size),
    )
    items = list(rows.scalars().all())
    return ModelRiskProfileListResponse(
        profiles=[_to_resp(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{profile_id}", response_model=ModelRiskProfileResponse)
async def get_model_risk_profile(
    user: AuthUser,
    profile_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> ModelRiskProfileResponse:
    stmt = select(ModelRiskProfile).where(ModelRiskProfile.id == profile_id)
    if not user.is_super_admin():
        stmt = stmt.where(ModelRiskProfile.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    p = row.scalar_one_or_none()
    if p is None:
        raise NotFoundError("Model risk profile")
    return _to_resp(p)
