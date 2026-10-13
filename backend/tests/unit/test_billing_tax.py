"""Unit tests for services/billing/tax.py — Stripe Tax (promptly-guide#50).

Stripe is mocked: the SDK methods are monkeypatched, as in test_stripe_client.
"""

from __future__ import annotations

import pytest
import stripe

from app.services.billing import tax

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# checkout_tax_params
# ---------------------------------------------------------------------------


def test_business_checkout_with_tax_on():
    assert tax.checkout_tax_params(enabled=True, business=True) == {
        "automatic_tax": {"enabled": True},
        "tax_id_collection": {"enabled": True},
        "billing_address_collection": "required",
    }


def test_business_checkout_with_tax_off_still_collects_vat_id():
    """VAT ID collection does not need Stripe Tax, so the flag does not gate it."""
    p = tax.checkout_tax_params(enabled=False, business=True)
    assert "automatic_tax" not in p
    assert p["tax_id_collection"] == {"enabled": True}


def test_consumer_checkout_collects_no_vat_id():
    assert tax.checkout_tax_params(enabled=True, business=False) == {
        "automatic_tax": {"enabled": True}
    }
    assert tax.checkout_tax_params(enabled=False, business=False) == {}


def test_never_sets_tax_exempt_or_rates():
    """Reverse charge and rates are Stripe Tax's job, not ours."""
    for enabled in (True, False):
        for business in (True, False):
            p = tax.checkout_tax_params(enabled=enabled, business=business)
            assert not {"tax_exempt", "tax_rates", "default_tax_rates"} & set(p)


def test_config_flag_defaults_on():
    # main sent automatic_tax on every Team checkout; the default keeps that.
    from app.config import get_settings

    assert get_settings().stripe_automatic_tax is True


# ---------------------------------------------------------------------------
# tax_id_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123456789MVA", ("no_vat", "123456789MVA")),
        ("NO 123 456 789 MVA", ("no_vat", "123456789MVA")),
        ("no123456789mva", ("no_vat", "123456789MVA")),
        ("DE123456789", ("eu_vat", "DE123456789")),
        ("se 5560000000 01", ("eu_vat", "SE556000000001")),
        ("EL123456789", ("eu_vat", "EL123456789")),
        ("NL.123456789.B01", ("eu_vat", "NL123456789B01")),
    ],
)
def test_tax_id_for_classifies(raw, expected):
    assert tax.tax_id_for(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "123456789", "GR123456789", "GB123456789", "US12-3456789", "NO12345678MVA", "DE"],
)
def test_tax_id_for_rejects(raw):
    with pytest.raises(ValueError, match="not a Norwegian or EU VAT number"):
        tax.tax_id_for(raw)


# ---------------------------------------------------------------------------
# attach_vat_id — mocked Stripe
# ---------------------------------------------------------------------------


class _FakeCustomerTaxIds:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.created: list[tuple[str, dict]] = []

    def list_tax_ids(self, customer_id, **kwargs):
        return {"data": self.existing}

    def create_tax_id(self, customer_id, **kwargs):
        self.created.append((customer_id, kwargs))
        return {"id": "txi_new", **kwargs}


@pytest.fixture
def fake_stripe(monkeypatch):
    fake = _FakeCustomerTaxIds()
    monkeypatch.setattr(stripe.Customer, "list_tax_ids", fake.list_tax_ids)
    monkeypatch.setattr(stripe.Customer, "create_tax_id", fake.create_tax_id)
    return fake


def test_attach_norwegian_vat_id(fake_stripe):
    result = tax.attach_vat_id("cus_1", "NO 999 888 777 MVA")
    assert fake_stripe.created == [("cus_1", {"type": "no_vat", "value": "999888777MVA"})]
    assert result["id"] == "txi_new"


def test_attach_eu_vat_id(fake_stripe):
    tax.attach_vat_id("cus_2", "fr 12 345678901")
    assert fake_stripe.created == [("cus_2", {"type": "eu_vat", "value": "FR12345678901"})]


def test_attach_is_idempotent(fake_stripe):
    already = {"id": "txi_old", "type": "eu_vat", "value": "DE123456789"}
    fake_stripe.existing = [already]
    assert tax.attach_vat_id("cus_3", "DE 123456789") is already
    assert fake_stripe.created == []


def test_attach_adds_alongside_a_different_id(fake_stripe):
    fake_stripe.existing = [{"id": "txi_old", "type": "eu_vat", "value": "DE123456789"}]
    tax.attach_vat_id("cus_4", "123456789MVA")
    assert fake_stripe.created == [("cus_4", {"type": "no_vat", "value": "123456789MVA"})]


def test_attach_rejects_bad_number_without_calling_stripe(fake_stripe):
    with pytest.raises(ValueError, match="not a Norwegian or EU VAT number"):
        tax.attach_vat_id("cus_5", "GB123456789")
    assert fake_stripe.created == []


def test_attach_requires_customer(fake_stripe):
    with pytest.raises(ValueError, match="customer_id"):
        tax.attach_vat_id("", "DE123456789")


def test_stripe_errors_propagate(monkeypatch):
    """A Stripe rejection (e.g. invalid format) reaches the caller unchanged."""

    def boom(customer_id, **kwargs):
        raise stripe.error.InvalidRequestError("Invalid value for eu_vat.", "value")

    monkeypatch.setattr(stripe.Customer, "list_tax_ids", lambda c, **kw: {"data": []})
    monkeypatch.setattr(stripe.Customer, "create_tax_id", boom)
    with pytest.raises(stripe.error.InvalidRequestError):
        tax.attach_vat_id("cus_6", "DE123456789")
