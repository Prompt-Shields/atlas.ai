"""Onboarding router — /api/v1/onboarding/*.

Tracks whether a tenant has completed the post-signup wizard. The
frontend reads `GET /status` to decide whether to show the wizard
vs route directly to `/dashboard`. Marking complete is a one-way
operation; re-running the wizard from the dashboard is an admin
action that doesn't reset this flag (we just walk through the same
steps as a refresher).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.tenant import Tenant

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


# ─── Schemas ─────────────────────────────────────────────────────────


class OnboardingStatusResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    is_complete: bool
    completed_at: datetime | None


class OnboardingCompleteRequest(BaseModel):
    # Free-form opaque payload — the wizard records which integrations
    # the user explicitly skipped vs connected, for analytics. We
    # don't validate the shape; the front-end's hands.
    steps_completed: list[str] | None = None


# ─── Endpoints ───────────────────────────────────────────────────────


async def _load_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("Tenant", str(tenant_id))
    return record


@router.get("/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> OnboardingStatusResponse:
    """Return whether the caller's tenant has completed onboarding.

    Used by the frontend on app boot to decide whether to redirect
    the user to /onboarding or /dashboard.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    tenant = await _load_tenant(db, user.tenant_id)
    return OnboardingStatusResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        is_complete=tenant.onboarding_completed_at is not None,
        completed_at=tenant.onboarding_completed_at,
    )


@router.post("/complete", response_model=OnboardingStatusResponse)
async def mark_onboarding_complete(
    user: OrgAdmin,
    payload: OnboardingCompleteRequest = Body(default_factory=OnboardingCompleteRequest),
    db: AsyncSession = Depends(get_db_session),
) -> OnboardingStatusResponse:
    """Mark the caller's tenant as onboarded.

    Idempotent — re-calling is a no-op. Only OrgAdmin+ can flip the
    flag (avoids accidental dismissal by a Viewer).
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    tenant = await _load_tenant(db, user.tenant_id)
    if tenant.onboarding_completed_at is None:
        tenant.onboarding_completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(tenant)

    return OnboardingStatusResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        is_complete=tenant.onboarding_completed_at is not None,
        completed_at=tenant.onboarding_completed_at,
    )


@router.post("/reset", response_model=OnboardingStatusResponse)
async def reset_onboarding(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> OnboardingStatusResponse:
    """Admin escape hatch — re-trigger the wizard.

    Used in dev / test, or by SuperAdmin troubleshooting a tenant.
    Clearing the completed_at timestamp causes the next login to
    route through /onboarding again.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")
    tenant = await _load_tenant(db, user.tenant_id)
    tenant.onboarding_completed_at = None
    await db.commit()
    await db.refresh(tenant)
    return OnboardingStatusResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        is_complete=False,
        completed_at=None,
    )
