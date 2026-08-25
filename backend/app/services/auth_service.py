"""Authentication service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.api_keys import generate_api_key
from app.auth.jwt import create_access_token, create_refresh_token, verify_refresh_token
from app.auth.passwords import hash_password, verify_password
from app.auth.token_store import consume_refresh_token, store_refresh_token
from app.config import get_settings
from app.errors import ConflictError, UnauthorizedError
from app.models.user import APIKey, Role, User, UserRole


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Authenticate user by email and password."""
    result = await db.execute(
        select(User)
        .where(User.email == email, User.is_active.is_(True))
        .options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        raise UnauthorizedError("Invalid email or password")
    return user


async def login(db: AsyncSession, email: str, password: str) -> dict:
    """Login and return token pair."""
    user = await authenticate_user(db, email, password)
    return await issue_tokens(db, user)


async def issue_tokens(db: AsyncSession, user: User) -> dict:
    """Issue an access/refresh token pair for an already-authenticated user.

    Shared by password login and SSO login (PRO-55). Runs the first-login
    billing hook and stores the refresh-token JTI in Redis for revocation.
    Caller is responsible for committing the session.
    """
    # First-login hook: stamp first_login_at (idempotent — only set when NULL)
    if user.first_login_at is None:
        from sqlalchemy import func

        from app.services.billing.seat_sync import on_first_login  # local import avoids cycle

        user.first_login_at = await db.scalar(select(func.now()))
        await db.flush()
        await on_first_login(db, user)  # Phase 2 no-op stub; Phase 4 adds real Stripe sync

    settings = get_settings()
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        roles=[r.role.value for r in user.roles],
        tenant_id=user.tenant_id,
        org_id=user.org_id,
    )
    refresh_token = create_refresh_token(user_id=user.id)

    # Store refresh token JTI in Redis for revocation tracking
    from app.auth.jwt import decode_token

    rt_payload = decode_token(refresh_token)
    ttl = settings.jwt_refresh_token_expire_days * 86400
    await store_refresh_token(user.id, rt_payload["jti"], ttl)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> dict:
    """Refresh token pair using a valid refresh token.

    Implements refresh-token rotation with replay detection:
    - Each refresh token can only be used once.
    - If a revoked token is reused, all tokens for that user are invalidated.
    """
    from jose import JWTError

    try:
        payload = verify_refresh_token(refresh_token)
    except JWTError:
        raise UnauthorizedError("Invalid refresh token")

    user_id = uuid.UUID(payload["sub"])
    jti = payload.get("jti")
    if not jti:
        raise UnauthorizedError("Malformed refresh token")

    # Consume the old token — returns False on replay (already used)
    was_valid = await consume_refresh_token(jti, user_id)
    if not was_valid:
        raise UnauthorizedError("Refresh token reuse detected — all sessions revoked")

    result = await db.execute(
        select(User)
        .where(User.id == user_id, User.is_active.is_(True))
        .options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise UnauthorizedError("User not found or inactive")

    settings = get_settings()
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        roles=[r.role.value for r in user.roles],
        tenant_id=user.tenant_id,
        org_id=user.org_id,
    )
    new_refresh_token = create_refresh_token(user_id=user.id)

    # Store the new refresh token JTI
    from app.auth.jwt import decode_token

    rt_payload = decode_token(new_refresh_token)
    ttl = settings.jwt_refresh_token_expire_days * 86400
    await store_refresh_token(user.id, rt_payload["jti"], ttl)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str | None,
    full_name: str,
    role: Role = Role.VIEWER,
    tenant_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    is_email_verified: bool = False,
    is_test_data: bool = False,
) -> User:
    """Create a new user with the specified role.

    ``password`` may be ``None`` for SSO-only users (e.g. Entra ID), who
    authenticate without a local password; ``hashed_password`` is left NULL.
    """
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise ConflictError("A user with this email already exists")

    user = User(
        email=email,
        hashed_password=hash_password(password) if password else None,
        full_name=full_name,
        tenant_id=tenant_id,
        org_id=org_id,
        is_active=True,
        is_email_verified=is_email_verified,
        is_test_data=is_test_data,
    )
    db.add(user)
    await db.flush()

    user_role = UserRole(
        user_id=user.id,
        role=role,
        tenant_id=tenant_id,
        org_id=org_id,
    )
    db.add(user_role)
    await db.flush()

    return user


async def create_api_key_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    description: str | None = None,
    tenant_id: uuid.UUID | None = None,
    is_test_data: bool = False,
) -> tuple[str, APIKey]:
    """Create a new API key for a user. Returns (raw_key, api_key_record)."""
    full_key, key_prefix, hashed_key = generate_api_key()

    api_key = APIKey(
        user_id=user_id,
        tenant_id=tenant_id,
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        name=name,
        description=description,
        is_test_data=is_test_data,
    )
    db.add(api_key)
    await db.flush()
    return full_key, api_key


async def bootstrap_super_admin(db: AsyncSession, email: str, password: str) -> User | None:
    """Create the super admin user if it doesn't exist. Called on startup."""
    import re

    import structlog

    _logger = structlog.get_logger()

    if len(password) < 12:
        _logger.error("super_admin_password_too_short", min_length=12)
        raise ValueError("Super admin password must be at least 12 characters")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        raise ValueError("Super admin password must contain upper and lowercase letters")
    if not re.search(r"\d", password):
        raise ValueError("Super admin password must contain at least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("Super admin password must contain at least one special character")

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return None

    return await create_user(
        db,
        email=email,
        password=password,
        full_name="Super Admin",
        role=Role.SUPER_ADMIN,
        is_email_verified=True,
    )
