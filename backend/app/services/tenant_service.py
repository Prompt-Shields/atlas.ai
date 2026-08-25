"""Tenant and Organisation service."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.models.settings import TenantSetting
from app.models.tenant import Organisation, Tenant


async def create_tenant(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    description: str | None = None,
    is_test_data: bool = False,
) -> Tenant:
    existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Tenant with slug '{slug}' already exists")

    tenant = Tenant(
        name=name,
        slug=slug,
        description=description,
        is_active=False,
        is_test_data=is_test_data,
    )
    db.add(tenant)
    await db.flush()

    # Create default settings
    settings = TenantSetting(tenant_id=tenant.id)
    db.add(settings)
    await db.flush()

    return tenant


async def activate_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise NotFoundError("Tenant", str(tenant_id))
    tenant.is_active = True
    await db.flush()
    return tenant


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise NotFoundError("Tenant", str(tenant_id))
    return tenant


async def list_tenants(
    db: AsyncSession, page: int = 1, page_size: int = 20
) -> tuple[list[Tenant], int]:
    total_result = await db.execute(select(func.count(Tenant.id)))
    total = total_result.scalar() or 0

    result = await db.execute(
        select(Tenant)
        .order_by(Tenant.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def create_organisation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    slug: str,
    is_test_data: bool = False,
) -> Organisation:
    # Verify tenant exists
    await get_tenant(db, tenant_id)

    existing = await db.execute(
        select(Organisation).where(
            Organisation.tenant_id == tenant_id,
            Organisation.slug == slug,
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError(f"Organisation with slug '{slug}' already exists in this tenant")

    org = Organisation(
        tenant_id=tenant_id,
        name=name,
        slug=slug,
        is_test_data=is_test_data,
    )
    db.add(org)
    await db.flush()
    return org


async def list_organisations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Organisation], int]:
    total_result = await db.execute(
        select(func.count(Organisation.id)).where(Organisation.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(Organisation)
        .where(Organisation.tenant_id == tenant_id)
        .order_by(Organisation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total
