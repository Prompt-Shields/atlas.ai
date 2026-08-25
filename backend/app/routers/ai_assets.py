"""AI assets — tenant catalogue (Milestone 2 Task 2.1)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Analyst, AuthUser, TenantAdmin, get_tenant_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.ai_asset import AIAsset
from app.models.ai_use_case import AIUseCase
from app.schemas.ai_asset import AIAssetCreate, AIAssetListResponse, AIAssetResponse, AIAssetUpdate
from app.schemas.ai_use_case import AIUseCaseResponse

router = APIRouter(prefix="/ai-assets", tags=["AI Assets"])


def _to_response(a: AIAsset) -> AIAssetResponse:
    return AIAssetResponse(
        id=str(a.id),
        tenant_id=str(a.tenant_id),
        org_id=str(a.org_id),
        name=a.name,
        description=a.description,
        deployment_status=a.deployment_status,
        hosting_location=a.hosting_location,
        model_type=a.model_type,
        is_third_party=a.is_third_party,
        owner_id=str(a.owner_id) if a.owner_id else None,
        risk_score=a.risk_score,
        gdpr_status=a.gdpr_status,
        eu_ai_act_status=a.eu_ai_act_status,
        nist_ai_rmf_status=a.nist_ai_rmf_status,
        eu_ai_act_risk_tier=a.eu_ai_act_risk_tier,
        accuracy=a.accuracy,
        latency_ms=a.latency_ms,
        drift_score=a.drift_score,
        monthly_calls=a.monthly_calls,
        created_at=a.created_at,
        updated_at=a.updated_at,
        is_test_data=a.is_test_data,
    )


@router.get("", response_model=AIAssetListResponse)
async def list_ai_assets(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> AIAssetListResponse:
    q = select(AIAsset)
    cq = select(func.count(AIAsset.id))
    if not user.is_super_admin():
        q = q.where(AIAsset.tenant_id == user.tenant_id)
        cq = cq.where(AIAsset.tenant_id == user.tenant_id)

    total = (await db.execute(cq)).scalar() or 0
    rows = await db.execute(
        q.order_by(AIAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size),
    )
    assets = list(rows.scalars().all())
    return AIAssetListResponse(
        assets=[_to_response(a) for a in assets],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{asset_id}", response_model=AIAssetResponse)
async def get_ai_asset(
    user: AuthUser,
    asset_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> AIAssetResponse:
    stmt = select(AIAsset).where(AIAsset.id == asset_id)
    if not user.is_super_admin():
        stmt = stmt.where(AIAsset.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    a = row.scalar_one_or_none()
    if a is None:
        raise NotFoundError("AI asset")
    return _to_response(a)


@router.post("", response_model=AIAssetResponse, status_code=status.HTTP_201_CREATED)
async def create_ai_asset(
    user: Analyst,
    body: AIAssetCreate,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> AIAssetResponse:
    if user.is_super_admin() or not user.tenant_id or not user.org_id:
        raise ForbiddenError("Tenant context required for this operation")

    owner_uuid = uuid.UUID(body.owner_id) if body.owner_id else None
    a = AIAsset(
        tenant_id=user.tenant_id,
        org_id=user.org_id,
        name=body.name,
        description=body.description,
        deployment_status=body.deployment_status,
        hosting_location=body.hosting_location,
        model_type=body.model_type,
        is_third_party=body.is_third_party,
        owner_id=owner_uuid,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _to_response(a)


@router.patch("/{asset_id}", response_model=AIAssetResponse)
async def update_ai_asset(
    user: Analyst,
    body: AIAssetUpdate,
    asset_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> AIAssetResponse:
    stmt = select(AIAsset).where(AIAsset.id == asset_id)
    if not user.is_super_admin():
        stmt = stmt.where(AIAsset.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    a = row.scalar_one_or_none()
    if a is None:
        raise NotFoundError("AI asset")

    if body.name is not None:
        a.name = body.name
    if body.description is not None:
        a.description = body.description
    if body.deployment_status is not None:
        a.deployment_status = body.deployment_status
    if body.hosting_location is not None:
        a.hosting_location = body.hosting_location
    if body.model_type is not None:
        a.model_type = body.model_type
    if body.is_third_party is not None:
        a.is_third_party = body.is_third_party
    if body.owner_id is not None:
        a.owner_id = uuid.UUID(body.owner_id) if body.owner_id else None

    await db.commit()
    await db.refresh(a)
    return _to_response(a)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_asset(
    user: TenantAdmin,
    asset_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> None:
    stmt = select(AIAsset).where(AIAsset.id == asset_id)
    if not user.is_super_admin():
        stmt = stmt.where(AIAsset.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    a = row.scalar_one_or_none()
    if a is None:
        raise NotFoundError("AI asset")
    await db.delete(a)
    await db.commit()


@router.get("/{asset_id}/use-cases", response_model=list[AIUseCaseResponse])
async def list_asset_use_cases(
    user: AuthUser,
    asset_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[AIUseCaseResponse]:
    stmt = select(AIAsset).where(AIAsset.id == asset_id)
    if not user.is_super_admin():
        stmt = stmt.where(AIAsset.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    if row.scalar_one_or_none() is None:
        raise NotFoundError("AI asset")

    uc_stmt = select(AIUseCase).where(AIUseCase.asset_id == asset_id)
    if not user.is_super_admin():
        uc_stmt = uc_stmt.where(AIUseCase.tenant_id == user.tenant_id)
    uc_rows = await db.execute(uc_stmt.order_by(AIUseCase.created_at.desc()))
    use_cases = list(uc_rows.scalars().all())
    return [
        AIUseCaseResponse(
            id=str(uc.id),
            tenant_id=str(uc.tenant_id),
            org_id=str(uc.org_id),
            asset_id=str(uc.asset_id),
            name=uc.name,
            department=uc.department,
            description=uc.description,
            business_owner_id=str(uc.business_owner_id) if uc.business_owner_id else None,
            data_classification=uc.data_classification,
            model_ids=uc.model_ids,
            risk_ids=uc.risk_ids,
            status=uc.status,
            discovered_via=uc.discovered_via,
            created_at_in_use=uc.created_at_in_use,
            created_at=uc.created_at,
            updated_at=uc.updated_at,
            is_test_data=uc.is_test_data,
        )
        for uc in use_cases
    ]
