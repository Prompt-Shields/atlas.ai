"""Thin Stripe SDK wrapper.

Centralises `stripe.api_key` configuration and provides a narrow surface for
the rest of the billing code to call.  Tests monkeypatch the underlying SDK
methods on the `stripe.*` module; callers import from this module only.
"""

from __future__ import annotations

import stripe

from app.config import get_settings


def _configure() -> None:
    stripe.api_key = get_settings().stripe_secret_key


def create_checkout_session(**kwargs):
    _configure()
    return stripe.checkout.Session.create(**kwargs)


def create_portal_session(**kwargs):
    _configure()
    return stripe.billing_portal.Session.create(**kwargs)


# --- Customer portal configurations (portal.py, scripts/stripe_portal.py) ---


def list_portal_configurations() -> list:
    _configure()
    return list(stripe.billing_portal.Configuration.list(limit=100).auto_paging_iter())


def create_portal_configuration(**kwargs):
    _configure()
    return stripe.billing_portal.Configuration.create(**kwargs)


def modify_portal_configuration(configuration_id: str, **kwargs):
    _configure()
    return stripe.billing_portal.Configuration.modify(configuration_id, **kwargs)


def modify_subscription_item(item_id: str, **kwargs):
    _configure()
    return stripe.SubscriptionItem.modify(item_id, **kwargs)


# --- Catalogue (scripts/stripe_catalog.py) ---------------------------------


def retrieve_product(product_id: str):
    """Return the product, or None if the account has no product with this id."""
    _configure()
    try:
        return stripe.Product.retrieve(product_id)
    except stripe.error.InvalidRequestError as exc:
        if getattr(exc, "code", None) == "resource_missing":
            return None
        raise


def create_product(**kwargs):
    _configure()
    return stripe.Product.create(**kwargs)


def modify_product(product_id: str, **kwargs):
    _configure()
    return stripe.Product.modify(product_id, **kwargs)


def retrieve_price(price_id: str):
    _configure()
    return stripe.Price.retrieve(price_id)


def list_prices(**kwargs) -> list:
    _configure()
    return list(stripe.Price.list(**kwargs).data)


def create_price(**kwargs):
    _configure()
    return stripe.Price.create(**kwargs)


def modify_price(price_id: str, **kwargs):
    _configure()
    return stripe.Price.modify(price_id, **kwargs)


def list_meters() -> list:
    _configure()
    return list(stripe.billing.Meter.list(limit=100).auto_paging_iter())


def create_meter(**kwargs):
    _configure()
    return stripe.billing.Meter.create(**kwargs)


def reactivate_meter(meter_id: str):
    _configure()
    return stripe.billing.Meter.reactivate(meter_id)


# --- Enterprise subscriptions (enterprise.py) ------------------------------


def create_customer(**kwargs):
    _configure()
    return stripe.Customer.create(**kwargs)


def modify_cash_balance(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.modify_cash_balance(customer_id, **kwargs)


def list_subscriptions(**kwargs) -> list:
    _configure()
    return list(stripe.Subscription.list(**kwargs).auto_paging_iter())


def create_subscription(**kwargs):
    _configure()
    return stripe.Subscription.create(**kwargs)


def retrieve_subscription(subscription_id: str, **kwargs):
    _configure()
    return stripe.Subscription.retrieve(subscription_id, **kwargs)


def modify_subscription(subscription_id: str, **kwargs):
    _configure()
    return stripe.Subscription.modify(subscription_id, **kwargs)


def modify_invoice(invoice_id: str, **kwargs):
    _configure()
    return stripe.Invoice.modify(invoice_id, **kwargs)


def create_quote(**kwargs):
    _configure()
    return stripe.Quote.create(**kwargs)


def retrieve_quote(quote_id: str, **kwargs):
    _configure()
    return stripe.Quote.retrieve(quote_id, **kwargs)


def list_quotes(**kwargs):
    _configure()
    return stripe.Quote.list(**kwargs)


def finalize_quote(quote_id: str, **kwargs):
    _configure()
    return stripe.Quote.finalize_quote(quote_id, **kwargs)


def accept_quote(quote_id: str, **kwargs):
    _configure()
    return stripe.Quote.accept(quote_id, **kwargs)


# --- Tax ids ---------------------------------------------------------------


def list_tax_ids(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.list_tax_ids(customer_id, **kwargs)


def create_tax_id(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.create_tax_id(customer_id, **kwargs)


def construct_event(payload: bytes, sig_header: str):
    return stripe.Webhook.construct_event(payload, sig_header, get_settings().stripe_webhook_secret)
