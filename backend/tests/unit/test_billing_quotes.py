"""Unit tests for services/billing/quotes.py.

Stripe is mocked at the ``stripe_client`` boundary, as in test_seat_sync.

Tests:
  - module validation: unknown module, empty list, Suite mixed with singles
  - prices resolved by lookup_key; a missing key raises
  - quote: seats on each module, platform fee on the first paid module only
  - quote: send_invoice, 30-day terms and tenant metadata
  - quote: explicit platform-fee override skips the Stripe lookup
  - customer created (and stored) only when the tenant has none
  - finalize delegates to Stripe
  - accept: bank-transfer payment settings on subscription and first invoice,
    automatic cash-balance reconciliation
  - CLI: --modules and --platform-fee parsing reach the service
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.tenant import Tenant
from app.services.billing import quotes

PRICES = {
    "promptly_atlas_annual": "price_atlas",
    "promptly_learn_annual": "price_learn",
    "promptly_adopt_annual": "price_adopt",
    "promptly_suite_annual": "price_suite",
    "promptly_platform_fee_annual": "price_fee",
}


def _tenant(**kw):
    t = Tenant(name="Acme", slug="acme")
    t.id = uuid.uuid4()
    t.stripe_customer_id = "cus_1"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _sub(status: str, *lookup_keys: str) -> dict:
    return {
        "status": status,
        "items": {"data": [{"price": {"lookup_key": k}} for k in lookup_keys]},
    }


@pytest.fixture
def fake_stripe(monkeypatch):
    """Patch every stripe_client call the quote service makes."""
    calls: dict[str, list] = {}

    def record(name, result):
        def _fn(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result(*args, **kwargs) if callable(result) else result

        monkeypatch.setattr(f"app.services.billing.stripe_client.{name}", _fn)

    def list_prices(**kw):
        return {
            "data": [{"id": PRICES[k], "lookup_key": k} for k in kw["lookup_keys"] if k in PRICES]
        }

    record("list_prices", list_prices)
    record("list_subscriptions", {"data": []})
    record("create_customer", {"id": "cus_new"})
    record("create_quote", lambda **kw: {"id": "qt_1", **kw})
    record("finalize_quote", {"id": "qt_1", "status": "open"})
    record(
        "accept_quote",
        {"id": "qt_1", "status": "accepted", "customer": "cus_1", "subscription": "sub_1"},
    )
    record("modify_cash_balance", {})
    record("modify_subscription", {"id": "sub_1", "latest_invoice": "in_1"})
    record("modify_invoice", {"id": "in_1"})
    return calls


# ---------------------------------------------------------------------------
# Validation and price lookup
# ---------------------------------------------------------------------------


def test_validate_modules_normalises_and_dedupes():
    assert quotes.validate_modules([" Atlas", "learn", "atlas"]) == ["atlas", "learn"]


@pytest.mark.parametrize(
    "modules",
    [[], ["guide"], ["suite", "atlas"]],
)
def test_validate_modules_rejects(modules):
    with pytest.raises(quotes.QuoteError):
        quotes.validate_modules(modules)


def test_resolve_prices_by_lookup_key(fake_stripe):
    got = quotes.resolve_prices(["promptly_atlas_annual", "promptly_platform_fee_annual"])
    assert got == {
        "promptly_atlas_annual": "price_atlas",
        "promptly_platform_fee_annual": "price_fee",
    }
    _, kw = fake_stripe["list_prices"][0]
    assert kw["active"] is True


def test_resolve_prices_missing_key_raises(fake_stripe, monkeypatch):
    monkeypatch.setattr("app.services.billing.stripe_client.list_prices", lambda **kw: {"data": []})
    with pytest.raises(quotes.QuoteError, match="promptly_learn_annual"):
        quotes.resolve_prices(["promptly_learn_annual"])


# ---------------------------------------------------------------------------
# Quote creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_quote_includes_platform_fee(fake_stripe):
    quote = await quotes.create_module_quote(
        AsyncMock(), _tenant(), modules=["atlas", "learn"], seats=250
    )
    assert quote["line_items"] == [
        {"price": "price_atlas", "quantity": 250},
        {"price": "price_learn", "quantity": 250},
        {"price": "price_fee", "quantity": 1},
    ]


@pytest.mark.asyncio
async def test_quote_bills_by_invoice_with_30_day_terms(fake_stripe):
    t = _tenant()
    quote = await quotes.create_module_quote(AsyncMock(), t, modules=["suite"], seats=50)
    assert quote["customer"] == "cus_1"
    assert quote["collection_method"] == "send_invoice"
    assert quote["invoice_settings"] == {"days_until_due": 30}
    assert quote["metadata"]["tenant_id"] == str(t.id)
    assert quote["subscription_data"]["metadata"]["billing_kind"] == quotes.BILLING_KIND


@pytest.mark.asyncio
async def test_no_platform_fee_when_already_billed(fake_stripe, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.stripe_client.list_subscriptions",
        lambda **kw: {
            "data": [_sub("active", "promptly_atlas_annual", "promptly_platform_fee_annual")]
        },
    )
    quote = await quotes.create_module_quote(AsyncMock(), _tenant(), modules=["adopt"], seats=10)
    assert quote["line_items"] == [{"price": "price_adopt", "quantity": 10}]


@pytest.mark.asyncio
async def test_platform_fee_on_canceled_subscription_does_not_count(fake_stripe, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.stripe_client.list_subscriptions",
        lambda **kw: {"data": [_sub("canceled", "promptly_platform_fee_annual")]},
    )
    quote = await quotes.create_module_quote(AsyncMock(), _tenant(), modules=["adopt"], seats=10)
    assert {"price": "price_fee", "quantity": 1} in quote["line_items"]


@pytest.mark.asyncio
async def test_explicit_platform_fee_override_skips_lookup(fake_stripe):
    quote = await quotes.create_module_quote(
        AsyncMock(), _tenant(), modules=["atlas"], seats=5, include_platform_fee=False
    )
    assert "list_subscriptions" not in fake_stripe
    assert quote["line_items"] == [{"price": "price_atlas", "quantity": 5}]


@pytest.mark.asyncio
async def test_zero_seats_rejected(fake_stripe):
    with pytest.raises(quotes.QuoteError):
        await quotes.create_module_quote(AsyncMock(), _tenant(), modules=["atlas"], seats=0)
    assert "create_quote" not in fake_stripe


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_created_when_missing(fake_stripe):
    t = _tenant(stripe_customer_id=None)
    db = AsyncMock()
    quote = await quotes.create_module_quote(
        db, t, modules=["atlas"], seats=5, billing_email="ap@acme.example"
    )
    _, kw = fake_stripe["create_customer"][0]
    assert kw["email"] == "ap@acme.example"
    assert kw["metadata"] == {"tenant_id": str(t.id)}
    assert t.stripe_customer_id == "cus_new"
    assert quote["customer"] == "cus_new"
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_missing_customer_without_email_raises(fake_stripe):
    with pytest.raises(quotes.QuoteError, match="billing email"):
        await quotes.create_module_quote(
            AsyncMock(), _tenant(stripe_customer_id=None), modules=["atlas"], seats=5
        )
    assert "create_customer" not in fake_stripe


@pytest.mark.asyncio
async def test_existing_customer_not_recreated(fake_stripe):
    await quotes.create_module_quote(
        AsyncMock(), _tenant(), modules=["atlas"], seats=5, billing_email="x@acme.example"
    )
    assert "create_customer" not in fake_stripe


# ---------------------------------------------------------------------------
# Finalise and accept
# ---------------------------------------------------------------------------


def test_finalize_delegates(fake_stripe):
    assert quotes.finalize_quote("qt_1")["status"] == "open"
    assert fake_stripe["finalize_quote"][0][0] == ("qt_1",)


def test_accept_sets_bank_transfer_on_subscription_and_invoice(fake_stripe):
    quotes.accept_quote("qt_1", bank_transfer_country="NL")

    expected = {
        "payment_method_types": ["customer_balance"],
        "payment_method_options": {
            "customer_balance": {
                "funding_type": "bank_transfer",
                "bank_transfer": {
                    "type": "eu_bank_transfer",
                    "eu_bank_transfer": {"country": "NL"},
                },
            }
        },
    }
    args, kw = fake_stripe["modify_subscription"][0]
    assert args == ("sub_1",)
    assert kw["payment_settings"] == expected

    args, kw = fake_stripe["modify_invoice"][0]
    assert args == ("in_1",)
    assert kw["payment_settings"] == expected

    args, kw = fake_stripe["modify_cash_balance"][0]
    assert args == ("cus_1",)
    assert kw == {"settings": {"reconciliation_mode": "automatic"}}


def test_accept_uses_configured_country(fake_stripe, monkeypatch):
    settings = MagicMock(stripe_bank_transfer_country="DE")
    monkeypatch.setattr("app.services.billing.quotes.get_settings", lambda: settings)
    quotes.accept_quote("qt_1")
    _, kw = fake_stripe["modify_subscription"][0]
    bt = kw["payment_settings"]["payment_method_options"]["customer_balance"]["bank_transfer"]
    assert bt["eu_bank_transfer"] == {"country": "DE"}


def test_accept_without_subscription_raises(fake_stripe, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.stripe_client.accept_quote",
        lambda qid: {"id": qid, "customer": "cus_1", "subscription": None},
    )
    with pytest.raises(quotes.QuoteError):
        quotes.accept_quote("qt_1", bank_transfer_country="IE")
    assert "modify_subscription" not in fake_stripe


def test_accept_with_expanded_invoice(fake_stripe, monkeypatch):
    monkeypatch.setattr(
        "app.services.billing.stripe_client.modify_subscription",
        lambda sid, **kw: {"id": sid, "latest_invoice": {"id": "in_9"}},
    )
    quotes.accept_quote("qt_1", bank_transfer_country="IE")
    assert fake_stripe["modify_invoice"][0][0] == ("in_9",)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_finalize_and_accept(fake_stripe, capsys):
    from scripts import stripe_module_quote as cli

    assert cli.main(["finalize", "qt_1"]) == 0
    assert cli.main(["accept", "qt_1", "--bank-country", "FR"]) == 0
    _, kw = fake_stripe["modify_subscription"][0]
    bt = kw["payment_settings"]["payment_method_options"]["customer_balance"]["bank_transfer"]
    assert bt["eu_bank_transfer"] == {"country": "FR"}
    assert "sub_1" in capsys.readouterr().out


def test_cli_create_passes_options(monkeypatch):
    from scripts import stripe_module_quote as cli

    seen = {}

    async def fake_create(args):
        seen.update(vars(args))

    monkeypatch.setattr(cli, "_create", fake_create)
    assert (
        cli.main(
            [
                "create",
                "--tenant",
                "acme",
                "--modules",
                "atlas,learn",
                "--seats",
                "9",
                "--no-platform-fee",
            ]
        )
        == 0
    )
    assert seen["modules"] == "atlas,learn"
    assert seen["seats"] == 9
    assert seen["platform_fee"] is False


def test_cli_reports_quote_error(fake_stripe, monkeypatch, capsys):
    from scripts import stripe_module_quote as cli

    def boom(qid, **kw):
        raise quotes.QuoteError("nope")

    monkeypatch.setattr(quotes, "accept_quote", boom)
    assert cli.main(["accept", "qt_1"]) == 1
    assert "nope" in capsys.readouterr().err
