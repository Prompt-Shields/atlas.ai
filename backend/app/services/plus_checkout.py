"""Stripe Checkout parameters and the offer for Promptly Plus (promptly-guide#61).

Free-tier users have no Stripe customer: Checkout creates one on upgrade, so
we pass no customer id. A subscription-mode session always creates the
customer; `customer_creation` is accepted only in payment and setup mode,
and Stripe refuses a subscription session that sets it. The yearly plan is
listed first so it is the default choice, since the fixed card fee weighs
heavily on a small monthly payment.

No amount is kept in this repository (it is public, and CONTRIBUTING.md
forbids committing pricing). Plus is offered from two Stripe Prices whose ids
are set in ``STRIPE_PRICE_PLUS_MONTHLY`` and ``STRIPE_PRICE_PLUS_YEARLY``;
the amounts shown to the user, and the yearly saving, are read from those
Price objects and cached for ``PRICE_CACHE_SECONDS``. If either price is
missing, unreadable or unusable, Plus is not offered at all (fail closed):
no options, no checkout, and a log line saying why.
"""

from __future__ import annotations

import time
from typing import Literal

import stripe
import structlog

from app.services.billing import stripe_client
from app.services.billing.tax import checkout_tax_params

log = structlog.get_logger()

Interval = Literal["monthly", "yearly"]

# The Stripe recurring interval each Plus plan must bill on.
STRIPE_INTERVAL: dict[Interval, str] = {"monthly": "month", "yearly": "year"}

# How long a Price read from Stripe is reused. An amount changed in Stripe is
# a new Price (amounts are immutable), so this only bounds how long a newly
# configured price id takes to show.
PRICE_CACHE_SECONDS = 600

# Wallets (Apple Pay, Google Pay) ride on "card" in Checkout; Link is separate.
PAYMENT_METHOD_TYPES = ["card", "link"]

_price_cache: dict[str, tuple[float, dict]] = {}


class PlusUnavailable(RuntimeError):
    """Plus cannot be offered: its Stripe prices are not configured or usable."""


def clear_price_cache() -> None:
    _price_cache.clear()


def _price(price_id: str) -> dict:
    """The Stripe Price ``price_id``, from the cache when fresh. Failures are not cached."""
    now = time.monotonic()
    hit = _price_cache.get(price_id)
    if hit and now - hit[0] < PRICE_CACHE_SECONDS:
        return hit[1]
    price = stripe_client.retrieve_price(price_id)
    _price_cache[price_id] = (now, price)
    return price


def _amount(prices: dict[Interval, str], interval: Interval) -> tuple[int, str]:
    """``(unit_amount, currency)`` of the configured Plus price for ``interval``.

    Raises ``PlusUnavailable`` with the reason when it cannot be used.
    """
    price_id = prices.get(interval, "")
    if not price_id:
        raise PlusUnavailable(f"no Stripe price configured for Plus {interval}")
    try:
        price = _price(price_id)
    except stripe.StripeError as exc:
        raise PlusUnavailable(f"cannot read Stripe price for Plus {interval}: {exc}") from None
    amount = price.get("unit_amount")
    currency = price.get("currency")
    recurring = price.get("recurring") or {}
    if not price.get("active"):
        raise PlusUnavailable(f"Stripe price for Plus {interval} is archived")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise PlusUnavailable(f"Stripe price for Plus {interval} has no unit amount")
    if not currency:
        raise PlusUnavailable(f"Stripe price for Plus {interval} has no currency")
    if (
        recurring.get("interval") != STRIPE_INTERVAL[interval]
        or recurring.get("interval_count", 1) != 1
    ):
        raise PlusUnavailable(
            f"Stripe price for Plus {interval} does not bill every {STRIPE_INTERVAL[interval]}"
        )
    return amount, currency


def plus_offer(prices: dict[Interval, str], default: Interval = "yearly") -> list[dict]:
    """Plans to show, promoted one first, with the yearly saving spelled out.

    ``prices`` maps each interval to its Stripe price id (the
    ``stripe_price_plus_*`` settings). Amounts are Stripe minor units (cents)
    read from those prices. Returns ``[]``, and logs why, when Plus cannot be
    offered; callers then show no Plus price and no upgrade.
    """
    try:
        monthly, currency = _amount(prices, "monthly")
        yearly, yearly_currency = _amount(prices, "yearly")
        if yearly_currency != currency:
            raise PlusUnavailable(
                f"Plus prices differ in currency ({currency} monthly, {yearly_currency} yearly)"
            )
    except PlusUnavailable as exc:
        log.warning("plus_offer_unavailable", reason=str(exc))
        return []
    options = [
        {
            "interval": "yearly",
            "amount": yearly,
            "currency": currency,
            "saves": max(monthly * 12 - yearly, 0),
        },
        {"interval": "monthly", "amount": monthly, "currency": currency, "saves": 0},
    ]
    options.sort(key=lambda o: o["interval"] != default)
    return options


def plus_checkout_params(
    *,
    prices: dict[Interval, str],
    interval: Interval,
    user_id: str,
    email: str,
    success_url: str,
    cancel_url: str,
    automatic_tax: bool = False,
) -> dict:
    """Params for `stripe.checkout.Session.create` upgrading a Free user to Plus.

    Raises ``PlusUnavailable`` unless ``plus_offer`` offers ``interval``, so
    no checkout is started for a plan whose price the user could not be shown.

    ``automatic_tax`` is `Settings.stripe_automatic_tax`. Plus buyers are
    consumers, so no VAT ID is collected; Stripe Tax charges them where the
    account is registered.
    """
    if not any(o["interval"] == interval for o in plus_offer(prices)):
        raise PlusUnavailable(f"Plus {interval} is not on offer; see plus_offer_unavailable")
    return {
        "mode": "subscription",
        "line_items": [{"price": prices[interval], "quantity": 1}],
        "customer_email": email,
        "payment_method_types": PAYMENT_METHOD_TYPES,
        "client_reference_id": user_id,
        "metadata": {"user_id": user_id, "plan": "plus", "interval": interval},
        "success_url": success_url,
        "cancel_url": cancel_url,
        **checkout_tax_params(enabled=automatic_tax, business=False),
    }
