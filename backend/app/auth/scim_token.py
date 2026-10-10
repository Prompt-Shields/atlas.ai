"""The SCIM bearer token a tenant's identity provider presents (promptly-guide #58).
Opaque, sha256-hashed at rest, one per tenant; resolved before any tenant is known,
the way `device_token` is."""

from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import bearer_scheme
from app.database import get_db_session, set_tenant_guc
from app.errors import UnauthorizedError
from app.models.guide_scim import GuideScimToken

_PREFIX = "pss_"  # prompt-shields SCIM
_TOKEN_BYTES = 32


def generate_scim_token() -> tuple[str, str]:
    """(raw token, sha256). Keep only the hash."""
    raw = _PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_scim_token(raw)


def hash_scim_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def require_scim_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> uuid.UUID:
    """The tenant whose identity provider is calling. Sets the tenant GUC."""
    if credentials is None or not credentials.credentials.startswith(_PREFIX):
        raise UnauthorizedError("SCIM token required")
    token_hash = hash_scim_token(credentials.credentials)
    # Pre-tenant lookup: SECURITY DEFINER helper on Postgres (RLS-exempt for this one
    # keyed read); the sqlite test schema has no RLS and no helper.
    if db.get_bind().dialect.name == "postgresql":
        row = (
            await db.execute(
                text("SELECT tenant_id FROM grc.resolve_guide_scim_token(:h)"), {"h": token_hash}
            )
        ).one_or_none()
        tenant_id = None if row is None else row.tenant_id
    else:
        tenant_id = (
            await db.execute(
                select(GuideScimToken.tenant_id).where(GuideScimToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()
    if tenant_id is None:
        raise UnauthorizedError("Invalid SCIM token")
    await set_tenant_guc(db, tenant_id)
    return tenant_id
