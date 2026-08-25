"""Billing routes: checkout, portal, entitlements.

Two routers are exported from this module:
- ``router``:          prefix=/billing  — authenticated billing endpoints
- ``webhooks_router``: prefix=/webhooks — unauthenticated Stripe webhook receiver
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser
from app.config import get_settings
from app.database import get_db_session
from app.errors import AppException, ForbiddenError, NotFoundError
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.billing import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    EntitlementsResponse,
    PortalSessionResponse,
)
from app.services import entitlements as ent
from app.services.billing import stripe_client
from app.services.billing import webhooks as wh

router = APIRouter(prefix="/billing", tags=["Billing"])

# Separate router — NO auth dependency; verified by Stripe signature only.
webhooks_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def _load_tenant(db: AsyncSession, user: AuthUser) -> Tenant:
    if not user.tenant_id:
        raise ForbiddenError("Tenant context required")
    t = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
    if not t:
        raise NotFoundError("Tenant")
    return t


async def _activated_count(db: AsyncSession, tenant_id) -> int:
    return (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.first_login_at.is_not(None),
            )
        )
    ) or 0


async def _is_email_verified(db: AsyncSession, user: AuthUser) -> bool:
    u = await db.scalar(select(User).where(User.id == user.user_id))
    return bool(u and u.is_email_verified)


@router.post("/checkout-session", response_model=CheckoutSessionResponse)
async def checkout_session(
    body: CheckoutSessionRequest,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> CheckoutSessionResponse:
    """Create a Stripe Checkout Session for the tenant to subscribe."""
    if not await _is_email_verified(db, user):
        raise AppException(
            code="email_not_verified",
            message="Verify your email before upgrading",
            status_code=403,
        )

    settings = get_settings()
    tenant = await _load_tenant(db, user)
    price = (
        settings.stripe_price_team_monthly
        if body.interval == "monthly"
        else settings.stripe_price_team_annual
    )
    activated = await _activated_count(db, tenant.id)
    quantity = max(5, activated)

    # Use DB server time to avoid clock-skew issues
    now = await db.scalar(select(func.now()))

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": quantity}],
        "customer_email": user.email,
        "automatic_tax": {"enabled": True},
        "client_reference_id": str(tenant.id),
        "metadata": {"tenant_id": str(tenant.id)},
        "success_url": settings.billing_checkout_success_url,
        "cancel_url": settings.billing_checkout_cancel_url,
    }

    if tenant.trial_ends_at is not None and now is not None:
        remaining = math.ceil((tenant.trial_ends_at - now).total_seconds() / 86400)
        if remaining >= 1:
            params["subscription_data"] = {"trial_period_days": remaining}

    session = stripe_client.create_checkout_session(**params)
    return CheckoutSessionResponse(url=session.url)


@router.post("/portal-session", response_model=PortalSessionResponse)
async def portal_session(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> PortalSessionResponse:
    """Create a Stripe Customer Portal session for the tenant."""
    tenant = await _load_tenant(db, user)
    if not tenant.stripe_customer_id:
        raise AppException(
            code="no_customer",
            message="No Stripe customer for this tenant",
            status_code=409,
        )
    session = stripe_client.create_portal_session(
        customer=tenant.stripe_customer_id,
        return_url=get_settings().billing_portal_return_url,
    )
    return PortalSessionResponse(url=session.url)


@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_entitlements(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> EntitlementsResponse:
    """Return the tenant's current plan entitlements and lock state."""
    tenant = await _load_tenant(db, user)
    # Source `now` from the DB server (never Python datetime.now())
    now = await db.scalar(select(func.now()))
    activated = await _activated_count(db, tenant.id)
    caps = [c.value for c in ent.PLAN_ENTITLEMENTS.get(tenant.plan, set())]
    return EntitlementsResponse(
        plan=tenant.plan.value,
        subscription_status=(
            tenant.subscription_status.value if tenant.subscription_status else None
        ),
        trial_ends_at=tenant.trial_ends_at,
        seats_used=activated,
        seats_billed=tenant.seats_billed,
        capabilities=caps,
        hard_locked=ent.hard_locked(tenant, now=now),
    )


# ---------------------------------------------------------------------------
# Stripe webhook receiver — NO auth (verified by Stripe signature)
# ---------------------------------------------------------------------------


@webhooks_router.post("/stripe", include_in_schema=True)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Receive and process Stripe webhook events.

    - Verifies the ``Stripe-Signature`` header via ``stripe_client.construct_event``.
      Bad/missing signature → 400 (Stripe stops retrying after a few attempts).
    - Dispatches to ``wh.process_event()`` which inserts the idempotency log row
      in the **same** DB transaction as every handler mutation.
    - Duplicate event id → 200 no-op (idempotency).
    - Unknown event type → 200 acknowledge.
    - Transient handler exception → 500 (Stripe retries with backoff).
    """
    payload: bytes = await request.body()
    sig: str = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe_client.construct_event(payload, sig)
    except Exception:
        # Bad or missing signature — tell Stripe to stop retrying.
        return Response(status_code=400)

    await wh.process_event(db, dict(event))
    return Response(status_code=200)
