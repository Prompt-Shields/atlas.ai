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


def construct_event(payload: bytes, sig_header: str):
    return stripe.Webhook.construct_event(payload, sig_header, get_settings().stripe_webhook_secret)
