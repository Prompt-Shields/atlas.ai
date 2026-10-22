"""Enterprise module sales by Stripe Quote.

Enterprise modules (Atlas.AI, Learn, Adopt, Suite) are sold by Quote, not by
Checkout. The flow is:

1. ``create_module_quote`` builds a draft quote for the tenant's modules and
   seat count, adding the annual platform fee when this is the tenant's first
   paid module. The fee is added once per tenant: see ``create_module_quote``.
2. ``finalize_quote`` makes it sendable; the buyer gets the quote PDF.
3. ``accept_quote`` accepts it once the buyer signs. Stripe then creates the
   subscription, which bills by ``send_invoice`` with 30-day terms.

Invoices are paid by EU bank transfer into the customer's cash balance
(``customer_balance``). Each customer gets its own virtual IBAN, and Stripe
reconciles incoming transfers against open invoices automatically.

Prices are found by ``lookup_key`` rather than by id, so the same code works in
test and live mode without per-environment configuration.

All Stripe calls go through ``stripe_client`` so tests can patch them.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tenant import Tenant
from app.services.billing import stripe_client, tax

log = structlog.get_logger()

MODULE_LOOKUP_KEYS: dict[str, str] = {
    "atlas": "promptly_atlas_annual",
    "learn": "promptly_learn_annual",
    "adopt": "promptly_adopt_annual",
    "suite": "promptly_suite_annual",
}
PLATFORM_FEE_LOOKUP_KEY = "promptly_platform_fee_annual"

# Suite bundles the three single modules, so quoting it alongside any of them
# would bill the same seats twice.
_SUITE = "suite"

DAYS_UNTIL_DUE = 30

# Marks the subscription a quote creates, so other billing code (the webhook
# handlers) can tell it apart from the self-serve Team subscription stored on
# the tenant: metadata[BILLING_KIND_KEY] == BILLING_KIND.
BILLING_KIND_KEY = "billing_kind"
BILLING_KIND = "enterprise_modules"

# Countries Stripe can issue the virtual IBAN in for eu_bank_transfer.
EU_BANK_TRANSFER_COUNTRIES = frozenset({"BE", "DE", "ES", "FR", "IE", "NL"})

# A subscription in one of these states still carries the platform fee.
_LIVE_STATUSES = {"active", "trialing", "past_due", "unpaid", "incomplete"}

# A quote in one of these states may still become a subscription. Stripe's
# quote list filters by one status at a time, so each is listed in turn.
_PENDING_QUOTE_STATUSES = ("draft", "open")

# Quote metadata marking a quote that carries the platform fee, so a second
# quote drafted before the first is accepted can see it.
PLATFORM_FEE_METADATA = "platform_fee"


class QuoteError(ValueError):
    """The quote request is invalid, or Stripe is missing something it needs."""


def _get(obj, key: str, default=None):
    """Read a field from a Stripe object or a plain dict (as in tests)."""
    try:
        value = obj[key]
    except (KeyError, TypeError):
        return default
    return default if value is None else value


def _data(listing) -> list:
    """Items of a Stripe list: a ListObject (``.data``) or an already-built list."""
    if isinstance(listing, list):
        return listing
    return _get(listing, "data", [])


def _bank_transfer_payment_settings(country: str) -> dict:
    return {
        "payment_method_types": ["customer_balance"],
        "payment_method_options": {
            "customer_balance": {
                "funding_type": "bank_transfer",
                "bank_transfer": {
                    "type": "eu_bank_transfer",
                    "eu_bank_transfer": {"country": country},
                },
            }
        },
    }


def validate_modules(modules: Iterable[str]) -> list[str]:
    """Return the modules de-duplicated in input order, or raise ``QuoteError``."""
    chosen: list[str] = []
    for m in modules:
        key = m.strip().lower()
        if key not in MODULE_LOOKUP_KEYS:
            raise QuoteError(
                f"Unknown module {m!r}; expected one of {', '.join(MODULE_LOOKUP_KEYS)}"
            )
        if key not in chosen:
            chosen.append(key)
    if not chosen:
        raise QuoteError("At least one module is required")
    if _SUITE in chosen and len(chosen) > 1:
        raise QuoteError("Suite already includes Atlas.AI, Learn and Adopt; quote it alone")
    return chosen


def resolve_prices(lookup_keys: Iterable[str]) -> dict[str, str]:
    """Map each lookup key to its active price id. Raise if any is missing."""
    keys = list(dict.fromkeys(lookup_keys))
    prices = stripe_client.list_prices(lookup_keys=keys, active=True, limit=len(keys))
    found = {_get(p, "lookup_key"): _get(p, "id") for p in _data(prices)}
    missing = [k for k in keys if k not in found]
    if missing:
        raise QuoteError(f"No active Stripe price for lookup key(s): {', '.join(missing)}")
    return {k: found[k] for k in keys}


def has_platform_fee(customer_id: str) -> bool:
    """True when a live subscription of this customer already bills the platform fee."""
    subs = stripe_client.list_subscriptions(customer=customer_id, status="all", limit=100)
    for sub in _data(subs):
        if _get(sub, "status") not in _LIVE_STATUSES:
            continue
        for item in _data(_get(sub, "items", {})):
            if _get(_get(item, "price", {}), "lookup_key") == PLATFORM_FEE_LOOKUP_KEY:
                return True
    return False


def pending_platform_fee_quote(customer_id: str) -> str | None:
    """Id of a draft or open quote of this customer that carries the platform fee."""
    for status in _PENDING_QUOTE_STATUSES:
        listing = stripe_client.list_quotes(customer=customer_id, status=status, limit=100)
        for quote in _data(listing):
            if _get(_get(quote, "metadata", {}), PLATFORM_FEE_METADATA) == "true":
                return _get(quote, "id")
    return None


async def _lock_tenant(db: AsyncSession, tenant: Tenant) -> None:
    """Lock the tenant row until the caller's transaction ends.

    Two quotes drafted at the same time for one tenant would otherwise both
    see no platform fee and both add it, or both create a Stripe customer.
    The lock makes the second wait until the first has created its quote and
    committed. ``populate_existing`` reloads the tenant, so a customer id
    stored by the first is seen by the second.
    """
    await db.execute(
        select(Tenant)
        .where(Tenant.id == tenant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def ensure_customer(
    db: AsyncSession, tenant: Tenant, *, billing_email: str | None = None
) -> str:
    """Return the tenant's Stripe customer id, creating the customer if needed.

    ``send_invoice`` needs an email on the customer, so a new customer needs
    ``billing_email``. An existing customer is left as it is.
    """
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id
    if not billing_email:
        raise QuoteError("Tenant has no Stripe customer; pass a billing email to create one")
    customer = stripe_client.create_customer(
        name=tenant.name,
        email=billing_email,
        metadata={"tenant_id": str(tenant.id)},
    )
    tenant.stripe_customer_id = _get(customer, "id")
    await db.flush()
    log.info("stripe_customer_created", tenant_id=str(tenant.id))
    return tenant.stripe_customer_id


async def create_module_quote(
    db: AsyncSession,
    tenant: Tenant,
    *,
    modules: Iterable[str],
    seats: int,
    billing_email: str | None = None,
    include_platform_fee: bool | None = None,
):
    """Create a draft Stripe Quote for ``modules`` at ``seats`` licensed users.

    ``include_platform_fee=None`` adds the fee only on the first paid module:
    when no live subscription of the customer bills it, and no draft or open
    quote carries it. The tenant row is locked first, so two quotes drafted
    at the same time cannot both add it. The lock lasts until the caller
    commits, which it should do as soon as this returns.
    """
    chosen = validate_modules(modules)
    if seats < 1:
        raise QuoteError("Seat count must be at least 1")

    await _lock_tenant(db, tenant)
    customer_id = await ensure_customer(db, tenant, billing_email=billing_email)
    if include_platform_fee is None:
        pending = pending_platform_fee_quote(customer_id)
        if pending:
            log.info(
                "stripe_platform_fee_on_pending_quote", tenant_id=str(tenant.id), quote=pending
            )
        include_platform_fee = not pending and not has_platform_fee(customer_id)

    keys = [MODULE_LOOKUP_KEYS[m] for m in chosen]
    if include_platform_fee:
        keys.append(PLATFORM_FEE_LOOKUP_KEY)
    prices = resolve_prices(keys)

    line_items = [{"price": prices[MODULE_LOOKUP_KEYS[m]], "quantity": seats} for m in chosen]
    if include_platform_fee:
        line_items.append({"price": prices[PLATFORM_FEE_LOOKUP_KEY], "quantity": 1})

    metadata = {
        "tenant_id": str(tenant.id),
        BILLING_KIND_KEY: BILLING_KIND,
        "modules": ",".join(chosen),
    }
    quote_metadata = dict(metadata)
    if include_platform_fee:
        quote_metadata[PLATFORM_FEE_METADATA] = "true"
    # Stripe Tax follows the account-wide flag (promptly-guide#50). The quote's
    # setting carries over to the subscription and its invoices.
    quote = stripe_client.create_quote(
        customer=customer_id,
        line_items=line_items,
        collection_method="send_invoice",
        invoice_settings={"days_until_due": DAYS_UNTIL_DUE},
        metadata=quote_metadata,
        subscription_data={"metadata": metadata},
        **tax.automatic_tax_params(get_settings().stripe_automatic_tax),
    )
    log.info(
        "stripe_quote_created",
        tenant_id=str(tenant.id),
        quote=_get(quote, "id"),
        modules=chosen,
        seats=seats,
        platform_fee=include_platform_fee,
    )
    return quote


def finalize_quote(quote_id: str):
    """Finalise a draft quote so it can be sent to the buyer."""
    quote = stripe_client.finalize_quote(quote_id)
    log.info("stripe_quote_finalized", quote=quote_id)
    return quote


def accept_quote(quote_id: str, *, bank_transfer_country: str | None = None):
    """Accept a signed quote and set its invoices up for bank transfer.

    Accepting creates the subscription. A Quote cannot carry payment settings,
    so they are set on the subscription afterwards, and on its first invoice,
    which Stripe creates at once and leaves as a draft for about an hour.
    Reconciliation of the customer's cash balance is pinned to automatic so a
    transfer is applied to the matching open invoice without manual work.

    Safe to run again: if the quote is already accepted, for example because a
    step after acceptance failed, the settings are applied again without a
    second acceptance. The country is checked before anything is accepted, so
    a typo cannot leave an accepted quote whose invoice has no bank details.
    """
    settings = get_settings()
    country = (bank_transfer_country or settings.stripe_bank_transfer_country).upper()
    if country not in EU_BANK_TRANSFER_COUNTRIES:
        raise QuoteError(
            f"Bank-transfer country {country!r} is not one Stripe issues EU IBANs in; "
            f"expected one of {', '.join(sorted(EU_BANK_TRANSFER_COUNTRIES))}"
        )
    payment_settings = _bank_transfer_payment_settings(country)

    quote = stripe_client.retrieve_quote(quote_id)
    if _get(quote, "status") == "accepted":
        log.info("stripe_quote_already_accepted", quote=quote_id)
    else:
        quote = stripe_client.accept_quote(quote_id)
    subscription_id = _get(quote, "subscription")
    customer_id = _get(quote, "customer")
    if not subscription_id:
        raise QuoteError(f"Quote {quote_id} was accepted but has no subscription")

    stripe_client.modify_cash_balance(customer_id, settings={"reconciliation_mode": "automatic"})
    # The subscription already has the quote's Stripe Tax setting. Sending
    # the flag again matters only if it was switched on between drafting and
    # acceptance, and keeps this update in line with every other one. The
    # first invoice is left as Stripe made it: automatic_tax changes its
    # amounts, which Stripe allows only on a draft, and a rerun may find it
    # finalised.
    subscription = stripe_client.modify_subscription(
        subscription_id,
        payment_settings=payment_settings,
        **tax.automatic_tax_params(settings.stripe_automatic_tax),
    )

    invoice = _get(subscription, "latest_invoice")
    invoice_id = invoice if isinstance(invoice, str) else _get(invoice, "id")
    if invoice_id:
        stripe_client.modify_invoice(invoice_id, payment_settings=payment_settings)

    log.info(
        "stripe_quote_accepted",
        quote=quote_id,
        subscription=subscription_id,
        invoice=invoice_id,
    )
    return quote
