"""Seat sync service — real implementation (replaces Phase 2 stub).

``on_first_login(db, user)`` is the entry point called from
``auth_service.login()`` on a user's first successful login.  It resolves
the activated-user count for the tenant and delegates to
``sync_seat_count()`` which holds the Stripe guard + exception routing.

Resilience contract
-------------------
* Pre-Stripe guard: if the tenant has no active subscription
  (``stripe_subscription_item_id is None`` OR ``subscription_status`` is
  ``None`` or ``CANCELED``) the function returns immediately — no Stripe
  call, no error.
* Transient Stripe errors (``APIConnectionError``, ``RateLimitError``,
  ``APIError``) are caught and the operation is enqueued in
  ``billing_outbox`` for later retry.
* ``InvalidRequestError`` (logic / data bug) is logged + alerted; it is
  **NOT** enqueued (retrying a logic bug is pointless).
* Queued rows are sent by ``seat_outbox`` (``scripts/stripe_seat_sync.py``).
* ``on_first_login`` additionally wraps its entire body so that any
  unexpected exception (e.g. a failed DB query before the Stripe call) is
  logged and swallowed — a billing glitch must never cause a login 500.
"""

from __future__ import annotations

import uuid

import stripe
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import SubscriptionStatus, Tenant
from app.models.user import User
from app.services.billing import outbox, stripe_client

log = structlog.get_logger()
SEAT_FLOOR = 5
OPERATION = "sync_seat"


async def activated_users(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """The tenant's activated users: those who have logged in at least once."""
    return (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.first_login_at.is_not(None),
            )
        )
    ) or 0


def seat_quantity(activated: int) -> int:
    """The seat quantity billed for ``activated`` users (never below the floor)."""
    return max(SEAT_FLOOR, activated)


def has_active_subscription(tenant: Tenant) -> bool:
    """False during a trial and after cancellation: then no seat is synced."""
    return tenant.stripe_subscription_item_id is not None and tenant.subscription_status not in (
        None,
        SubscriptionStatus.CANCELED,
    )


async def on_first_login(db: AsyncSession, user: User) -> None:
    """Hook called on a user's first successful login.

    Computes the activated-user count for the tenant and delegates to
    ``sync_seat_count``.  Any exception is caught here so that a billing
    failure can never propagate out and turn a login into a 500.
    """
    try:
        if not user.tenant_id:
            return

        tenant = await db.scalar(select(Tenant).where(Tenant.id == user.tenant_id))
        if tenant is None:
            return

        activated = await activated_users(db, tenant.id)
        await sync_seat_count(db, tenant, activated_users=activated)
    except Exception:
        # Login must never 500 due to seat-sync — log and swallow.
        log.exception("seat_sync_on_first_login_failed", tenant_id=str(user.tenant_id))


async def sync_seat_count(db: AsyncSession, tenant: Tenant, *, activated_users: int) -> None:
    """Sync seat count to Stripe with pre-Stripe guard and exception routing.

    Pre-Stripe guard: returns immediately (no-op) when the tenant has no
    active Stripe subscription — during a free trial or after cancellation.

    On success the updated ``seats_billed`` value is flushed to the DB.

    Exception routing:
    * Transient Stripe errors → enqueue to ``billing_outbox`` for retry.
    * ``InvalidRequestError`` (logic bug) → log + alert, do NOT enqueue.
    * Neither branch re-raises — the caller (login) must always proceed.
    """
    # Pre-Stripe guard — no-op during trial and after cancellation.
    if not has_active_subscription(tenant):
        return

    quantity = seat_quantity(activated_users)

    try:
        stripe_client.modify_subscription_item(
            tenant.stripe_subscription_item_id,
            quantity=quantity,
            proration_behavior="create_prorations",
        )
        tenant.seats_billed = quantity
        await db.flush()
    except (
        stripe.APIConnectionError,
        stripe.RateLimitError,
        stripe.APIError,
    ):
        await outbox.enqueue(
            db,
            operation=OPERATION,
            tenant_id=tenant.id,
            payload={"quantity": quantity},
        )
    except stripe.InvalidRequestError:
        # Logic bug — alert (e.g. Sentry), do NOT enqueue.  Never re-raise.
        log.exception("stripe_seat_sync_invalid_request", tenant_id=str(tenant.id))
    # Any path: return; caller (login) proceeds.
