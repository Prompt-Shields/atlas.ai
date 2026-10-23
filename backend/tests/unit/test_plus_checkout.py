"""Unit tests for Promptly Plus checkout and offer (promptly-guide#61).

Amounts are made up (1234, 9999): real prices live in Stripe only. The fake
Stripe answers ``retrieve_price`` and every other wrapper raises.
"""

from __future__ import annotations

import pytest
import stripe

from app.services import plus_checkout
from app.services.billing import stripe_client
from app.services.plus_checkout import PlusUnavailable, plus_checkout_params, plus_offer

pytestmark = pytest.mark.unit

PRICES = {"monthly": "price_m", "yearly": "price_y"}


def _stripe_price(price_id, amount, interval, **extra):
    return {
        "id": price_id,
        "object": "price",
        "active": True,
        "currency": "eur",
        "unit_amount": amount,
        "recurring": {"interval": interval, "interval_count": 1},
        **extra,
    }


class FakeStripe:
    def __init__(self) -> None:
        self.prices = {
            "price_m": _stripe_price("price_m", 1234, "month"),
            "price_y": _stripe_price("price_y", 9999, "year"),
        }
        self.reads: list[str] = []

    def retrieve_price(self, price_id: str) -> dict:
        self.reads.append(price_id)
        if price_id not in self.prices:
            raise stripe.InvalidRequestError("No such price", "id", code="resource_missing")
        return dict(self.prices[price_id])


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()

    def refuse(*_a, **_k):
        raise AssertionError("Plus must only read prices from Stripe")

    for name in dir(stripe_client):
        value = getattr(stripe_client, name)
        if callable(value) and getattr(value, "__module__", "") == stripe_client.__name__:
            monkeypatch.setattr(stripe_client, name, refuse)
    monkeypatch.setattr(stripe_client, "retrieve_price", f.retrieve_price)
    plus_checkout.clear_price_cache()
    yield f
    plus_checkout.clear_price_cache()


def _params(interval="monthly", prices=PRICES, **kw):
    return plus_checkout_params(
        prices=prices,
        interval=interval,
        user_id="u1",
        email="a@b.no",
        success_url="s",
        cancel_url="c",
        **kw,
    )


def test_picks_price_per_interval(fake) -> None:
    assert _params("monthly")["line_items"][0]["price"] == "price_m"
    assert _params("yearly")["line_items"][0]["price"] == "price_y"


def test_creates_customer_at_upgrade_and_offers_link_and_cards(fake) -> None:
    p = _params()
    # Subscription mode creates the customer itself; Stripe refuses
    # customer_creation outside payment and setup mode.
    assert "customer" not in p and "customer_creation" not in p
    assert p["mode"] == "subscription"
    assert p["payment_method_types"] == ["card", "link"]


def test_offer_reads_amounts_from_stripe_and_promotes_yearly(fake) -> None:
    opts = plus_offer(PRICES)
    assert opts == [
        {"interval": "yearly", "amount": 9999, "currency": "eur", "saves": 1234 * 12 - 9999},
        {"interval": "monthly", "amount": 1234, "currency": "eur", "saves": 0},
    ]
    assert plus_offer(PRICES, default="monthly")[0]["interval"] == "monthly"


def test_saving_never_negative(fake) -> None:
    fake.prices["price_y"]["unit_amount"] = 1234 * 13
    assert plus_offer(PRICES)[0]["saves"] == 0


def test_prices_are_cached(fake) -> None:
    plus_offer(PRICES)
    plus_offer(PRICES)
    _params("yearly")
    assert sorted(fake.reads) == ["price_m", "price_y"]


def test_cache_expires(fake, monkeypatch) -> None:
    plus_offer(PRICES)
    now = plus_checkout.time.monotonic() + plus_checkout.PRICE_CACHE_SECONDS + 1
    monkeypatch.setattr(plus_checkout.time, "monotonic", lambda: now)
    plus_offer(PRICES)
    assert len(fake.reads) == 4


@pytest.mark.parametrize(
    "broken",
    [
        {"monthly": "", "yearly": "price_y"},
        {"monthly": "price_m", "yearly": ""},
        {},
        {"monthly": "price_m", "yearly": "price_gone"},
    ],
)
def test_unconfigured_or_missing_price_offers_nothing(fake, broken) -> None:
    assert plus_offer(broken) == []
    with pytest.raises(PlusUnavailable):
        _params("monthly", prices=broken)
    with pytest.raises(PlusUnavailable):
        _params("yearly", prices=broken)


@pytest.mark.parametrize(
    "change",
    [
        {"active": False},
        {"unit_amount": None},
        {"unit_amount": 0},
        {"unit_amount": "1234"},
        {"currency": None},
        {"currency": "usd"},
        {"recurring": {"interval": "month", "interval_count": 1}},
        {"recurring": {"interval": "year", "interval_count": 2}},
        {"recurring": None},
    ],
)
def test_unusable_yearly_price_offers_nothing(fake, change) -> None:
    fake.prices["price_y"].update(change)
    assert plus_offer(PRICES) == []
    with pytest.raises(PlusUnavailable):
        _params("monthly")


def test_stripe_error_offers_nothing_and_is_not_cached(fake, monkeypatch) -> None:
    def down(_price_id):
        raise stripe.APIConnectionError("network down")

    monkeypatch.setattr(stripe_client, "retrieve_price", down)
    assert plus_offer(PRICES) == []
    monkeypatch.setattr(stripe_client, "retrieve_price", fake.retrieve_price)
    assert len(plus_offer(PRICES)) == 2


def test_unavailable_is_logged(fake, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(plus_checkout.log, "warning", lambda event, **kw: seen.append((event, kw)))
    plus_offer({"monthly": "", "yearly": "price_y"})
    assert seen == [
        ("plus_offer_unavailable", {"reason": "no Stripe price configured for Plus monthly"})
    ]


def test_automatic_tax_off_by_default_and_no_vat_id_for_consumers(fake) -> None:
    p = _params()
    assert "automatic_tax" not in p and "tax_id_collection" not in p


def test_automatic_tax_when_switched_on(fake) -> None:
    p = _params("yearly", automatic_tax=True)
    assert p["automatic_tax"] == {"enabled": True}
    assert "tax_id_collection" not in p
