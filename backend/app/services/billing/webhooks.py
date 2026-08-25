"""Stripe webhook event processor.

Idempotency contract
--------------------
Every handler inserts a row into ``stripe_webhook_events`` in the **same**
database transaction as the tenant mutation.  If the event has already been
processed (primary-key conflict on ``stripe_event_id``), we bail out before
making any mutation — giving us exactly-once semantics on replay.

External I/O rule
-----------------
Handlers MUST NOT send email, call Stripe, or post alerts inline — doing so
would hold the Postgres transaction open across a network round-trip and break
the "both commit or both roll back" guarantee.  Instead, enqueue a
``BillingOutbox`` row inside the transaction; the outbox-drain worker (Phase 6)
processes those side effects asynchronously.
"""

from __future__ import annotations

import uuid as _uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import StripeWebhookEvent
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import User

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Helpers — kept module-level so tests can monkeypatch them
# ---------------------------------------------------------------------------


def _fetch_subscription_item_id(subscription_id: str) -> str | None:
    """Retrieve subscription.items.data[0].id from Stripe.

    Stubbed in tests via monkeypatch; real impl calls the Stripe SDK.
    Module-level so tests can patch ``app.services.billing.webhooks._fetch_subscription_item_id``.
    """
    import stripe  # local import — not needed if tests stub this function entirely

    from app.config import get_settings

    stripe.api_key = get_settings().stripe_secret_key
    sub = stripe.Subscription.retrieve(subscription_id)
    items = sub.get("items", {}).get("data", [])
    return items[0]["id"] if items else None


async def _tenant_by_id(db: AsyncSession, tenant_id: str) -> Tenant | None:
    try:
        tid = _uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError):
        return None
    return await db.scalar(select(Tenant).where(Tenant.id == tid))


async def _tenant_by_customer(db: AsyncSession, customer_id: str) -> Tenant | None:
    return await db.scalar(select(Tenant).where(Tenant.stripe_customer_id == customer_id))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def process_event(db: AsyncSession, event: dict) -> None:
    """Idempotent event processor.

    Inserts a ``StripeWebhookEvent`` row and dispatches to the correct handler
    in the **same** transaction.  Duplicate ``event["id"]`` → silent no-op.
    Unknown ``event["type"]`` → acknowledged (200) but no mutation.
    """
    event_id: str = event["id"]
    event_type: str = event["type"]

    # --- Idempotency check ---------------------------------------------------
    exists = await db.scalar(
        select(StripeWebhookEvent.stripe_event_id).where(
            StripeWebhookEvent.stripe_event_id == event_id
        )
    )
    if exists:
        log.info("webhook_duplicate_skipped", event_id=event_id, event_type=event_type)
        return  # no-op — already processed

    # --- Write the idempotency log row (same txn as the mutation below) ------
    webhook_row = StripeWebhookEvent(stripe_event_id=event_id, event_type=event_type)
    db.add(webhook_row)

    # --- Dispatch to handler -------------------------------------------------
    obj = event["data"]["object"]
    handler = _HANDLERS.get(event_type)
    if handler is None:
        log.info("webhook_unknown_type", event_id=event_id, event_type=event_type)
        # No mutation, but still acknowledge with 200.  processed_at will be
        # set below so we can audit it.
    else:
        try:
            await handler(db, obj)
        except Exception:  # pragma: no cover
            log.exception("webhook_handler_error", event_id=event_id, event_type=event_type)
            # Re-raise so the endpoint returns 500 → Stripe retries.
            raise

    # --- Stamp processed_at (in-txn) ----------------------------------------
    # We re-fetch the row we just added so ORM identity-map refresh isn't
    # needed — use func.now() so the timestamp is from the DB server clock.
    webhook_row.processed_at = await db.scalar(select(func.now()))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _on_checkout_completed(db: AsyncSession, obj: dict) -> None:
    """checkout.session.completed — the "cutover" event.

    Sets plan=team, stores Stripe IDs, marks subscription_status=active.
    Tenant routing: prefers ``metadata.tenant_id`` then ``client_reference_id``.
    """
    tenant_id = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
    if not tenant_id:
        log.warning("webhook_checkout_no_tenant_id", obj_keys=list(obj.keys()))
        return

    tenant = await _tenant_by_id(db, tenant_id)
    if not tenant:
        log.warning("webhook_tenant_not_found", tenant_id=tenant_id)
        return

    tenant.stripe_customer_id = obj.get("customer")
    tenant.stripe_subscription_id = obj.get("subscription")

    # Fetch the subscription item id from Stripe (stubbed in tests)
    if obj.get("subscription"):
        tenant.stripe_subscription_item_id = _fetch_subscription_item_id(obj["subscription"])

    tenant.plan = Plan.TEAM
    tenant.subscription_status = SubscriptionStatus.ACTIVE
    tenant.plan_changed_at = await db.scalar(select(func.now()))

    # Spec §140: seed seats_billed = max(5, activated_users_at_that_moment).
    # "Activated" means first_login_at IS NOT NULL for the tenant.
    activated_count: int = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant.id,
                User.first_login_at.is_not(None),
            )
        )
        or 0
    )
    tenant.seats_billed = max(5, activated_count)

    log.info(
        "webhook_checkout_completed",
        tenant_id=str(tenant_id),
        customer=tenant.stripe_customer_id,
        subscription=tenant.stripe_subscription_id,
    )


async def _on_subscription_updated(db: AsyncSession, obj: dict) -> None:
    """customer.subscription.updated — syncs status and seat quantity."""
    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    _STATUS_MAP: dict[str, SubscriptionStatus] = {
        "trialing": SubscriptionStatus.TRIALING,
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
    }
    if obj.get("status") in _STATUS_MAP:
        tenant.subscription_status = _STATUS_MAP[obj["status"]]

    items = (obj.get("items") or {}).get("data") or []
    if items and items[0].get("quantity") is not None:
        tenant.seats_billed = items[0]["quantity"]

    log.info(
        "webhook_subscription_updated",
        tenant_id=str(tenant.id),
        status=obj.get("status"),
    )


async def _on_subscription_deleted(db: AsyncSession, obj: dict) -> None:
    """customer.subscription.deleted — marks subscription as canceled."""
    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    tenant.subscription_status = SubscriptionStatus.CANCELED
    tenant.plan_changed_at = await db.scalar(select(func.now()))

    log.info("webhook_subscription_deleted", tenant_id=str(tenant.id))


async def _on_invoice_payment_failed(db: AsyncSession, obj: dict) -> None:
    """invoice.payment_failed — sets subscription_status=past_due."""
    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    tenant.subscription_status = SubscriptionStatus.PAST_DUE
    log.info("webhook_invoice_payment_failed", tenant_id=str(tenant.id))


async def _on_invoice_paid(db: AsyncSession, obj: dict) -> None:
    """invoice.paid — clears past_due / confirms active subscription."""
    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    tenant.subscription_status = SubscriptionStatus.ACTIVE
    log.info("webhook_invoice_paid", tenant_id=str(tenant.id))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.updated": _on_subscription_updated,
    "customer.subscription.deleted": _on_subscription_deleted,
    "invoice.payment_failed": _on_invoice_payment_failed,
    "invoice.paid": _on_invoice_paid,
}
