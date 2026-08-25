"""Invite service — manages tenant invitation lifecycle."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppException, ConflictError, NotFoundError
from app.models.invite import Invite
from app.models.user import Role
from app.services.auth_service import create_user
from app.services.tenant_service import activate_tenant

INVITE_TOKEN_BYTES = 32
INVITE_EXPIRY_HOURS = 72


def _generate_invite_token() -> tuple[str, str]:
    """Generate a signed invite token. Returns (raw_token, token_hash)."""
    raw = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


async def create_invite(
    db: AsyncSession,
    *,
    email: str,
    tenant_id: uuid.UUID,
    invited_by: uuid.UUID,
    role: str = "TENANT_ADMIN",
) -> tuple[Invite, str]:
    """Create an invite. Returns (invite, raw_token)."""
    # Check for existing active invite
    existing = await db.execute(
        select(Invite).where(
            Invite.email == email,
            Invite.tenant_id == tenant_id,
            Invite.is_used.is_(False),
            Invite.is_revoked.is_(False),
            Invite.expires_at > datetime.now(UTC),
        )
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Active invite already exists for this email and tenant")

    raw_token, token_hash = _generate_invite_token()
    invite = Invite(
        email=email,
        tenant_id=tenant_id,
        invited_by=invited_by,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=INVITE_EXPIRY_HOURS),
        role=role,
    )
    db.add(invite)
    await db.flush()
    return invite, raw_token


async def confirm_invite(
    db: AsyncSession,
    *,
    token: str,
    password: str,
    full_name: str,
) -> dict:
    """Confirm an invite: validate token, create user, activate tenant."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(Invite).where(
            Invite.token_hash == token_hash,
            Invite.is_used.is_(False),
            Invite.is_revoked.is_(False),
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise NotFoundError("Invite")

    if invite.expires_at < datetime.now(UTC):
        raise AppException(
            code="INVITE_EXPIRED",
            message="This invite has expired",
            status_code=410,
        )

    # Create user
    role = Role(invite.role)
    user = await create_user(
        db,
        email=invite.email,
        password=password,
        full_name=full_name,
        role=role,
        tenant_id=invite.tenant_id,
        is_email_verified=True,
    )

    # Mark invite as used
    invite.is_used = True

    # Activate tenant if not already active
    await activate_tenant(db, invite.tenant_id)

    await db.flush()
    return {"user_id": str(user.id), "tenant_id": str(invite.tenant_id)}


async def list_invites(
    db: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Invite], int]:
    """List invites, optionally filtered by tenant."""
    query = select(Invite)
    count_query = select(func.count(Invite.id))

    if tenant_id:
        query = query.where(Invite.tenant_id == tenant_id)
        count_query = count_query.where(Invite.tenant_id == tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(Invite.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(result.scalars().all()), total
