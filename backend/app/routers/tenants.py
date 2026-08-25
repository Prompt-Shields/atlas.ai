"""Tenant and Organisation management router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SuperAdmin, TenantAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.schemas.tenant import (
    OrgCreate,
    OrgResponse,
    TenantCreate,
    TenantResponse,
)
from app.services import tenant_service

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    user: SuperAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    """Create a new tenant (super admin only)."""
    tenant = await tenant_service.create_tenant(
        db, name=body.name, slug=body.slug, description=body.description
    )
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        description=tenant.description,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    user: SuperAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> list[TenantResponse]:
    """List all tenants (super admin only)."""
    tenants, _ = await tenant_service.list_tenants(db, page=page, page_size=page_size)
    return [
        TenantResponse(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            is_active=t.is_active,
            description=t.description,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tenants
    ]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    """Get tenant details."""
    tid = uuid.UUID(tenant_id)
    if not user.can_access_tenant(tid):
        raise ForbiddenError("Access denied: tenant boundary violation")
    tenant = await tenant_service.get_tenant(db, tid)
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        description=tenant.description,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.post("/{tenant_id}/organisations", response_model=OrgResponse, status_code=201)
async def create_organisation(
    tenant_id: str,
    body: OrgCreate,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> OrgResponse:
    """Create an organisation within a tenant."""
    tid = uuid.UUID(tenant_id)
    if not user.can_access_tenant(tid):
        raise ForbiddenError("Access denied: tenant boundary violation")
    org = await tenant_service.create_organisation(
        db, tenant_id=tid, name=body.name, slug=body.slug
    )
    return OrgResponse(
        id=str(org.id),
        tenant_id=str(org.tenant_id),
        name=org.name,
        slug=org.slug,
        is_active=org.is_active,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.get("/{tenant_id}/organisations", response_model=list[OrgResponse])
async def list_organisations(
    tenant_id: str,
    user: TenantAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> list[OrgResponse]:
    """List organisations in a tenant."""
    tid = uuid.UUID(tenant_id)
    if not user.can_access_tenant(tid):
        raise ForbiddenError("Access denied: tenant boundary violation")
    orgs, _ = await tenant_service.list_organisations(db, tid, page=page, page_size=page_size)
    return [
        OrgResponse(
            id=str(o.id),
            tenant_id=str(o.tenant_id),
            name=o.name,
            slug=o.slug,
            is_active=o.is_active,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )
        for o in orgs
    ]
