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


def modify_subscription_item(item_id: str, **kwargs):
    _configure()
    return stripe.SubscriptionItem.modify(item_id, **kwargs)


def list_prices(**kwargs):
    _configure()
    return stripe.Price.list(**kwargs)


def create_customer(**kwargs):
    _configure()
    return stripe.Customer.create(**kwargs)


def modify_cash_balance(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.modify_cash_balance(customer_id, **kwargs)


def list_subscriptions(**kwargs):
    _configure()
    return stripe.Subscription.list(**kwargs)


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


def list_tax_ids(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.list_tax_ids(customer_id, **kwargs)


def create_tax_id(customer_id: str, **kwargs):
    _configure()
    return stripe.Customer.create_tax_id(customer_id, **kwargs)


def construct_event(payload: bytes, sig_header: str):
    return stripe.Webhook.construct_event(payload, sig_header, get_settings().stripe_webhook_secret)
