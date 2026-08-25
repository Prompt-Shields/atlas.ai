"""Device-bound credential: opaque token, sha256-hashed at rest. Mirrors
app.auth.api_keys hashing so ops/reasoning stays uniform."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, set_tenant_guc
from app.errors import UnauthorizedError
from app.models.enrolled_device import EnrolledDevice

_TOKEN_BYTES = 32
_PREFIX = "psd_"  # prompt-shields device


def generate_device_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Persist only the hash."""
    raw = _PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_device_token(raw)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class DeviceContext:
    device_id: uuid.UUID
    tenant_id: uuid.UUID


async def require_device_token(
    db: AsyncSession = Depends(get_db_session),
    x_device_token: str | None = Header(None),
) -> DeviceContext:
    """Resolve the calling device from its bound token via the SECURITY DEFINER
    bootstrap function (RLS-exempt, since no tenant is known yet), then set the
    tenant GUC so RLS applies to every subsequent query in this session."""
    if not x_device_token:
        raise UnauthorizedError("X-Device-Token header required")
    token_hash = hash_device_token(x_device_token)

    # Bootstrap lookup: we don't yet know the tenant, so this must not go
    # through tenant-scoped RLS. On Postgres use the SECURITY DEFINER helper
    # which bypasses RLS for this one narrow lookup. The test harness builds the
    # schema with create_all on sqlite (no RLS, helper function absent), so fall
    # back to a direct ORM select — equivalent because sqlite has no row security.
    if db.get_bind().dialect.name == "postgresql":
        row = (
            await db.execute(
                text(
                    "SELECT device_id, tenant_id, enrollment_state "
                    "FROM grc.resolve_device_by_token(:h)"
                ),
                {"h": token_hash},
            )
        ).one_or_none()
        found = None if row is None else (row.device_id, row.tenant_id, row.enrollment_state)
    else:
        obj = (
            await db.execute(select(EnrolledDevice).where(EnrolledDevice.token_hash == token_hash))
        ).scalar_one_or_none()
        found = None if obj is None else (obj.id, obj.tenant_id, obj.enrollment_state)

    if found is None or found[2] != "active":
        raise UnauthorizedError("Invalid device token")
    device_id, tenant_id, _ = found
    await set_tenant_guc(db, tenant_id)
    return DeviceContext(device_id=device_id, tenant_id=tenant_id)
