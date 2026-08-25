"""FastAPI auth dependencies — JWT + API key validation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import validate_api_key
from app.auth.jwt import verify_access_token
from app.database import get_db_session, set_tenant_guc
from app.errors import ForbiddenError, UnauthorizedError
from app.models.user import APIKey, Role

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """Represents the authenticated user context."""

    def __init__(
        self,
        user_id: uuid.UUID,
        email: str,
        roles: list[str],
        tenant_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.roles = roles
        self.tenant_id = tenant_id
        self.org_id = org_id

    def has_role(self, role: Role) -> bool:
        return role.value in self.roles

    def is_super_admin(self) -> bool:
        return self.has_role(Role.SUPER_ADMIN)

    def can_access_tenant(self, tenant_id: uuid.UUID) -> bool:
        return self.is_super_admin() or self.tenant_id == tenant_id

    def can_access_org(self, org_id: uuid.UUID) -> bool:
        return self.is_super_admin() or self.org_id == org_id


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    """Extract and validate the JWT from the Authorization header."""
    if credentials is None:
        raise UnauthorizedError("Bearer token required")

    try:
        payload = verify_access_token(credentials.credentials)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    user = CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        email=payload["email"],
        roles=payload.get("roles", []),
        tenant_id=uuid.UUID(payload["tenant_id"]) if payload.get("tenant_id") else None,
        org_id=uuid.UUID(payload["org_id"]) if payload.get("org_id") else None,
    )

    # Set on request state for middleware/logging
    request.state.user_id = user.user_id
    request.state.tenant_id = user.tenant_id
    request.state.org_id = user.org_id

    return user


async def require_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    x_api_key: str | None = Header(None),
) -> APIKey | None:
    """Validate API key header. Required for sensitive/adapter endpoints.

    Returns the validated APIKey record for downstream tenant checks.
    """
    if not x_api_key:
        raise UnauthorizedError("X-API-Key header required for this endpoint")

    api_key_record = await validate_api_key(db, x_api_key)
    if api_key_record is None:
        raise UnauthorizedError("Invalid API key")
    return api_key_record


async def require_jwt_and_api_key(
    user: CurrentUser = Depends(get_current_user),
    api_key_record: APIKey | None = Depends(require_api_key),
) -> CurrentUser:
    """Require both JWT and API key for sensitive endpoints.

    Verifies the API key belongs to the same tenant as the JWT user.
    Non-super-admin users MUST use a tenant-scoped API key that matches
    their own tenant — unscoped keys are only valid for super admins.
    """
    if not user.is_super_admin():
        if not api_key_record or not api_key_record.tenant_id:
            raise ForbiddenError("API key must be tenant-scoped")
        if user.tenant_id != api_key_record.tenant_id:
            raise ForbiddenError("API key does not belong to your tenant")
    return user


async def get_tenant_db_session(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AsyncSession:
    """Yield the request DB session with tenant GUC set (RLS enforcement).

    This must be the same AsyncSession the route handler uses. It depends on the
    authenticated user so tenant context is available before queries run.
    """
    if user.is_super_admin():
        return db
    if not user.tenant_id:
        raise ForbiddenError("Tenant context required")
    await set_tenant_guc(db, user.tenant_id)
    return db


def require_roles(*roles: Role):
    """Factory for role-based access control dependency."""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.is_super_admin():
            return user
        for role in roles:
            if user.has_role(role):
                return user
        raise ForbiddenError(f"Required role: {', '.join(r.value for r in roles)}")

    return _check


def require_tenant_access():
    """Ensure user can access the requested tenant."""

    async def _check(
        tenant_id: uuid.UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not user.can_access_tenant(tenant_id):
            raise ForbiddenError("Access denied: tenant boundary violation")
        return user

    return _check


# Type aliases for dependency injection
AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
SecureUser = Annotated[CurrentUser, Depends(require_jwt_and_api_key)]
SuperAdmin = Annotated[CurrentUser, Depends(require_roles(Role.SUPER_ADMIN))]
TenantAdmin = Annotated[CurrentUser, Depends(require_roles(Role.SUPER_ADMIN, Role.TENANT_ADMIN))]
OrgAdmin = Annotated[
    CurrentUser, Depends(require_roles(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.ORG_ADMIN))
]
Analyst = Annotated[
    CurrentUser,
    Depends(require_roles(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.ORG_ADMIN, Role.ANALYST)),
]
