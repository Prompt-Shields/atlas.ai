"""Enterprise subscriptions for Promptly: the free Work client plus paid modules.

Business plan, "Stripe setup" (Prompt-Shields/promptly-guide#45, #46, #47):

* Every enterprise gets one subscription from day one, holding the €0 Work
  client price with ``quantity`` = provisioned users.
* Each paid module (Atlas.AI, Learn, Adopt, Suite) is a subscription item on
  that same subscription, with ``quantity`` = licensed users.
* The €10,000 platform fee is added once, with the first paid module, and is
  waived while the customer is a pilot.

Stripe is the source of truth. The subscription is found by listing the
customer's subscriptions and matching the Work client lookup key, which is
strongly consistent (``Subscription.search`` is not). No new tenant columns
are needed: the tenant's existing ``stripe_customer_id`` links it to Stripe.
The Team-plan columns (``stripe_subscription_id`` and
``stripe_subscription_item_id``) are deliberately left alone, so Atlas's
self-serve checkout and seat sync keep working as before.

Every change to items is one ``Subscription.modify`` call, so a module and
the platform fee are added together or not at all.

These functions let Stripe errors propagate: they are called from admin or
sales actions, where a failure should be visible, not retried silently.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.services.billing import catalog, stripe_client

# Subscription metadata. The webhook handlers use KIND_KEY to tell this
# subscription apart from a Team-plan one on the same customer.
KIND_KEY = "promptly_subscription"
KIND_ENTERPRISE = "enterprise"
PILOT_KEY = "promptly_pilot"

# Enterprises pay by invoice; there is no card on file for a free client.
DAYS_UNTIL_DUE = 30

_LIVE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid", "incomplete", "paused"})


class EnterpriseBillingError(Exception):
    """Base class for errors a caller should show rather than retry."""


class NoEnterpriseSubscription(EnterpriseBillingError):
    pass


class ModuleConflict(EnterpriseBillingError):
    pass


def is_enterprise_subscription(obj: dict | None) -> bool:
    return ((obj or {}).get("metadata") or {}).get(KIND_KEY) == KIND_ENTERPRISE


def is_pilot(subscription) -> bool:
    return (subscription.get("metadata") or {}).get(PILOT_KEY) == "true"


def _items_by_lookup_key(subscription) -> dict[str, dict]:
    items = (subscription.get("items") or {}).get("data") or []
    out: dict[str, dict] = {}
    for item in items:
        key = (item.get("price") or {}).get("lookup_key")
        if key:
            out[key] = item
    return out


def _paid_module_keys(items: dict[str, dict]) -> set[str]:
    return set(items) & set(catalog.MODULE_PRICES.values())


def _module_key(module: str) -> str:
    try:
        return catalog.MODULE_PRICES[module]
    except KeyError:
        raise ValueError(
            f"Unknown module {module!r}; expected one of {sorted(catalog.MODULE_PRICES)}"
        ) from None


async def ensure_customer(db: AsyncSession, tenant: Tenant) -> str:
    """Return the tenant's Stripe customer id, creating the customer if needed."""
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id
    customer = stripe_client.create_customer(
        name=tenant.name,
        metadata={"tenant_id": str(tenant.id)},
        # Two concurrent calls create one customer, not two.
        idempotency_key=f"promptly-customer-{tenant.id}",
    )
    tenant.stripe_customer_id = customer["id"]
    await db.flush()
    return tenant.stripe_customer_id


def find_enterprise_subscription(customer_id: str | None):
    """Return the customer's live subscription that holds the Work client."""
    if not customer_id:
        return None
    for sub in stripe_client.list_subscriptions(customer=customer_id, status="all"):
        if sub.get("status") not in _LIVE_STATUSES:
            continue
        if catalog.WORK_CLIENT in _items_by_lookup_key(sub):
            return sub
    return None


def _require_subscription(tenant: Tenant):
    sub = find_enterprise_subscription(tenant.stripe_customer_id)
    if sub is None:
        raise NoEnterpriseSubscription(
            f"Tenant {tenant.id} has no Work client subscription; call start_work_client first"
        )
    return sub


