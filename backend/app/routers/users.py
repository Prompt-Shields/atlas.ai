"""User management router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import AuthUser, OrgAdmin, TenantAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.models.user import Role, User, UserRole
from app.schemas.user import UserCreate, UserListResponse, UserResponse, UserUpdate
from app.services import auth_service
from app.services.audit import log_audit_event

router = APIRouter(prefix="/users", tags=["Users"])


def _to_response(u: User) -> UserResponse:
    """Serialise a User (with roles loaded) to the API response shape."""
    return UserResponse(
        id=str(u.id),
        email=u.email,
        full_name=u.full_name,
        is_active=u.is_active,
        is_email_verified=u.is_email_verified,
        tenant_id=str(u.tenant_id) if u.tenant_id else None,
        org_id=str(u.org_id) if u.org_id else None,
        roles=[r.role.value for r in u.roles],
        auth_source="sso" if u.entra_object_id else "local",
        created_at=u.created_at,
        updated_at=u.updated_at,
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Create a user within the caller's tenant."""
    new_user = await auth_service.create_user(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=Role(body.role),
        tenant_id=user.tenant_id,
        org_id=uuid.UUID(body.org_id) if body.org_id else user.org_id,
    )

    await log_audit_event(
        db,
        event_type="user.created",
        action="create",
        actor_id=user.user_id,
        tenant_id=user.tenant_id,
        resource_type="user",
        resource_id=str(new_user.id),
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    result = await db.execute(
        select(User).where(User.id == new_user.id).options(selectinload(User.roles))
    )
    loaded = result.scalar_one()
    return _to_response(loaded)


@router.get("", response_model=UserListResponse)
async def list_users(
    user: OrgAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="Search email or full name (case-insensitive)"),
    role: str | None = Query(
        None, pattern=r"^(SUPER_ADMIN|TENANT_ADMIN|ORG_ADMIN|ANALYST|VIEWER)$"
    ),
    is_active: bool | None = Query(None, description="Filter by active status"),
    source: str | None = Query(
        None, pattern=r"^(sso|local)$", description="sso = Entra-linked, local = password"
    ),
    db: AsyncSession = Depends(get_db_session),
) -> UserListResponse:
    """List users in the caller's tenant (org-scoped unless super admin).

    Supports admin-portal filtering: free-text `q`, `role`, `is_active`, and
    `source` (sso vs local). Filters compose (logical AND).
    """
    query = select(User).options(selectinload(User.roles))
    count_query = select(func.count(User.id))

    filters = []
    if not user.is_super_admin():
        filters.append(User.tenant_id == user.tenant_id)
        if not user.has_role(Role.TENANT_ADMIN) and user.org_id:
            filters.append(User.org_id == user.org_id)
    if q:
        like = f"%{q}%"
        filters.append(or_(User.email.ilike(like), User.full_name.ilike(like)))
    if role is not None:
        filters.append(User.roles.any(UserRole.role == Role(role)))
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if source == "sso":
        filters.append(User.entra_object_id.is_not(None))
    elif source == "local":
        filters.append(User.entra_object_id.is_(None))

    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    users = list(result.scalars().all())

    return UserListResponse(
        users=[_to_response(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get the current authenticated user's profile."""
    result = await db.execute(
        select(User).where(User.id == user.user_id).options(selectinload(User.roles))
    )
    u = result.scalar_one()
    return _to_response(u)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    current_user: TenantAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Update a user (tenant admin+ only). Can change role, name, active status."""
    target_id = uuid.UUID(user_id)
    result = await db.execute(
        select(User).where(User.id == target_id).options(selectinload(User.roles))
    )
    target = result.scalar_one_or_none()
    if not target:
        from app.errors import NotFoundError

        raise NotFoundError("User", user_id)

    # Tenant boundary check
    if not current_user.is_super_admin() and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Cannot modify users in other tenants")

    if body.full_name is not None:
        target.full_name = body.full_name
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.role is not None:
        # Update role
        old_roles = [r.role.value for r in target.roles]
        # Clear existing roles and add new one
        for ur in target.roles:
            await db.delete(ur)
        new_role = UserRole(
            user_id=target.id,
            role=Role(body.role),
            tenant_id=target.tenant_id,
            org_id=target.org_id,
        )
        db.add(new_role)

        await log_audit_event(
            db,
            event_type="role.changed",
            action="update",
            actor_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
            resource_type="user",
            resource_id=user_id,
            details={"old_roles": old_roles, "new_role": body.role},
            correlation_id=getattr(request.state, "correlation_id", None),
        )

    await db.flush()

    # Re-fetch
    result = await db.execute(
        select(User).where(User.id == target_id).options(selectinload(User.roles))
    )
    u = result.scalar_one()
    return _to_response(u)
