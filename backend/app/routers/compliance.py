"""Compliance assessments per AI asset (Task 2.4)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Analyst, AuthUser, get_tenant_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.ai_asset import AIAsset
from app.models.compliance_assessment import ComplianceAssessment
from app.schemas.compliance_assessment import (
    ComplianceAssessmentCreate,
    ComplianceAssessmentListResponse,
    ComplianceAssessmentResponse,
    ComplianceAssessmentUpdate,
)

router = APIRouter(prefix="/compliance", tags=["Compliance"])


def _to_resp(c: ComplianceAssessment) -> ComplianceAssessmentResponse:
    return ComplianceAssessmentResponse(
        id=str(c.id),
        tenant_id=str(c.tenant_id),
        org_id=str(c.org_id),
        asset_id=str(c.asset_id),
        framework=c.framework,
        risk_level=c.risk_level,
        passed=c.passed,
        rationale=c.rationale,
        review_date=c.review_date,
        live_from=c.live_from,
        live_to=c.live_to,
        approval_status=c.approval_status,
        approved_by_id=str(c.approved_by_id) if c.approved_by_id else None,
        created_at=c.created_at,
        updated_at=c.updated_at,
        is_test_data=c.is_test_data,
    )


@router.get("", response_model=ComplianceAssessmentListResponse)
async def list_compliance(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    framework: str | None = Query(None),
    passed: bool | None = Query(None),
    asset_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> ComplianceAssessmentListResponse:
    q = select(ComplianceAssessment)
    cq = select(func.count(ComplianceAssessment.id))
    if not user.is_super_admin():
        q = q.where(ComplianceAssessment.tenant_id == user.tenant_id)
        cq = cq.where(ComplianceAssessment.tenant_id == user.tenant_id)
    if framework:
        q = q.where(ComplianceAssessment.framework == framework)
        cq = cq.where(ComplianceAssessment.framework == framework)
    if passed is not None:
        q = q.where(ComplianceAssessment.passed == passed)
        cq = cq.where(ComplianceAssessment.passed == passed)
    if asset_id is not None:
        q = q.where(ComplianceAssessment.asset_id == asset_id)
        cq = cq.where(ComplianceAssessment.asset_id == asset_id)

    total = (await db.execute(cq)).scalar() or 0
    rows = await db.execute(
        q.order_by(ComplianceAssessment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size),
    )
    items = list(rows.scalars().all())
    return ComplianceAssessmentListResponse(
        assessments=[_to_resp(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


async def _assert_asset_for_user(
    db: AsyncSession,
    user: AuthUser,
    asset_uuid: uuid.UUID,
) -> None:
    stmt = select(AIAsset).where(AIAsset.id == asset_uuid)
    if not user.is_super_admin():
        stmt = stmt.where(AIAsset.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    if row.scalar_one_or_none() is None:
        raise NotFoundError("AI asset")


@router.post("", response_model=ComplianceAssessmentResponse, status_code=status.HTTP_201_CREATED)
async def create_compliance(
    user: Analyst,
    body: ComplianceAssessmentCreate,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> ComplianceAssessmentResponse:
    if user.is_super_admin() or not user.tenant_id or not user.org_id:
        raise ForbiddenError("Tenant context required for this operation")

    aid = uuid.UUID(body.asset_id)
    await _assert_asset_for_user(db, user, aid)

    appr = uuid.UUID(body.approved_by_id) if body.approved_by_id else None

    c = ComplianceAssessment(
        tenant_id=user.tenant_id,
        org_id=user.org_id,
        asset_id=aid,
        framework=body.framework,
        risk_level=body.risk_level,
        passed=body.passed,
        rationale=body.rationale,
        review_date=body.review_date,
        live_from=body.live_from,
        live_to=body.live_to,
        approval_status=body.approval_status,
        approved_by_id=appr,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _to_resp(c)


@router.patch("/{compliance_id}", response_model=ComplianceAssessmentResponse)
async def patch_compliance(
    user: Analyst,
    body: ComplianceAssessmentUpdate,
    compliance_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> ComplianceAssessmentResponse:
    stmt = select(ComplianceAssessment).where(ComplianceAssessment.id == compliance_id)
    if not user.is_super_admin():
        stmt = stmt.where(ComplianceAssessment.tenant_id == user.tenant_id)
    row = await db.execute(stmt)
    c = row.scalar_one_or_none()
    if c is None:
        raise NotFoundError("Compliance assessment")

    if body.risk_level is not None:
        c.risk_level = body.risk_level
    if body.passed is not None:
        c.passed = body.passed
    if body.rationale is not None:
        c.rationale = body.rationale
    if body.review_date is not None:
        c.review_date = body.review_date
    if body.live_from is not None:
        c.live_from = body.live_from
    if body.live_to is not None:
        c.live_to = body.live_to
    if body.approval_status is not None:
        c.approval_status = body.approval_status
    if body.approved_by_id is not None:
        c.approved_by_id = uuid.UUID(body.approved_by_id) if body.approved_by_id else None

    await db.commit()
    await db.refresh(c)
    return _to_resp(c)
