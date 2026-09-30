"""Stripe Checkout parameters for Promptly Plus (promptly-guide#61). No I/O.

Free-tier users have no Stripe customer: Checkout creates one on upgrade, so
we pass `customer_creation` rather than a customer id. The yearly plan is
listed first so it is the default choice, since the fixed card fee weighs
heavily on a EUR 9 payment.
"""

from __future__ import annotations

from typing import Literal

Interval = Literal["monthly", "yearly"]

PLUS_MONTHLY_EUR = 9
PLUS_YEARLY_EUR = 84

# Wallets (Apple Pay, Google Pay) ride on "card" in Checkout; Link is separate.
PAYMENT_METHOD_TYPES = ["card", "link"]


def plus_checkout_params(
    *,
    prices: dict[Interval, str],
    interval: Interval,
    user_id: str,
    email: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Params for `stripe.checkout.Session.create` upgrading a Free user to Plus."""
    price = prices.get(interval, "")
    if not price:
        raise ValueError(f"no Stripe price configured for Plus {interval}")
    return {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "customer_email": email,
        "customer_creation": "always",
        "payment_method_types": PAYMENT_METHOD_TYPES,
        "client_reference_id": user_id,
        "metadata": {"user_id": user_id, "plan": "plus", "interval": interval},
        "success_url": success_url,
        "cancel_url": cancel_url,
    }


def interval_options(default: Interval = "yearly") -> list[dict]:
    """Plans to show, promoted one first, with the saving spelled out."""
    saving = PLUS_MONTHLY_EUR * 12 - PLUS_YEARLY_EUR
    options = [
        {"interval": "yearly", "eur": PLUS_YEARLY_EUR, "saves_eur": saving},
        {"interval": "monthly", "eur": PLUS_MONTHLY_EUR, "saves_eur": 0},
    ]
    options.sort(key=lambda o: o["interval"] != default)
    return options
