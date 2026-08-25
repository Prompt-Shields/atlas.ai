"""UseCaseReview router — re-attestation cadence + drift.

Backs /dashboard/reattestation. Tenant-scoped throughout. Owner of
the use case or OrgAdmin+ can mark reviewed; only OrgAdmin+ can
dismiss.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.use_case import UseCase
from app.models.use_case_review import ReviewStatus, UseCaseReview

router = APIRouter(prefix="/use-case-reviews", tags=["Use case reviews"])


# ─── Schemas ─────────────────────────────────────────────────────────


class UseCaseReviewResponse(BaseModel):
    id: str
    tenant_id: str
    use_case_id: str
    use_case_title: str
    use_case_tool: str
    use_case_department: str
    due_at: datetime
    status: ReviewStatus
    reviewed_by_user_id: str | None
    reviewed_at: datetime | None
    notes: str | None
    drift_observed: bool
    dismiss_reason: str | None
    created_at: datetime


class UseCaseReviewListResponse(BaseModel):
    reviews: list[UseCaseReviewResponse]
    total: int
    page: int
    page_size: int


class UseCaseReviewCounts(BaseModel):
    due: int
    overdue: int
    reviewed: int
    dismissed: int
    drift_observed_count: int


class MarkReviewedRequest(BaseModel):
    notes: str | None = Field(None, max_length=2000)
    drift_observed: bool = Field(
        False,
        description=(
            "Set true when the registered fields no longer match "
            "reality — e.g. tool switched models, new data classes, "
            "broader user base. IT lead drills in next."
        ),
    )


class DismissReviewRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ─── Helpers ─────────────────────────────────────────────────────────


def _serialise(
    review: UseCaseReview,
    use_case: UseCase,
) -> UseCaseReviewResponse:
    return UseCaseReviewResponse(
        id=str(review.id),
        tenant_id=str(review.tenant_id),
        use_case_id=str(review.use_case_id),
        use_case_title=use_case.title,
        use_case_tool=use_case.tool,
        use_case_department=use_case.department,
        due_at=review.due_at,
        status=review.status,
        reviewed_by_user_id=(
            str(review.reviewed_by_user_id) if review.reviewed_by_user_id else None
        ),
        reviewed_at=review.reviewed_at,
        notes=review.notes,
        drift_observed=review.drift_observed,
        dismiss_reason=review.dismiss_reason,
        created_at=review.created_at,
    )


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=UseCaseReviewListResponse)
async def list_reviews(
    user: AuthUser,
    status_filter: ReviewStatus | None = Query(None, alias="status"),
    mine: bool = Query(
        False,
        description=(
            "When true, return only reviews for use cases the caller owns. "
            "Drives the dashboard banner that prompts owners to attest."
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseReviewListResponse:
    base = select(UseCaseReview, UseCase).join(UseCase, UseCaseReview.use_case_id == UseCase.id)
    count_base = select(func.count(UseCaseReview.id)).join(
        UseCase, UseCaseReview.use_case_id == UseCase.id
    )

    if not user.is_super_admin():
        base = base.where(UseCaseReview.tenant_id == user.tenant_id)
        count_base = count_base.where(UseCaseReview.tenant_id == user.tenant_id)
    if status_filter is not None:
        base = base.where(UseCaseReview.status == status_filter)
        count_base = count_base.where(UseCaseReview.status == status_filter)
    if mine:
        base = base.where(UseCase.owner_user_id == user.user_id)
        count_base = count_base.where(UseCase.owner_user_id == user.user_id)

    total = int((await db.execute(count_base)).scalar() or 0)

    rows = (
        await db.execute(
            base.order_by(UseCaseReview.due_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    reviews = [_serialise(rev, uc) for rev, uc in rows]
    return UseCaseReviewListResponse(
        reviews=reviews,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/counts", response_model=UseCaseReviewCounts)
async def get_counts(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseReviewCounts:
    base = select(UseCaseReview.status, func.count(UseCaseReview.id)).group_by(UseCaseReview.status)
    drift_q = select(func.count(UseCaseReview.id)).where(UseCaseReview.drift_observed.is_(True))
    if not user.is_super_admin():
        base = base.where(UseCaseReview.tenant_id == user.tenant_id)
        drift_q = drift_q.where(UseCaseReview.tenant_id == user.tenant_id)

    counts = dict.fromkeys(ReviewStatus, 0)
    for status, cnt in (await db.execute(base)).all():
        counts[status] = int(cnt or 0)

    drift_count = int((await db.execute(drift_q)).scalar() or 0)

    return UseCaseReviewCounts(
        due=counts[ReviewStatus.DUE],
        overdue=counts[ReviewStatus.OVERDUE],
        reviewed=counts[ReviewStatus.REVIEWED],
        dismissed=counts[ReviewStatus.DISMISSED],
        drift_observed_count=drift_count,
    )


@router.post("/{review_id}/mark-reviewed", response_model=UseCaseReviewResponse)
async def mark_reviewed(
    payload: MarkReviewedRequest,
    user: AuthUser,
    review_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseReviewResponse:
    """Attest the use case is still accurate. Idempotent — re-calling
    overwrites notes / drift_observed on the existing REVIEWED row.
    """
    row_q = (
        select(UseCaseReview, UseCase)
        .join(UseCase, UseCaseReview.use_case_id == UseCase.id)
        .where(UseCaseReview.id == review_id)
    )
    result = (await db.execute(row_q)).one_or_none()
    if result is None:
        raise NotFoundError("UseCaseReview", str(review_id))
    review, use_case = result

    if not user.is_super_admin() and review.tenant_id != user.tenant_id:
        raise NotFoundError("UseCaseReview", str(review_id))

    # Owner-or-OrgAdmin gate. The dismiss path relies on the OrgAdmin
    # dependency; here owners are let in as well.
    is_owner = use_case.owner_user_id == user.user_id
    if not is_owner and not _is_org_admin(user):
        raise ForbiddenError("Only the use-case owner or an OrgAdmin can mark reviewed")

    now = datetime.now(UTC)
    review.status = ReviewStatus.REVIEWED
    review.reviewed_by_user_id = user.user_id
    review.reviewed_at = now
    review.notes = payload.notes
    review.drift_observed = payload.drift_observed
    await db.commit()
    await db.refresh(review)
    return _serialise(review, use_case)


@router.post("/{review_id}/dismiss", response_model=UseCaseReviewResponse)
async def dismiss_review(
    payload: DismissReviewRequest,
    user: OrgAdmin,
    review_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseReviewResponse:
    """OrgAdmin-only — dismiss a review with a reason (e.g.
    'use case retired pending cleanup').
    """
    row_q = (
        select(UseCaseReview, UseCase)
        .join(UseCase, UseCaseReview.use_case_id == UseCase.id)
        .where(UseCaseReview.id == review_id)
    )
    result = (await db.execute(row_q)).one_or_none()
    if result is None:
        raise NotFoundError("UseCaseReview", str(review_id))
    review, use_case = result

    if not user.is_super_admin() and review.tenant_id != user.tenant_id:
        raise NotFoundError("UseCaseReview", str(review_id))

    review.status = ReviewStatus.DISMISSED
    review.dismiss_reason = payload.reason.strip()
    review.reviewed_by_user_id = user.user_id
    review.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(review)
    return _serialise(review, use_case)


@router.post("/enqueue", response_model=dict)
async def enqueue_now(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """OrgAdmin escape hatch — trigger the enqueue job on-demand.

    Useful for testing the cadence + for tenants that want a fresh
    sweep right after onboarding.
    """
    from app.services.use_case_review_sync import enqueue_due_reviews

    n = await enqueue_due_reviews(db)
    return {"enqueued_or_rolled_over": n}


# ─── Role check helper ───────────────────────────────────────────────


def _is_org_admin(user) -> bool:
    """The dependency layer's `has_role` indirection — we duplicate
    here so the router stays decoupled from Role's enum import.
    Acceptable copy because the alternative is dragging Role/RBAC
    into the router file.
    """
    from app.models.user import Role

    return any(user.has_role(r) for r in (Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.ORG_ADMIN))
