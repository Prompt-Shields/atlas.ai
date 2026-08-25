"""Developer-API scope enforcement.

The /v1/developer/* endpoints accept either:
  - a JWT (for dashboard UI calls — full access scoped by role)
  - an X-API-Key header (for SDK/programmatic calls — scoped by the
    DeveloperAPIKeyScope rows attached to that key)

This module exposes `require_developer_scope(scope)` — a dependency
factory that returns a CurrentUser-like context whether the caller
authenticated via JWT or API key.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import validate_api_key
from app.auth.dependencies import CurrentUser, bearer_scheme, get_current_user
from app.database import get_db_session
from app.errors import ForbiddenError, UnauthorizedError
from app.models.developer import DeveloperAPIKeyScope, DeveloperScope


@dataclass
class DeveloperPrincipal:
    """Resolved caller — either a JWT user or an API key.

    For event ingestion we need:
      - tenant_id (to scope the data)
      - api_key_id (to record provenance, if applicable)
      - scope (to authorize)
    """

    tenant_id: uuid.UUID
    api_key_id: uuid.UUID | None
    user_id: uuid.UUID | None
    scopes: set[DeveloperScope]

    def has_scope(self, scope: DeveloperScope) -> bool:
        return scope in self.scopes


async def _resolve_via_api_key(
    db: AsyncSession,
    api_key: str,
) -> DeveloperPrincipal:
    """Validate an X-API-Key header and load its scopes."""
    record = await validate_api_key(db, api_key)
    if record is None:
        raise UnauthorizedError("Invalid API key")
    if record.tenant_id is None:
        raise ForbiddenError("API key must be tenant-scoped for the developer API")

    stmt = select(DeveloperAPIKeyScope.scope).where(DeveloperAPIKeyScope.api_key_id == record.id)
    result = await db.execute(stmt)
    scopes = {row[0] for row in result.all()}

    return DeveloperPrincipal(
        tenant_id=record.tenant_id,
        api_key_id=record.id,
        user_id=record.user_id,
        scopes=scopes,
    )


def _principal_from_user(user: CurrentUser) -> DeveloperPrincipal:
    """JWT-authenticated dashboard users implicitly hold all developer scopes
    within their tenant. Cross-tenant access is enforced at the route level."""
    if user.tenant_id is None and not user.is_super_admin():
        raise ForbiddenError("User must belong to a tenant for the developer API")
    # SUPER_ADMIN without tenant context — still allow, route handlers must
    # require a tenant_id query/path param.
    return DeveloperPrincipal(
        tenant_id=user.tenant_id or uuid.UUID(int=0),  # zero UUID = "unset"
        api_key_id=None,
        user_id=user.user_id,
        scopes=set(DeveloperScope),  # all scopes
    )


def require_developer_scope(required: DeveloperScope):
    """FastAPI dependency factory enforcing one scope.

    The dependency resolves the caller via either:
      - X-API-Key header → looks up scopes from DeveloperAPIKeyScope rows
      - Authorization: Bearer <jwt> → all scopes within the user's tenant
    """

    async def _dep(
        request: Request,
        x_api_key: str | None = Header(default=None),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        db: AsyncSession = Depends(get_db_session),
    ) -> DeveloperPrincipal:
        if x_api_key:
            principal = await _resolve_via_api_key(db, x_api_key)
            if required not in principal.scopes:
                raise ForbiddenError(f"API key lacks required scope: {required.value}")
            # Mirror onto request.state for audit middleware
            request.state.tenant_id = principal.tenant_id
            return principal

        # Fall back to JWT
        user = await get_current_user(request, credentials)
        principal = _principal_from_user(user)
        return principal

    return _dep


# Convenience type aliases for route signatures
EventWriter = Annotated[
    DeveloperPrincipal,
    Depends(require_developer_scope(DeveloperScope.EVENTS_WRITE)),
]
EventReader = Annotated[
    DeveloperPrincipal,
    Depends(require_developer_scope(DeveloperScope.EVENTS_READ)),
]
EventTypeWriter = Annotated[
    DeveloperPrincipal,
    Depends(require_developer_scope(DeveloperScope.EVENT_TYPES_WRITE)),
]
EventTypeReader = Annotated[
    DeveloperPrincipal,
    Depends(require_developer_scope(DeveloperScope.EVENT_TYPES_READ)),
]
