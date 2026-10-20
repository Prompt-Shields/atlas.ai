"""Unit tests for Promptly Plus checkout params (promptly-guide#61)."""

from __future__ import annotations

import pytest

from app.services.plus_checkout import interval_options, plus_checkout_params

pytestmark = pytest.mark.unit

PRICES = {"monthly": "price_m", "yearly": "price_y"}


def _params(interval="monthly"):
    return plus_checkout_params(
        prices=PRICES,
        interval=interval,
        user_id="u1",
        email="a@b.no",
        success_url="s",
        cancel_url="c",
    )


def test_picks_price_per_interval() -> None:
    assert _params("monthly")["line_items"][0]["price"] == "price_m"
    assert _params("yearly")["line_items"][0]["price"] == "price_y"


def test_creates_customer_at_upgrade_and_offers_link_and_cards() -> None:
    p = _params()
    # Subscription mode creates the customer itself; Stripe refuses
    # customer_creation outside payment and setup mode.
    assert "customer" not in p and "customer_creation" not in p
    assert p["mode"] == "subscription"
    assert p["payment_method_types"] == ["card", "link"]


def test_missing_price_fails_loudly() -> None:
    with pytest.raises(ValueError, match="no Stripe price"):
        plus_checkout_params(
            prices={"monthly": "", "yearly": "y"},
            interval="monthly",
            user_id="u",
            email="e",
            success_url="s",
            cancel_url="c",
        )


def test_yearly_is_promoted_and_saves_24() -> None:
    opts = interval_options()
    assert opts[0]["interval"] == "yearly" and opts[0]["saves_eur"] == 24
