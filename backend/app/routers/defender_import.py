"""Defender for Cloud Apps import router — `/api/v1/discover/defender-import` (M3, issue #243).

  GET   /discover/defender-import              Viewer+   list candidate rows
  POST  /discover/defender-import/import       OrgAdmin  extract + upsert from a pasted export
  POST  /discover/defender-import/{id}/accept  OrgAdmin  promote a candidate into the AI inventory
  PATCH /discover/defender-import/{id}         OrgAdmin  dismiss (or reset) a candidate

Tenant scoping is an explicit `tenant_id` filter (super-admins see all),
matching `routers/mcp_discovery.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.schemas.defender_import import (
    DefenderAppAcceptRequest,
    DefenderAppAcceptResponse,
    DefenderAppStatusUpdate,
    DefenderImportListResponse,
    DefenderImportRequest,
    DefenderImportResponse,
    DiscoveredDefenderAppResponse,
)
from app.services import defender_import_service

router = APIRouter(prefix="/discover/defender-import", tags=["Defender for Cloud Apps import"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


@router.get("", response_model=DefenderImportListResponse)
async def list_defender_apps(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> DefenderImportListResponse:
    """List the tenant's imported app candidates, highest-risk first."""
    return await defender_import_service.list_apps(db, _tenant_scope(user))


@router.post("/import", response_model=DefenderImportResponse)
async def import_defender_export(
    payload: DefenderImportRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DefenderImportResponse:
    """Extract known apps from a pasted export; upsert enriched candidate rows."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot import")
    return await defender_import_service.import_export(db, user.tenant_id, payload.raw_text)


@router.post("/{app_id}/accept", response_model=DefenderAppAcceptResponse)
async def accept_defender_app(
    app_id: uuid.UUID,
    payload: DefenderAppAcceptRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DefenderAppAcceptResponse:
    """Promote a discovered app candidate into the AI inventory as a SaaS vendor."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot accept")
    result = await defender_import_service.accept(db, user.tenant_id, app_id, payload)
    if result is None:
        raise NotFoundError("Discovered app not found")
    return result


@router.patch("/{app_id}", response_model=DiscoveredDefenderAppResponse)
async def update_defender_app_status(
    app_id: uuid.UUID,
    payload: DefenderAppStatusUpdate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DiscoveredDefenderAppResponse:
    """Dismiss (or reset) a discovered app candidate."""
    app = await defender_import_service.update_status(db, _tenant_scope(user), app_id, payload)
    if app is None:
        raise NotFoundError("Discovered app not found")
    return app
