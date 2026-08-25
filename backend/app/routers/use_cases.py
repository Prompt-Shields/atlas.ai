"""Use-case registry router — `/api/v1/use-cases`.

CRUD over `UseCase`. Tenant-scoped throughout: every read filters by
`user.tenant_id` unless the caller is `SUPER_ADMIN`; writes set
`tenant_id` from the JWT and reject any cross-tenant attempt.

Role gates:

  GET  list / detail / counts → any authenticated user (Viewer+)
  POST / PATCH / DELETE       → OrgAdmin (or higher)

POST never creates a record for another tenant. SUPER_ADMIN must
specify `tenant_id` via a query param if they want to seed cross-
tenant data (out of scope for v0.1 — for now SUPER_ADMIN writes go to
their own tenant_id like everyone else).

The frontend stub at `/dashboard/registry` (PR #25) becomes the
consumer once this lands.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin, get_tenant_db_session
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.saas_vendor import SaaSVendorProfile
from app.models.use_case import (
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.schemas.owner_inference import (
    OwnerCandidateResponse,
    OwnerSuggestionResponse,
    OwnerSuggestionsListResponse,
)
from app.schemas.use_case import (
    UseCaseCounts,
    UseCaseCreate,
    UseCaseListResponse,
    UseCasePromoteResponse,
    UseCaseResponse,
    UseCaseUpdate,
)
from app.services import owner_inference as owner_inference_service
from app.services.use_case_promotion import promote_use_case_to_asset

router = APIRouter(prefix="/use-cases", tags=["Use cases"])


def _not_vendor():
    """Predicate excluding use_cases that carry a saas_vendor_profiles row.

    SaaS vendors are modelled as a use_cases row + a 1:1 saas_vendor_profiles
    row (epic #187) but live on their own surface (/saas-vendor-ai). They must
    not leak into the AI-use-case inventory list or its counts (#196), so
    anti-join on the profile via a correlated NOT EXISTS.
    """
    return ~(
        select(SaaSVendorProfile.id).where(SaaSVendorProfile.use_case_id == UseCase.id).exists()
    )


def _safe_json_loads(raw: str | None, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = []
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _serialise(u: UseCase) -> UseCaseResponse:
    return UseCaseResponse(
        id=str(u.id),
        tenant_id=str(u.tenant_id),
        title=u.title,
        tool=u.tool,
        department=u.department,
        owner_user_id=str(u.owner_user_id) if u.owner_user_id else None,
        status=u.status,
        risk_tier=u.risk_tier,
        source=u.source,
        data_classes=_safe_json_loads(u.data_classes),
        frequency=u.frequency,
        notes=u.notes,
        dispatched_from_response_id=(
            str(u.dispatched_from_response_id) if u.dispatched_from_response_id else None
        ),
        created_at=u.created_at,
        updated_at=u.updated_at,
        is_test_data=u.is_test_data,
    )


# ─── List ────────────────────────────────────────────────────────────


@router.get("", response_model=UseCaseListResponse)
async def list_use_cases(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: UseCaseStatus | None = Query(None, alias="status"),
    source_filter: UseCaseSource | None = Query(None, alias="source"),
    risk_tier_filter: UseCaseRiskTier | None = Query(None, alias="risk_tier"),
    tool: str | None = Query(None, max_length=100),
    department: str | None = Query(None, max_length=255),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseListResponse:
    """List registered use cases for the caller's tenant (paginated)."""
    base = select(UseCase).where(_not_vendor())
    base_count = select(func.count(UseCase.id)).where(_not_vendor())

    if not user.is_super_admin():
        base = base.where(UseCase.tenant_id == user.tenant_id)
        base_count = base_count.where(UseCase.tenant_id == user.tenant_id)

    if status_filter is not None:
        base = base.where(UseCase.status == status_filter)
        base_count = base_count.where(UseCase.status == status_filter)
    if source_filter is not None:
        base = base.where(UseCase.source == source_filter)
        base_count = base_count.where(UseCase.source == source_filter)
    if risk_tier_filter is not None:
        base = base.where(UseCase.risk_tier == risk_tier_filter)
        base_count = base_count.where(UseCase.risk_tier == risk_tier_filter)
    if tool:
        base = base.where(UseCase.tool == tool)
        base_count = base_count.where(UseCase.tool == tool)
    if department:
        base = base.where(UseCase.department == department)
        base_count = base_count.where(UseCase.department == department)

    total_result = await db.execute(base_count)
    total = int(total_result.scalar() or 0)

    rows_result = await db.execute(
        base.order_by(UseCase.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    rows = list(rows_result.scalars().all())

    return UseCaseListResponse(
        use_cases=[_serialise(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── Counts ──────────────────────────────────────────────────────────


@router.get("/counts", response_model=UseCaseCounts)
async def counts_use_cases(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseCounts:
    """Counts grouped by status. Used for the registry header summary."""
    query = (
        select(UseCase.status, func.count(UseCase.id)).where(_not_vendor()).group_by(UseCase.status)
    )
    if not user.is_super_admin():
        query = query.where(UseCase.tenant_id == user.tenant_id)

    result = await db.execute(query)
    by_status: dict[UseCaseStatus, int] = dict.fromkeys(UseCaseStatus, 0)
    total = 0
    for s, n in result.all():
        count = int(n or 0)
        by_status[s] = count
        total += count

    return UseCaseCounts(
        total=total,
        draft=by_status[UseCaseStatus.DRAFT],
        review=by_status[UseCaseStatus.REVIEW],
        active=by_status[UseCaseStatus.ACTIVE],
        retired=by_status[UseCaseStatus.RETIRED],
    )


# ─── Owner suggestions ───────────────────────────────────────────────
# Registered ahead of GET /{use_case_id} so the literal path segment
# "owner-suggestions" isn't swallowed by the {use_case_id} route.


@router.get("/owner-suggestions", response_model=OwnerSuggestionsListResponse)
async def owner_suggestions(
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> OwnerSuggestionsListResponse:
    """Ranked business + IT owner candidates for the tenant's unowned use cases.

    Heuristic over directory (Microsoft Graph) + role signals — see
    `app.services.owner_inference`. Read-only: accepting a suggestion is
    a plain `PATCH /use-cases/{id}` with `{"owner_user_id": ...}`.
    """
    if user.tenant_id is None:
        # SUPER_ADMIN with no tenant context — the heuristic is inherently
        # per-tenant (directory + role data are tenant-scoped), so there's
        # nothing sane to infer across all tenants at once.
        return OwnerSuggestionsListResponse(suggestions=[], total_unowned=0)

    suggestions = await owner_inference_service.suggest_owners(db, user.tenant_id, limit=limit)
    return OwnerSuggestionsListResponse(
        suggestions=[
            OwnerSuggestionResponse(
                use_case_id=str(s.use_case_id),
                use_case_title=s.use_case_title,
                department=s.department,
                business_owner_candidates=[
                    OwnerCandidateResponse(
                        user_id=str(c.user_id),
                        display_name=c.display_name,
                        email=c.email,
                        confidence=c.confidence,
                        rationale=c.rationale,
                    )
                    for c in s.business_owner_candidates
                ],
                it_owner_candidates=[
                    OwnerCandidateResponse(
                        user_id=str(c.user_id),
                        display_name=c.display_name,
                        email=c.email,
                        confidence=c.confidence,
                        rationale=c.rationale,
                    )
                    for c in s.it_owner_candidates
                ],
            )
            for s in suggestions
        ],
        total_unowned=len(suggestions),
    )


# ─── Detail ──────────────────────────────────────────────────────────


@router.post(
    "/{use_case_id}/promote",
    response_model=UseCasePromoteResponse,
)
async def promote_use_case(
    user: OrgAdmin,
    response: Response,
    use_case_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> UseCasePromoteResponse:
    """Promote an intake use case to the canonical SPM asset catalogue."""
    if user.tenant_id is None or user.org_id is None:
        raise ForbiddenError("Tenant and organization context required for promotion")

    result = await db.execute(select(UseCase).where(UseCase.id == use_case_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("UseCase", str(use_case_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise ForbiddenError("Cannot promote a use case from another tenant")

    asset, ai_use_case, created = await promote_use_case_to_asset(
        db,
        use_case=record,
        tenant_id=user.tenant_id,
        org_id=user.org_id,
    )
    await db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK

    return UseCasePromoteResponse(
        use_case_id=str(record.id),
        asset_id=str(asset.id),
        ai_use_case_id=str(ai_use_case.id),
        created=created,
    )


@router.get("/{use_case_id}", response_model=UseCaseResponse)
async def get_use_case(
    user: AuthUser,
    use_case_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseResponse:
    result = await db.execute(select(UseCase).where(UseCase.id == use_case_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("UseCase", str(use_case_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        # Cross-tenant access → pretend it doesn't exist (avoid leaking
        # the fact that the id is valid in another tenant).
        raise NotFoundError("UseCase", str(use_case_id))
    return _serialise(record)


# ─── Create ──────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=UseCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_use_case(
    payload: UseCaseCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseResponse:
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot register a use case")

    record = UseCase(
        tenant_id=user.tenant_id,
        title=payload.title,
        tool=payload.tool,
        department=payload.department,
        owner_user_id=payload.owner_user_id,
        status=payload.status,
        risk_tier=payload.risk_tier,
        source=payload.source,
        data_classes=json.dumps(payload.data_classes),
        frequency=payload.frequency,
        notes=payload.notes,
        dispatched_from_response_id=payload.dispatched_from_response_id,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _serialise(record)


# ─── Update ──────────────────────────────────────────────────────────


@router.patch("/{use_case_id}", response_model=UseCaseResponse)
async def update_use_case(
    payload: UseCaseUpdate,
    user: OrgAdmin,
    use_case_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> UseCaseResponse:
    result = await db.execute(select(UseCase).where(UseCase.id == use_case_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("UseCase", str(use_case_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("UseCase", str(use_case_id))

    # We use `model_fields_set` so absent fields stay untouched but
    # explicit-null fields *do* clear the column.
    provided = payload.model_dump(exclude_unset=True)

    if "title" in provided:
        record.title = provided["title"]
    if "tool" in provided:
        record.tool = provided["tool"]
    if "department" in provided:
        record.department = provided["department"]
    if "owner_user_id" in provided:
        record.owner_user_id = provided["owner_user_id"]
    if "status" in provided:
        record.status = provided["status"]
    if "risk_tier" in provided:
        record.risk_tier = provided["risk_tier"]
    if "data_classes" in provided:
        # `None` clears to empty array; non-None is the normalised list.
        record.data_classes = json.dumps(provided["data_classes"] or [])
    if "frequency" in provided:
        record.frequency = provided["frequency"]
    if "notes" in provided:
        record.notes = provided["notes"]

    await db.commit()
    await db.refresh(record)
    return _serialise(record)


# ─── Delete (soft) ───────────────────────────────────────────────────


@router.delete(
    "/{use_case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_use_case(
    user: OrgAdmin,
    use_case_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Soft delete — transitions status to RETIRED rather than DELETE.

    Retains audit history. If a hard delete is genuinely needed (GDPR
    erasure of the whole record), that's a separate compliance
    endpoint added in PR B alongside the SurveyResponse erasure path.
    """
    result = await db.execute(select(UseCase).where(UseCase.id == use_case_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("UseCase", str(use_case_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("UseCase", str(use_case_id))

    record.status = UseCaseStatus.RETIRED
    await db.commit()
