"""Thin Stripe SDK wrapper.

Centralises `stripe.api_key` configuration and provides a narrow surface for
the rest of the billing code to call.  Tests monkeypatch the underlying SDK
methods on the `stripe.*` module; callers import from this module only.

Every function returns plain Python data (dicts and lists). Since stripe-python
15, a `StripeObject` is no longer a `dict`: `.get()`, `.keys()` and `dict(obj)`
raise. The billing code reads Stripe data as dicts, so the conversion happens
here, once, with `to_dict()`.
"""

from __future__ import annotations

from typing import Any

import stripe

from app.config import get_settings

# The Stripe API version every request is made with. It is pinned here rather
# than taken from the SDK, so upgrading the SDK does not change what Stripe
# sends and accepts: 2024-06-20 is what stripe-python 10 pinned. Moving it is a
# migration of its own (for example, 2026-09-30.endive drops
# `payment_method_types` from Checkout sessions, which Plus checkout sends).
# Webhook payloads follow the endpoint's version in the Stripe Dashboard.
STRIPE_API_VERSION = "2024-06-20"


def _configure() -> None:
    stripe.api_key = get_settings().stripe_secret_key
    stripe.api_version = STRIPE_API_VERSION


def _plain(value: Any) -> Any:
    """`value` with every Stripe object turned into plain dicts and lists."""
    if isinstance(value, stripe.StripeObject):
        return value.to_dict()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def create_checkout_session(**kwargs):
    _configure()
    return _plain(stripe.checkout.Session.create(**kwargs))


def create_portal_session(**kwargs):
    _configure()
    return _plain(stripe.billing_portal.Session.create(**kwargs))


# --- Customer portal configurations (portal.py, scripts/stripe_portal.py) ---


def list_portal_configurations() -> list:
    _configure()
    return _plain(list(stripe.billing_portal.Configuration.list(limit=100).auto_paging_iter()))


def create_portal_configuration(**kwargs):
    _configure()
    return _plain(stripe.billing_portal.Configuration.create(**kwargs))


def modify_portal_configuration(configuration_id: str, **kwargs):
    _configure()
    return _plain(stripe.billing_portal.Configuration.modify(configuration_id, **kwargs))


def modify_subscription_item(item_id: str, **kwargs):
    _configure()
    return _plain(stripe.SubscriptionItem.modify(item_id, **kwargs))


# --- Catalogue (scripts/stripe_catalog.py) ---------------------------------


def retrieve_product(product_id: str):
    """Return the product, or None if the account has no product with this id."""
    _configure()
    try:
        return _plain(stripe.Product.retrieve(product_id))
    except stripe.InvalidRequestError as exc:
        if getattr(exc, "code", None) == "resource_missing":
            return None
        raise


def create_product(**kwargs):
    _configure()
    return _plain(stripe.Product.create(**kwargs))


def modify_product(product_id: str, **kwargs):
    _configure()
    return _plain(stripe.Product.modify(product_id, **kwargs))


def retrieve_price(price_id: str):
    _configure()
    return _plain(stripe.Price.retrieve(price_id))


def list_prices(**kwargs) -> list:
    _configure()
    return _plain(list(stripe.Price.list(**kwargs).data))


def create_price(**kwargs):
    _configure()
    return _plain(stripe.Price.create(**kwargs))


def modify_price(price_id: str, **kwargs):
    _configure()
    return _plain(stripe.Price.modify(price_id, **kwargs))


def list_meters() -> list:
    _configure()
    return _plain(list(stripe.billing.Meter.list(limit=100).auto_paging_iter()))


def create_meter(**kwargs):
    _configure()
    return _plain(stripe.billing.Meter.create(**kwargs))


def reactivate_meter(meter_id: str):
    _configure()
    return _plain(stripe.billing.Meter.reactivate(meter_id))


# --- Enterprise subscriptions (enterprise.py) ------------------------------


def create_customer(**kwargs):
    _configure()
    return _plain(stripe.Customer.create(**kwargs))


def modify_cash_balance(customer_id: str, **kwargs):
    _configure()
    return _plain(stripe.Customer.modify_cash_balance(customer_id, **kwargs))


def list_subscriptions(**kwargs) -> list:
    _configure()
    return _plain(list(stripe.Subscription.list(**kwargs).auto_paging_iter()))


def create_subscription(**kwargs):
    _configure()
    return _plain(stripe.Subscription.create(**kwargs))


def retrieve_subscription(subscription_id: str, **kwargs):
    _configure()
    return _plain(stripe.Subscription.retrieve(subscription_id, **kwargs))


def modify_subscription(subscription_id: str, **kwargs):
    _configure()
    return _plain(stripe.Subscription.modify(subscription_id, **kwargs))


def modify_invoice(invoice_id: str, **kwargs):
    _configure()
    return _plain(stripe.Invoice.modify(invoice_id, **kwargs))


def create_quote(**kwargs):
    _configure()
    return _plain(stripe.Quote.create(**kwargs))


def retrieve_quote(quote_id: str, **kwargs):
    _configure()
    return _plain(stripe.Quote.retrieve(quote_id, **kwargs))


def list_quotes(**kwargs):
    _configure()
    return _plain(stripe.Quote.list(**kwargs))


def finalize_quote(quote_id: str, **kwargs):
    _configure()
    return _plain(stripe.Quote.finalize_quote(quote_id, **kwargs))


def accept_quote(quote_id: str, **kwargs):
    _configure()
    return _plain(stripe.Quote.accept(quote_id, **kwargs))


# --- Tax ids ---------------------------------------------------------------


def list_tax_ids(customer_id: str, **kwargs):
    _configure()
    return _plain(stripe.Customer.list_tax_ids(customer_id, **kwargs))


def create_tax_id(customer_id: str, **kwargs):
    _configure()
    return _plain(stripe.Customer.create_tax_id(customer_id, **kwargs))


def construct_event(payload: bytes, sig_header: str):
    event = stripe.Webhook.construct_event(
        payload, sig_header, get_settings().stripe_webhook_secret
    )
    return _plain(event)
