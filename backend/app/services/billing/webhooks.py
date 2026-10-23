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

Two kinds of subscription
-------------------------
A tenant's Stripe customer can hold several subscriptions:

* the self-serve **Team** subscription, stored on the tenant. It alone drives
  ``plan``, ``subscription_status`` and ``seats_billed``;
* **module subscriptions**, which carry Promptly prices (Work client,
  Atlas.AI, Learn, Adopt, Suite, platform fee, step overage). These are the
  enterprise Work client subscription, on which modules are items, and the
  invoiced subscription a module Quote creates on acceptance. They drive
  module entitlements only, through ``tenant.stripe_subscriptions``, and
  never touch the Team columns.
"""

from __future__ import annotations

import uuid as _uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import StripeWebhookEvent
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import User
from app.services.billing import enterprise, quotes, stripe_client
from app.services.entitlements import LOOKUP_KEY_MODULES, subscription_items_from_stripe

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Helpers — kept module-level so tests can monkeypatch them
# ---------------------------------------------------------------------------


def _fetch_subscription_item_id(subscription_id: str) -> str | None:
    """Retrieve subscription.items.data[0].id from Stripe.

    Module-level so tests can patch ``app.services.billing.webhooks._fetch_subscription_item_id``.
    """
    sub = stripe_client.retrieve_subscription(subscription_id)
    items = (sub.get("items") or {}).get("data") or []
    return items[0]["id"] if items else None


def _select_tenant(*where, lock: bool):
    stmt = select(Tenant).where(*where)
    if lock:
        # Module-subscription handlers read-modify-write the whole
        # stripe_subscriptions JSON. Two events for one tenant in flight at
        # once would otherwise both read the old value, and the later commit
        # would drop the other's entry. The row lock serialises them;
        # populate_existing makes sure the locked read is the one we use.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return stmt


async def _tenant_by_id(db: AsyncSession, tenant_id: str, *, lock: bool = False) -> Tenant | None:
    try:
        tid = _uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError):
        return None
    return await db.scalar(_select_tenant(Tenant.id == tid, lock=lock))


async def _tenant_by_customer(
    db: AsyncSession, customer_id: str, *, lock: bool = False
) -> Tenant | None:
    return await db.scalar(_select_tenant(Tenant.stripe_customer_id == customer_id, lock=lock))


# ---------------------------------------------------------------------------
# Module subscriptions
# ---------------------------------------------------------------------------

# Metadata markers set by the code that creates each module subscription,
# imported from those writers so the two cannot drift apart:
#   the enterprise Work client subscription (enterprise.py);
#   the subscription an accepted module Quote creates (quotes.py).
_MODULE_SUBSCRIPTION_MARKERS: dict[str, str] = {
    enterprise.KIND_KEY: enterprise.KIND_ENTERPRISE,
    quotes.BILLING_KIND_KEY: quotes.BILLING_KIND,
}


def _module_marker(metadata: dict | None) -> str | None:
    """The marker value if *metadata* marks a module subscription, else None."""
    for key, value in _MODULE_SUBSCRIPTION_MARKERS.items():
        if (metadata or {}).get(key) == value:
            return value
    return None


def _subscription_module_kind(obj: dict) -> str | None:
    """Kind of module subscription *obj* is, or None for a Team subscription.

    By metadata first. Failing that, any item priced from the Promptly
    catalogue makes it a module subscription, so one created without a marker
    (by hand in the Dashboard, say) still cannot overwrite the Team status.
    """
    marker = _module_marker(obj.get("metadata"))
    if marker:
        return marker
    items = subscription_items_from_stripe((obj.get("items") or {}).get("data") or [])
    if any(key in LOOKUP_KEY_MODULES for key in items):
        return "unmarked"
    return None


def _invoice_subscription(obj: dict) -> tuple[str | None, dict]:
    """(subscription id, subscription metadata) of an invoice.

    Older API versions put them at ``subscription`` and
    ``subscription_details``; newer ones under ``parent.subscription_details``.
    """
    details = obj.get("subscription_details") or {}
    parent = (obj.get("parent") or {}).get("subscription_details") or {}
    sub = obj.get("subscription") or parent.get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return sub, details.get("metadata") or parent.get("metadata") or {}


# Stripe statuses a subscription never leaves.
_TERMINAL_STATUSES = frozenset({"canceled", "incomplete_expired"})


def _set_module_subscription(
    tenant: Tenant, sub_id: str, *, created: int | None, **changes
) -> bool:
    """Update one entry of ``tenant.stripe_subscriptions``; False if ignored.

    Stripe does not deliver events in order, so an event is ignored when:

    * it is older (by ``event.created``) than the last one applied to this
      subscription. A delayed ``subscription.updated`` would otherwise bring
      back items removed since, until the next event, which for an annual
      subscription may be a year away;
    * the subscription has already ended. A canceled subscription is never
      reopened, so no later ``updated``, ``invoice.paid`` or
      ``invoice.payment_failed`` (which would hand back the free Work
      client) may revive it.

    Assigns a new dict rather than mutating in place: the column is plain
    JSON, so SQLAlchemy only sees a change when the attribute is reassigned.
    """
    subs = dict(tenant.stripe_subscriptions or {})
    entry = dict(subs.get(sub_id) or {"items": {}})
    last = entry.get("event_created")
    if created is not None and last is not None and created < last:
        log.info(
            "webhook_module_event_out_of_order",
            tenant_id=str(tenant.id),
            subscription=sub_id,
            created=created,
            last_applied=last,
        )
        return False
    if entry.get("status") in _TERMINAL_STATUSES and changes.get("status") not in (
        _TERMINAL_STATUSES
    ):
        log.info(
            "webhook_module_subscription_already_ended",
            tenant_id=str(tenant.id),
            subscription=sub_id,
            status=changes.get("status"),
        )
        return False
    entry.update(changes)
    if created is not None:
        entry["event_created"] = created
    subs[sub_id] = entry
    tenant.stripe_subscriptions = subs
    return True


async def _tenant_for_module_subscription(db: AsyncSession, obj: dict) -> Tenant | None:
    """Find the tenant a module subscription belongs to.

    By customer first. Failing that by ``metadata.tenant_id``, which both
    writers set, and the customer id is stored so later invoice events find
    the tenant too. A tenant already linked to another customer is never
    re-pointed from metadata.
    """
    customer = obj.get("customer") or ""
    tenant = await _tenant_by_customer(db, customer, lock=True)
    if tenant:
        return tenant
    tenant_id = (obj.get("metadata") or {}).get("tenant_id")
    tenant = await _tenant_by_id(db, tenant_id, lock=True) if tenant_id else None
    if tenant is None:
        return None
    if tenant.stripe_customer_id and tenant.stripe_customer_id != customer:
        log.warning(
            "webhook_module_subscription_customer_mismatch",
            tenant_id=str(tenant.id),
            customer=customer,
        )
        return None
    tenant.stripe_customer_id = customer or None
    return tenant


async def _sync_module_subscription(
    db: AsyncSession, obj: dict, kind: str, created: int | None
) -> None:
    """customer.subscription.created/updated/deleted for a module subscription.

    Stores the subscription's status and items. The event carries the full
    item list, so items are replaced, not merged: an item removed from the
    subscription switches its module off.
    """
    tenant = await _tenant_for_module_subscription(db, obj)
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return
    items = (obj.get("items") or {}).get("data") or []
    applied = _set_module_subscription(
        tenant,
        obj["id"],
        created=created,
        kind=kind,
        status=obj.get("status"),
        items=subscription_items_from_stripe(items),
    )
    if not applied:
        return
    log.info(
        "webhook_module_subscription_synced",
        tenant_id=str(tenant.id),
        subscription=obj["id"],
        kind=kind,
        status=obj.get("status"),
    )


async def _module_invoice_target(db: AsyncSession, obj: dict) -> tuple[Tenant, str] | None:
    """(tenant, subscription id) if the invoice is for a module subscription."""
    sub_id, metadata = _invoice_subscription(obj)
    if not sub_id:
        return None
    tenant = await _tenant_by_customer(db, obj.get("customer", ""), lock=True)
    if not tenant or sub_id == tenant.stripe_subscription_id:
        return None
    if _module_marker(metadata) or sub_id in (tenant.stripe_subscriptions or {}):
        return tenant, sub_id
    return None


def _is_other_subscription(tenant: Tenant, obj: dict) -> bool:
    """True if the invoice names a subscription other than the tenant's Team one.

    Such an invoice belongs to a module subscription whose
    ``subscription.created`` has not arrived yet (Stripe does not deliver in
    order), and must not change the Team status.
    """
    sub_id, _ = _invoice_subscription(obj)
    return bool(
        sub_id and tenant.stripe_subscription_id and sub_id != tenant.stripe_subscription_id
    )


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
            await handler(db, obj, created=event.get("created"))
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


async def _on_checkout_completed(
    db: AsyncSession, obj: dict, *, created: int | None = None
) -> None:
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

    # A tenant keeps its first Stripe customer. Checkout reuses it, so a
    # different id here means a session was created some other way; keep the
    # stored one, because invoice and subscription events for it route by it.
    incoming_customer = obj.get("customer")
    if not tenant.stripe_customer_id:
        tenant.stripe_customer_id = incoming_customer
    elif incoming_customer and incoming_customer != tenant.stripe_customer_id:
        log.warning(
            "webhook_checkout_customer_mismatch",
            tenant_id=str(tenant.id),
            stored_customer=tenant.stripe_customer_id,
            incoming_customer=incoming_customer,
        )
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


async def _on_subscription_created(
    db: AsyncSession, obj: dict, *, created: int | None = None
) -> None:
    """customer.subscription.created — records a new module subscription.

    A Team subscription is created by Checkout and handled by
    checkout.session.completed, so it is ignored here.
    """
    kind = _subscription_module_kind(obj)
    if kind:
        await _sync_module_subscription(db, obj, kind, created)


async def _on_subscription_updated(
    db: AsyncSession, obj: dict, *, created: int | None = None
) -> None:
    """customer.subscription.updated — syncs status and seat quantity.

    A module subscription updates module entitlements only.
    """
    kind = _subscription_module_kind(obj)
    if kind:
        await _sync_module_subscription(db, obj, kind, created)
        return

    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    _STATUS_MAP: dict[str, SubscriptionStatus] = {
        "trialing": SubscriptionStatus.TRIALING,
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        # Stripe moves a subscription to unpaid when retries run out and the
        # settings keep it open; for entitlements that is still past_due.
        "unpaid": SubscriptionStatus.PAST_DUE,
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


async def _on_subscription_deleted(
    db: AsyncSession, obj: dict, *, created: int | None = None
) -> None:
    """customer.subscription.deleted — marks subscription as canceled.

    Deleting a module subscription switches off its modules only; the Team
    subscription's status is left alone. Its event carries status=canceled.
    """
    kind = _subscription_module_kind(obj)
    if kind:
        await _sync_module_subscription(db, {**obj, "status": "canceled"}, kind, created)
        return

    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return

    tenant.subscription_status = SubscriptionStatus.CANCELED
    tenant.plan_changed_at = await db.scalar(select(func.now()))

    log.info("webhook_subscription_deleted", tenant_id=str(tenant.id))


async def _on_invoice_payment_failed(
    db: AsyncSession, obj: dict, *, created: int | None = None
) -> None:
    """invoice.payment_failed — sets subscription_status=past_due.

    For a module subscription, marks that subscription past_due instead, which
    switches its paid modules off until an invoice is paid.
    """
    target = await _module_invoice_target(db, obj)
    if target:
        tenant, sub_id = target
        if _set_module_subscription(tenant, sub_id, created=created, status="past_due"):
            log.info("webhook_module_invoice_payment_failed", tenant_id=str(tenant.id))
        return

    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return
    if _is_other_subscription(tenant, obj):
        log.info("webhook_invoice_for_unknown_subscription", tenant_id=str(tenant.id))
        return

    tenant.subscription_status = SubscriptionStatus.PAST_DUE
    log.info("webhook_invoice_payment_failed", tenant_id=str(tenant.id))


async def _on_invoice_paid(db: AsyncSession, obj: dict, *, created: int | None = None) -> None:
    """invoice.paid — clears past_due / confirms active subscription.

    For a module subscription, does the same to that subscription only.
    """
    target = await _module_invoice_target(db, obj)
    if target:
        tenant, sub_id = target
        # A final invoice (step overage in arrears, say) can be paid after the
        # subscription ended. _set_module_subscription refuses to reopen it.
        if _set_module_subscription(tenant, sub_id, created=created, status="active"):
            log.info("webhook_module_invoice_paid", tenant_id=str(tenant.id))
        return

    tenant = await _tenant_by_customer(db, obj.get("customer", ""))
    if not tenant:
        log.warning("webhook_tenant_not_found_by_customer", customer=obj.get("customer"))
        return
    if _is_other_subscription(tenant, obj):
        log.info("webhook_invoice_for_unknown_subscription", tenant_id=str(tenant.id))
        return

    tenant.subscription_status = SubscriptionStatus.ACTIVE
    log.info("webhook_invoice_paid", tenant_id=str(tenant.id))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.created": _on_subscription_created,
    "customer.subscription.updated": _on_subscription_updated,
    "customer.subscription.deleted": _on_subscription_deleted,
    "invoice.payment_failed": _on_invoice_payment_failed,
    "invoice.paid": _on_invoice_paid,
}