async def start_work_client(
    db: AsyncSession, tenant: Tenant, *, provisioned_users: int, pilot: bool = False
):
    """Put an enterprise on the free Work client subscription (one per tenant).

    Idempotent: if the tenant already has one, only its quantity is brought
    up to date, and ``pilot`` is ignored (use ``end_pilot`` to change it).
    """
    if provisioned_users < 0:
        raise ValueError("provisioned_users must not be negative")

    customer_id = await ensure_customer(db, tenant)
    existing = find_enterprise_subscription(customer_id)
    if existing is not None:
        item = _items_by_lookup_key(existing)[catalog.WORK_CLIENT]
        if item.get("quantity") == provisioned_users:
            return existing
        # Free item, so there is nothing to prorate.
        return stripe_client.modify_subscription(
            existing["id"],
            items=[{"id": item["id"], "quantity": provisioned_users}],
            proration_behavior="none",
        )

    return stripe_client.create_subscription(
        customer=customer_id,
        items=[{"price": catalog.price_id(catalog.WORK_CLIENT), "quantity": provisioned_users}],
        collection_method="send_invoice",
        days_until_due=DAYS_UNTIL_DUE,
        metadata={
            KIND_KEY: KIND_ENTERPRISE,
            "tenant_id": str(tenant.id),
            PILOT_KEY: "true" if pilot else "false",
        },
        # Stripe keeps idempotency keys for 24 hours, which covers a double
        # click or a retried request.
        idempotency_key=f"promptly-work-client-{tenant.id}",
    )


def add_module(tenant: Tenant, module: str, *, licensed_users: int):
    """Add a paid module, or set its quantity if it is already there.

    With the first paid module the platform fee is added in the same call,
    unless the customer is a pilot, and the billing year is restarted so the
    annual contract runs from the sale. Later changes are prorated and
    invoiced at once.

    Adding Suite replaces any of Atlas.AI, Learn or Adopt already on the
    subscription. Adding one of those when Suite is present is refused.
    """
    if licensed_users < 1:
        raise ValueError("licensed_users must be at least 1")
    key = _module_key(module)
    sub = _require_subscription(tenant)
    items = _items_by_lookup_key(sub)

    if module in catalog.SUITE_COVERS and catalog.SUITE_ANNUAL in items:
        raise ModuleConflict(f"{module} is already included in the Suite")

    first_paid = not _paid_module_keys(items)
    changes: list[dict] = []
    if key in items:
        if items[key].get("quantity") == licensed_users:
            return sub
        changes.append({"id": items[key]["id"], "quantity": licensed_users})
    else:
        changes.append({"price": catalog.price_id(key), "quantity": licensed_users})

    if module == "suite":
        for covered in catalog.SUITE_COVERS:
            covered_key = catalog.MODULE_PRICES[covered]
            if covered_key in items:
                changes.append({"id": items[covered_key]["id"], "deleted": True})

    if not is_pilot(sub) and catalog.PLATFORM_FEE_ANNUAL not in items:
        changes.append({"price": catalog.price_id(catalog.PLATFORM_FEE_ANNUAL), "quantity": 1})

    params: dict = {"items": changes}
    if first_paid:
        params["billing_cycle_anchor"] = "now"
        params["proration_behavior"] = "create_prorations"
    else:
        params["proration_behavior"] = "always_invoice"
    return stripe_client.modify_subscription(sub["id"], **params)


def remove_module(tenant: Tenant, module: str, *, proration_behavior: str = "none"):
    """Remove a paid module. No-op if it is not on the subscription.

    By default there is no credit for the rest of the year, because modules
    are sold on annual contracts. The platform fee stays: whether to drop it
    when the last module goes is a contract question, not a billing one.
    """
    key = _module_key(module)
    sub = _require_subscription(tenant)
    item = _items_by_lookup_key(sub).get(key)
    if item is None:
        return sub
    return stripe_client.modify_subscription(
        sub["id"],
        items=[{"id": item["id"], "deleted": True}],
        proration_behavior=proration_behavior,
    )


def end_pilot(tenant: Tenant):
    """Mark a pilot as converted; add the platform fee if a module is already paid."""
    sub = _require_subscription(tenant)
    items = _items_by_lookup_key(sub)
    params: dict = {"metadata": {PILOT_KEY: "false"}}
    if _paid_module_keys(items) and catalog.PLATFORM_FEE_ANNUAL not in items:
        params["items"] = [{"price": catalog.price_id(catalog.PLATFORM_FEE_ANNUAL), "quantity": 1}]
        params["proration_behavior"] = "always_invoice"
    elif not is_pilot(sub):
        return sub
    return stripe_client.modify_subscription(sub["id"], **params)
