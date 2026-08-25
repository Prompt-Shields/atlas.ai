"""AI Governance Handbook acknowledgement router.

Backs the /dashboard/handbook page. Three endpoints:

  GET  /api/v1/handbook/status
       Returns whether the caller has acknowledged the current
       handbook version + acknowledgement stats for the tenant.

  POST /api/v1/handbook/acknowledge
       Records that the caller has read + accepted the current
       handbook version. Idempotent — re-calling returns the
       existing row.

  GET  /api/v1/handbook/tenant-stats
       Tenant-wide coverage: total users vs acknowledged users
       broken out per handbook version. OrgAdmin+ only. Used by
       the admin sidebar widget "X of Y staff have acknowledged
       the handbook".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.models.handbook import HandbookAcknowledgement, TenantHandbookOverride
from app.models.user import User

router = APIRouter(prefix="/handbook", tags=["Handbook"])


# Canonical version for the stock atlas handbook. Tenants can override
# both the version *and* the content via TenantHandbookOverride —
# `resolve_handbook_version(db, tenant_id)` is the right call for
# anything that needs to know what version applies to a given user.
# Bump this constant when the stock copy materially changes.
STOCK_HANDBOOK_VERSION = "1.0"
CURRENT_HANDBOOK_VERSION = STOCK_HANDBOOK_VERSION  # backwards-compat alias


async def resolve_handbook_version(db: AsyncSession, tenant_id: uuid.UUID | None) -> str:
    """Return the effective handbook version for a tenant.

    Prefer the tenant override if one exists; otherwise fall back to
    the stock version constant. Importable from anywhere that needs
    "what version is this user expected to ack?" — dispatcher,
    banner, Slack DM composer, etc.
    """
    if tenant_id is None:
        return STOCK_HANDBOOK_VERSION
    override = (
        await db.execute(
            select(TenantHandbookOverride.version).where(
                TenantHandbookOverride.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    return override or STOCK_HANDBOOK_VERSION


# ─── Schemas ─────────────────────────────────────────────────────────


class HandbookStatusResponse(BaseModel):
    current_version: str
    acknowledged: bool
    acknowledged_at: datetime | None
    acknowledged_version: str | None


class HandbookAckRequest(BaseModel):
    notes: str | None = Field(None, max_length=500)


class HandbookAckResponse(BaseModel):
    user_id: str
    version: str
    acknowledged_at: datetime


class HandbookCoverageItem(BaseModel):
    version: str
    count: int


class HandbookTenantStats(BaseModel):
    tenant_id: str
    current_version: str
    total_users: int
    acknowledged_current_count: int
    acknowledged_any_count: int
    by_version: list[HandbookCoverageItem]


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get("/status", response_model=HandbookStatusResponse)
async def get_handbook_status(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> HandbookStatusResponse:
    """Return whether the caller has acknowledged the current version."""
    effective_version = await resolve_handbook_version(db, user.tenant_id)
    ack = (
        await db.execute(
            select(HandbookAcknowledgement)
            .where(
                HandbookAcknowledgement.user_id == user.user_id,
                HandbookAcknowledgement.version == effective_version,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    return HandbookStatusResponse(
        current_version=effective_version,
        acknowledged=ack is not None,
        acknowledged_at=ack.created_at if ack else None,
        acknowledged_version=ack.version if ack else None,
    )


@router.post("/acknowledge", response_model=HandbookAckResponse)
async def acknowledge_handbook(
    user: AuthUser,
    payload: HandbookAckRequest | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> HandbookAckResponse:
    """Idempotent — re-calling returns the existing row."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot acknowledge")

    effective_version = await resolve_handbook_version(db, user.tenant_id)
    existing = (
        await db.execute(
            select(HandbookAcknowledgement).where(
                HandbookAcknowledgement.user_id == user.user_id,
                HandbookAcknowledgement.version == effective_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return HandbookAckResponse(
            user_id=str(existing.user_id),
            version=existing.version,
            acknowledged_at=existing.created_at,
        )

    ack = HandbookAcknowledgement(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        version=effective_version,
        notes=(payload.notes if payload else None),
    )
    db.add(ack)
    await db.commit()
    await db.refresh(ack)
    return HandbookAckResponse(
        user_id=str(ack.user_id),
        version=ack.version,
        acknowledged_at=ack.created_at,
    )


@router.get("/tenant-stats", response_model=HandbookTenantStats)
async def get_tenant_stats(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> HandbookTenantStats:
    """OrgAdmin+ — return coverage for the caller's tenant."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    # Total active users in the tenant.
    total_q = select(func.count(User.id)).where(
        User.tenant_id == user.tenant_id,
        User.is_active.is_(True),
    )
    total_users = int((await db.execute(total_q)).scalar() or 0)

    # Acknowledged current version (unique users).
    current_q = select(func.count(func.distinct(HandbookAcknowledgement.user_id))).where(
        HandbookAcknowledgement.tenant_id == user.tenant_id,
        HandbookAcknowledgement.version == CURRENT_HANDBOOK_VERSION,
    )
    current_count = int((await db.execute(current_q)).scalar() or 0)

    # Acknowledged any version (unique users).
    any_q = select(func.count(func.distinct(HandbookAcknowledgement.user_id))).where(
        HandbookAcknowledgement.tenant_id == user.tenant_id,
    )
    any_count = int((await db.execute(any_q)).scalar() or 0)

    # Per-version breakdown.
    by_q = (
        select(
            HandbookAcknowledgement.version,
            func.count(func.distinct(HandbookAcknowledgement.user_id)),
        )
        .where(HandbookAcknowledgement.tenant_id == user.tenant_id)
        .group_by(HandbookAcknowledgement.version)
    )
    by_rows = (await db.execute(by_q)).all()
    by_version = [HandbookCoverageItem(version=v, count=int(c or 0)) for v, c in by_rows]

    return HandbookTenantStats(
        tenant_id=str(user.tenant_id),
        current_version=await resolve_handbook_version(db, user.tenant_id),
        total_users=total_users,
        acknowledged_current_count=current_count,
        acknowledged_any_count=any_count,
        by_version=by_version,
    )


# ─── Per-tenant handbook override ────────────────────────────────────


class HandbookContentResponse(BaseModel):
    """Returned by GET /content — render path for the handbook page.

    `content_markdown` is None when no tenant override exists; the
    frontend should fall back to its curated stock Markdown blocks
    in that case. `version` is always populated (stock or override).
    """

    version: str
    content_markdown: str | None
    updated_at: datetime | None
    is_stock: bool


class HandbookOverrideUpsert(BaseModel):
    """Upsert payload — OrgAdmin sets both content + version atomically.

    Version-bumping behaviour: every change to `version` triggers
    re-acknowledgement (existing HandbookAcknowledgement rows for
    older versions stop satisfying the "current version" filter).
    Document this clearly in the admin UI so admins don't accidentally
    nuke their handbook coverage with a typo bump.
    """

    version: str = Field(..., min_length=1, max_length=50)
    content_markdown: str = Field(..., min_length=10, max_length=200_000)


@router.get("/content", response_model=HandbookContentResponse)
async def get_handbook_content(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> HandbookContentResponse:
    """Return the tenant's handbook Markdown (or null if using stock)."""
    if user.tenant_id is None:
        return HandbookContentResponse(
            version=STOCK_HANDBOOK_VERSION,
            content_markdown=None,
            updated_at=None,
            is_stock=True,
        )

    override = (
        await db.execute(
            select(TenantHandbookOverride).where(TenantHandbookOverride.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()

    if override is None:
        return HandbookContentResponse(
            version=STOCK_HANDBOOK_VERSION,
            content_markdown=None,
            updated_at=None,
            is_stock=True,
        )

    return HandbookContentResponse(
        version=override.version,
        content_markdown=override.content_markdown,
        updated_at=override.updated_at,
        is_stock=False,
    )


@router.put("/content", response_model=HandbookContentResponse)
async def upsert_handbook_content(
    payload: HandbookOverrideUpsert,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> HandbookContentResponse:
    """OrgAdmin+ — upsert this tenant's handbook content + version.

    Atomic: a single PUT updates both fields. The unique constraint
    on tenant_id makes this a true upsert — either we INSERT a new
    row or UPDATE the existing one.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant.")

    existing = (
        await db.execute(
            select(TenantHandbookOverride).where(TenantHandbookOverride.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        record = TenantHandbookOverride(
            tenant_id=user.tenant_id,
            version=payload.version,
            content_markdown=payload.content_markdown,
            updated_by_user_id=user.user_id,
        )
        db.add(record)
    else:
        existing.version = payload.version
        existing.content_markdown = payload.content_markdown
        existing.updated_by_user_id = user.user_id
        record = existing

    await db.commit()
    await db.refresh(record)
    return HandbookContentResponse(
        version=record.version,
        content_markdown=record.content_markdown,
        updated_at=record.updated_at,
        is_stock=False,
    )


@router.delete("/content", status_code=204)
async def revert_to_stock_handbook(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """OrgAdmin+ — drop the tenant override; frontend reverts to stock.

    Important caveat: this drops the row, but already-acked users
    are *not* automatically re-acked under the stock version. If the
    stock version differs from the deleted override's version, users
    will get re-prompted on next dashboard load. That's the desired
    behaviour — reverting is a material change.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant.")
    existing = (
        await db.execute(
            select(TenantHandbookOverride).where(TenantHandbookOverride.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
