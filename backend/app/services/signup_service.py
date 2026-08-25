"""Atomic tenant + admin-user signup / provisioning service.

Two entry points share one `provision_tenant()` core:

* `signup()`     — password-based self-serve signup (email + password form).
* `provision_or_join_sso_user()` — Entra ID SSO self-serve (SP-Azure): the
  first user of an Azure directory (`tid`) provisions a tenant + admin; later
  users from the same `tid` join it as members. Both are no-card 14-day trials.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import AppException
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import Role, User
from app.services.auth_service import create_user, login
from app.services.email_service import send_email

logger = structlog.get_logger()

TRIAL_DAYS = 14

# Role granted to SSO users who join an already-provisioned tenant. The first
# user of an Azure directory becomes TENANT_ADMIN; subsequent auto-joined users
# get a non-admin working role rather than admin rights.
JIT_MEMBER_ROLE = Role.ANALYST


async def provision_tenant(
    db: AsyncSession,
    *,
    tenant_name: str,
    tenant_slug: str,
    admin_email: str,
    admin_full_name: str = "",
    admin_password: str | None = None,
    azure_tenant_id: str | None = None,
    admin_is_email_verified: bool = False,
) -> tuple[Tenant, User]:
    """Create a new tenant + its TENANT_ADMIN user atomically.

    - Sets plan=FREE, subscription_status=TRIALING, trial_ends_at=now+14d.
    - `admin_password=None` provisions an SSO-only admin (no local password).
    - `azure_tenant_id` records the Entra `tid` this tenant was created from.
    - Raises AppException(422, slug_taken) on duplicate slug and ConflictError
      (from create_user) on duplicate admin email — both roll back the flushed
      tenant row since they share this transaction.

    Caller owns the commit. Returns (tenant, admin_user).
    """
    existing_slug = await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
    if existing_slug.scalar_one_or_none():
        raise AppException(
            code="slug_taken",
            message="A tenant with this slug already exists",
            status_code=422,
        )

    now = datetime.now(UTC)
    tenant = Tenant(
        name=tenant_name,
        slug=tenant_slug,
        is_active=True,
        plan=Plan.FREE,
        subscription_status=SubscriptionStatus.TRIALING,
        trial_ends_at=now + timedelta(days=TRIAL_DAYS),
        azure_tenant_id=azure_tenant_id,
    )
    db.add(tenant)
    await db.flush()

    admin = await create_user(
        db,
        email=admin_email,
        password=admin_password,
        full_name=admin_full_name or admin_email.split("@")[0],
        role=Role.TENANT_ADMIN,
        tenant_id=tenant.id,
        is_email_verified=admin_is_email_verified,
    )
    await db.flush()
    return tenant, admin


async def signup(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    tenant_name: str,
    tenant_slug: str,
    full_name: str = "",
) -> dict:
    """Create a new tenant + TENANT_ADMIN user atomically (password signup).

    - Raises ConflictError(409) on duplicate email; AppException(422) on slug.
    - Sets subscription_status=TRIALING, plan=FREE, trial_ends_at=now+14d.
    - Verification email is fire-and-forget / non-fatal.
    - Returns a token dict with tenant_id.
    """
    tenant, _admin = await provision_tenant(
        db,
        tenant_name=tenant_name,
        tenant_slug=tenant_slug,
        admin_email=email,
        admin_full_name=full_name,
        admin_password=password,
        admin_is_email_verified=False,
    )

    # --- Issue tokens (reuses existing login machinery) -----------------------
    # login() calls authenticate_user() which reads the just-flushed user row
    # — visible in the same session/transaction.
    tokens = await login(db, email=email, password=password)

    # --- Verification email: fire-and-forget / non-fatal ----------------------
    try:
        await send_email(
            email,
            "Verify your Atlas account",
            "<p>Welcome to Atlas. Please verify your email to enable upgrades.</p>",
        )
    except Exception:
        logger.warning("verification_email_send_failed", email=email)

    return {**tokens, "tenant_id": str(tenant.id)}


# ─── Entra ID SSO self-serve (SP-Azure) ──────────────────────────────


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "tenant"


async def _unique_slug(db: AsyncSession, base: str, *, discriminator: str) -> str:
    """Return `base`, or `base-<discriminator>` if `base` is already taken."""
    taken = (await db.execute(select(Tenant.id).where(Tenant.slug == base))).first()
    if taken is None:
        return base
    return f"{base}-{discriminator[:8]}"


async def provision_or_join_sso_user(
    db: AsyncSession,
    *,
    azure_tenant_id: str,
    email: str,
    entra_object_id: str,
    full_name: str = "",
) -> User:
    """JIT-provision an Entra SSO user (SP-Azure self-serve signup).

    - First user of `azure_tenant_id` (no tenant maps to that `tid` yet):
      create a tenant (14-day trial) + this user as TENANT_ADMIN.
    - Later users of the same `tid`: join the existing tenant as members
      (`JIT_MEMBER_ROLE`).

    The Entra email is treated as verified (Microsoft asserts it). Callers
    reach this only after link-only resolution has already failed, so no user
    with this email/oid exists yet. Caller owns the commit.
    """
    email = email.lower()
    tenant = (
        await db.execute(select(Tenant).where(Tenant.azure_tenant_id == azure_tenant_id))
    ).scalar_one_or_none()

    if tenant is None:
        # First user from this Azure directory → new tenant + admin.
        domain = email.split("@")[-1]
        base_slug = _slugify(domain)
        slug = await _unique_slug(db, base_slug, discriminator=azure_tenant_id)
        _tenant, user = await provision_tenant(
            db,
            tenant_name=domain,
            tenant_slug=slug,
            admin_email=email,
            admin_full_name=full_name,
            admin_password=None,
            azure_tenant_id=azure_tenant_id,
            admin_is_email_verified=True,
        )
        logger.info(
            "sso_jit_tenant_provisioned",
            tenant_id=str(_tenant.id),
            azure_tenant_id=azure_tenant_id,
        )
    else:
        # Subsequent user from a known Azure directory → join as member.
        user = await create_user(
            db,
            email=email,
            password=None,
            full_name=full_name or email.split("@")[0],
            role=JIT_MEMBER_ROLE,
            tenant_id=tenant.id,
            is_email_verified=True,
        )
        logger.info(
            "sso_jit_member_joined",
            tenant_id=str(tenant.id),
            azure_tenant_id=azure_tenant_id,
        )

    user.entra_object_id = entra_object_id
    await db.flush()

    # Reload with roles eagerly loaded — the caller (complete_sso_login →
    # issue_tokens) reads user.roles, which would otherwise lazy-load and raise
    # MissingGreenlet in the async session.
    return (
        await db.execute(select(User).where(User.id == user.id).options(selectinload(User.roles)))
    ).scalar_one()
