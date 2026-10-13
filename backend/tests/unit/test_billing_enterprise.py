"""Unit tests for services/billing/enterprise.py.

Stripe is mocked at the ``stripe_client`` boundary, as in test_seat_sync.py.
Subscriptions are plain dicts shaped like Stripe's, with the price lookup
key on each item, which is what the service matches on.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.tenant import SubscriptionStatus, Tenant
from app.services.billing import catalog, enterprise, stripe_client, webhooks

PRICE_IDS = {key: f"price_{key}" for key in (p.lookup_key for p in catalog.PRICES)}


def _tenant(**kw) -> Tenant:
    t = Tenant(name="Acme Insurance", slug="acme")
    t.id = uuid.uuid4()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _item(key: str, quantity: int, n: int = 0) -> dict:
    return {"id": f"si_{key}_{n}", "quantity": quantity, "price": {"lookup_key": key}}


def _sub(*items: dict, pilot: bool = False, status: str = "active", sub_id="sub_1") -> dict:
    return {
        "id": sub_id,
        "status": status,
        "metadata": {
            enterprise.KIND_KEY: enterprise.KIND_ENTERPRISE,
            enterprise.PILOT_KEY: "true" if pilot else "false",
        },
        "items": {"data": list(items)},
    }


@pytest.fixture
def stripe_mock(monkeypatch):
    """Patch the stripe_client calls the service makes; return the mocks."""
    m = MagicMock()
    m.subscriptions = []
    m.list_subscriptions.side_effect = lambda **kw: m.subscriptions
    m.list_prices.side_effect = lambda *, lookup_keys, **kw: [{"id": PRICE_IDS[lookup_keys[0]]}]
    m.create_customer.return_value = {"id": "cus_new"}
    m.create_subscription.side_effect = lambda **kw: {"id": "sub_new", **kw}
    m.modify_subscription.side_effect = lambda sub_id, **kw: {"id": sub_id, **kw}
    for name in (
        "list_subscriptions",
        "list_prices",
        "create_customer",
        "create_subscription",
        "modify_subscription",
    ):
        monkeypatch.setattr(stripe_client, name, getattr(m, name))
    return m


def _modify_items(stripe_mock) -> list[dict]:
    return stripe_mock.modify_subscription.call_args.kwargs["items"]


# ---------------------------------------------------------------------------
# start_work_client (#45)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_customer_and_free_subscription(stripe_mock):
    t = _tenant()
    db = AsyncMock()

    await enterprise.start_work_client(db, t, provisioned_users=1000)

    assert t.stripe_customer_id == "cus_new"
    assert stripe_mock.create_customer.call_args.kwargs["metadata"] == {"tenant_id": str(t.id)}
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["customer"] == "cus_new"
    assert kw["items"] == [{"price": PRICE_IDS[catalog.WORK_CLIENT], "quantity": 1000}]
    assert kw["collection_method"] == "send_invoice"
    assert kw["metadata"][enterprise.KIND_KEY] == enterprise.KIND_ENTERPRISE
    assert kw["metadata"][enterprise.PILOT_KEY] == "false"
    assert kw["idempotency_key"] == f"promptly-work-client-{t.id}"


@pytest.mark.asyncio
async def test_start_reuses_existing_customer(stripe_mock):
    t = _tenant(stripe_customer_id="cus_team")
    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=50, pilot=True)
    stripe_mock.create_customer.assert_not_called()
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["customer"] == "cus_team"
    assert kw["metadata"][enterprise.PILOT_KEY] == "true"


@pytest.mark.asyncio
async def test_start_leaves_team_plan_columns_alone(stripe_mock):
    t = _tenant(
        stripe_customer_id="cus_team",
        stripe_subscription_id="sub_team",
        stripe_subscription_item_id="si_team",
        subscription_status=SubscriptionStatus.ACTIVE,
    )
    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=50)
    assert t.stripe_subscription_id == "sub_team"
    assert t.stripe_subscription_item_id == "si_team"


@pytest.mark.asyncio
async def test_start_is_one_per_tenant(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 1000))]

    result = await enterprise.start_work_client(AsyncMock(), t, provisioned_users=1000)

    stripe_mock.create_subscription.assert_not_called()
    stripe_mock.modify_subscription.assert_not_called()
    assert result["id"] == "sub_1"


@pytest.mark.asyncio
async def test_start_updates_provisioned_users_on_existing(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 1000))]

    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=1200)

    stripe_mock.create_subscription.assert_not_called()
    assert _modify_items(stripe_mock) == [{"id": f"si_{catalog.WORK_CLIENT}_0", "quantity": 1200}]
    assert stripe_mock.modify_subscription.call_args.kwargs["proration_behavior"] == "none"


@pytest.mark.asyncio
async def test_start_ignores_canceled_and_team_subscriptions(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 10), status="canceled", sub_id="sub_old"),
        {"id": "sub_team", "status": "active", "items": {"data": [_item("team_annual", 5)]}},
    ]
    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=10)
    stripe_mock.create_subscription.assert_called_once()


@pytest.mark.asyncio
async def test_start_rejects_negative_quantity(stripe_mock):
    with pytest.raises(ValueError, match="negative"):
        await enterprise.start_work_client(AsyncMock(), _tenant(), provisioned_users=-1)


# ---------------------------------------------------------------------------
# add_module / remove_module (#46) and the platform fee (#47)
# ---------------------------------------------------------------------------


def test_first_module_adds_platform_fee_in_same_call(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 1000))]

    enterprise.add_module(t, "suite", licensed_users=1000)

    stripe_mock.modify_subscription.assert_called_once()
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["items"] == [
        {"price": PRICE_IDS[catalog.SUITE_ANNUAL], "quantity": 1000},
        {"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1},
    ]
    # The annual contract starts at the first sale.
    assert kw["billing_cycle_anchor"] == "now"


def test_second_module_does_not_add_fee_again(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 1000),
            _item(catalog.ATLAS_ANNUAL, 1000),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]

    enterprise.add_module(t, "learn", licensed_users=300)

    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["items"] == [{"price": PRICE_IDS[catalog.LEARN_ANNUAL], "quantity": 300}]
    assert kw["proration_behavior"] == "always_invoice"
    assert "billing_cycle_anchor" not in kw


def test_fee_added_once_even_if_first_module_was_removed(stripe_mock):
    """The fee stays after a module is removed, so re-adding does not double it."""
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 1000), _item(catalog.PLATFORM_FEE_ANNUAL, 1))
    ]
    enterprise.add_module(t, "adopt", licensed_users=200)
    prices = [i.get("price") for i in _modify_items(stripe_mock)]
    assert prices == [PRICE_IDS[catalog.ADOPT_ANNUAL]]


def test_pilot_waives_platform_fee(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 50), pilot=True)]

    enterprise.add_module(t, "atlas", licensed_users=50)

    assert _modify_items(stripe_mock) == [
        {"price": PRICE_IDS[catalog.ATLAS_ANNUAL], "quantity": 50}
    ]


def test_existing_module_updates_quantity(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 1000),
            _item(catalog.ATLAS_ANNUAL, 500),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]
    enterprise.add_module(t, "atlas", licensed_users=800)
    assert _modify_items(stripe_mock) == [{"id": f"si_{catalog.ATLAS_ANNUAL}_0", "quantity": 800}]


def test_same_quantity_is_a_no_op(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 1000),
            _item(catalog.ATLAS_ANNUAL, 500),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]
    enterprise.add_module(t, "atlas", licensed_users=500)
    stripe_mock.modify_subscription.assert_not_called()


def test_suite_replaces_individual_modules(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 1000),
            _item(catalog.ATLAS_ANNUAL, 1000),
            _item(catalog.LEARN_ANNUAL, 1000),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]
    enterprise.add_module(t, "suite", licensed_users=1000)
    items = _modify_items(stripe_mock)
    assert items[0] == {"price": PRICE_IDS[catalog.SUITE_ANNUAL], "quantity": 1000}
    assert sorted(i["id"] for i in items[1:]) == sorted(
        [f"si_{catalog.ATLAS_ANNUAL}_0", f"si_{catalog.LEARN_ANNUAL}_0"]
    )
    assert all(i["deleted"] for i in items[1:])


def test_module_covered_by_suite_is_refused(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 1000), _item(catalog.SUITE_ANNUAL, 1000))
    ]
    with pytest.raises(enterprise.ModuleConflict):
        enterprise.add_module(t, "learn", licensed_users=10)
    stripe_mock.modify_subscription.assert_not_called()


def test_add_module_without_work_client_subscription_raises(stripe_mock):
    with pytest.raises(enterprise.NoEnterpriseSubscription):
        enterprise.add_module(_tenant(stripe_customer_id="cus_1"), "atlas", licensed_users=5)
    with pytest.raises(enterprise.NoEnterpriseSubscription):
        enterprise.add_module(_tenant(), "atlas", licensed_users=5)


def test_unknown_module_raises(stripe_mock):
    with pytest.raises(ValueError, match="Unknown module"):
        enterprise.add_module(_tenant(stripe_customer_id="cus_1"), "copilot", licensed_users=5)


def test_zero_licensed_users_raises(stripe_mock):
    with pytest.raises(ValueError, match="at least 1"):
        enterprise.add_module(_tenant(stripe_customer_id="cus_1"), "atlas", licensed_users=0)


def test_remove_module_deletes_item_and_keeps_fee(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 1000),
            _item(catalog.ADOPT_ANNUAL, 200),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]
    enterprise.remove_module(t, "adopt")
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["items"] == [{"id": f"si_{catalog.ADOPT_ANNUAL}_0", "deleted": True}]
    assert kw["proration_behavior"] == "none"


def test_remove_absent_module_is_a_no_op(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 1000))]
    enterprise.remove_module(t, "adopt")
    stripe_mock.modify_subscription.assert_not_called()


# ---------------------------------------------------------------------------
# end_pilot (#47: fee waived for pilots)
# ---------------------------------------------------------------------------


def test_end_pilot_adds_fee_when_a_module_is_paid(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 50), _item(catalog.ATLAS_ANNUAL, 50), pilot=True)
    ]
    enterprise.end_pilot(t)
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["metadata"] == {enterprise.PILOT_KEY: "false"}
    assert kw["items"] == [{"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1}]


def test_end_pilot_without_modules_only_clears_flag(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 50), pilot=True)]
    enterprise.end_pilot(t)
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw == {"metadata": {enterprise.PILOT_KEY: "false"}}


def test_end_pilot_on_converted_customer_is_a_no_op(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(
            _item(catalog.WORK_CLIENT, 50),
            _item(catalog.ATLAS_ANNUAL, 50),
            _item(catalog.PLATFORM_FEE_ANNUAL, 1),
        )
    ]
    enterprise.end_pilot(t)
    stripe_mock.modify_subscription.assert_not_called()


# ---------------------------------------------------------------------------
# Webhooks must not let the enterprise subscription overwrite Team-plan state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        webhooks._on_subscription_updated,
        webhooks._on_subscription_deleted,
    ],
)
async def test_webhook_skips_enterprise_subscription(handler):
    db = AsyncMock()
    obj = {**_sub(_item(catalog.WORK_CLIENT, 1000)), "customer": "cus_1", "status": "canceled"}
    await handler(db, obj)
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [webhooks._on_invoice_paid, webhooks._on_invoice_payment_failed],
)
async def test_webhook_skips_enterprise_invoice(handler):
    db = AsyncMock()
    obj = {
        "customer": "cus_1",
        "subscription_details": {"metadata": {enterprise.KIND_KEY: enterprise.KIND_ENTERPRISE}},
    }
    await handler(db, obj)
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_still_handles_team_subscription():
    t = _tenant(stripe_customer_id="cus_1", subscription_status=SubscriptionStatus.ACTIVE)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=t)
    obj = {"customer": "cus_1", "status": "past_due", "items": {"data": [{"quantity": 7}]}}
    await webhooks._on_subscription_updated(db, obj)
    assert t.subscription_status == SubscriptionStatus.PAST_DUE
    assert t.seats_billed == 7
