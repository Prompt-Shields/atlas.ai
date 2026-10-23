"""Unit tests for services/billing/enterprise.py.

Stripe is mocked at the ``stripe_client`` boundary, as in test_seat_sync.py.
Subscriptions are plain dicts shaped like Stripe's, with the price lookup
key on each item, which is what the service matches on.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta, timezone
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

    await enterprise.start_work_client(
        db, t, provisioned_users=1000, billing_email="ap@acme.example"
    )

    assert t.stripe_customer_id == "cus_new"
    assert stripe_mock.create_customer.call_args.kwargs["metadata"] == {"tenant_id": str(t.id)}
    # send_invoice needs an email on the customer.
    assert stripe_mock.create_customer.call_args.kwargs["email"] == "ap@acme.example"
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["customer"] == "cus_new"
    assert kw["items"] == [{"price": PRICE_IDS[catalog.WORK_CLIENT], "quantity": 1000}]
    assert kw["collection_method"] == "send_invoice"
    assert kw["metadata"][enterprise.KIND_KEY] == enterprise.KIND_ENTERPRISE
    assert kw["metadata"][enterprise.PILOT_KEY] == "false"
    assert kw["idempotency_key"] == f"promptly-work-client-{t.id}"


@pytest.mark.asyncio
async def test_start_without_customer_needs_billing_email(stripe_mock):
    t = _tenant()
    with pytest.raises(enterprise.MissingBillingEmail):
        await enterprise.start_work_client(AsyncMock(), t, provisioned_users=10)
    stripe_mock.create_customer.assert_not_called()
    stripe_mock.create_subscription.assert_not_called()
    assert t.stripe_customer_id is None


@pytest.mark.asyncio
async def test_start_reuses_existing_customer(stripe_mock):
    t = _tenant(stripe_customer_id="cus_team")
    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=50, pilot=True)
    stripe_mock.create_customer.assert_not_called()
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["customer"] == "cus_team"
    assert kw["metadata"][enterprise.PILOT_KEY] == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_start_automatic_tax_follows_setting(stripe_mock, monkeypatch, enabled):
    settings = MagicMock(stripe_automatic_tax=enabled)
    monkeypatch.setattr("app.services.billing.enterprise.get_settings", lambda: settings)
    t = _tenant(stripe_customer_id="cus_team")
    await enterprise.start_work_client(AsyncMock(), t, provisioned_users=50)
    kw = stripe_mock.create_subscription.call_args.kwargs
    if enabled:
        assert kw["automatic_tax"] == {"enabled": True}
    else:
        assert "automatic_tax" not in kw


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
# Stripe Tax on changes (promptly-guide#50)
# ---------------------------------------------------------------------------


def _start_quantity(t):
    asyncio.run(enterprise.start_work_client(AsyncMock(), t, provisioned_users=1200))


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "change",
    [
        _start_quantity,
        lambda t: enterprise.add_module(t, "learn", licensed_users=300),
        lambda t: enterprise.add_module(t, "atlas", licensed_users=800),
        lambda t: enterprise.remove_module(t, "atlas"),
        enterprise.end_pilot,
    ],
    ids=["provisioned_users", "add_module", "module_quantity", "remove_module", "end_pilot"],
)
def test_every_change_follows_automatic_tax(stripe_mock, monkeypatch, change, enabled):
    """Each modify call carries the same setting as the create call.

    A subscription created while the flag was off then picks Stripe Tax up
    with its next change, as a Team or quoted one would.
    """
    settings = MagicMock(stripe_automatic_tax=enabled)
    monkeypatch.setattr("app.services.billing.enterprise.get_settings", lambda: settings)
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 1000), _item(catalog.ATLAS_ANNUAL, 500), pilot=True)
    ]

    change(t)

    stripe_mock.modify_subscription.assert_called_once()
    kw = stripe_mock.modify_subscription.call_args.kwargs
    if enabled:
        assert kw["automatic_tax"] == {"enabled": True}
    else:
        assert "automatic_tax" not in kw


# ---------------------------------------------------------------------------
# end_pilot (#47: fee waived for pilots)
# ---------------------------------------------------------------------------


# end_pilot clears the flag and records the day the pilot ended, so a converted
# pilot still counts as one in the conversion report (promptly-guide#43).
ENDED = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)
ENDED_METADATA = {enterprise.PILOT_KEY: "false", enterprise.PILOT_ENDED_KEY: "2026-10-23"}


def test_end_pilot_adds_fee_when_a_module_is_paid(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 50), _item(catalog.ATLAS_ANNUAL, 50), pilot=True)
    ]
    enterprise.end_pilot(t, now=ENDED)
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["metadata"] == ENDED_METADATA
    assert kw["items"] == [{"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1}]


def test_end_pilot_without_modules_clears_flag_and_records_the_day(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 50), pilot=True)]
    enterprise.end_pilot(t, now=ENDED)
    kw = stripe_mock.modify_subscription.call_args.kwargs
    kw.pop("automatic_tax", None)  # covered by test_every_change_follows_automatic_tax
    assert kw == {"metadata": ENDED_METADATA}


def test_end_pilot_records_the_utc_day(stripe_mock):
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [_sub(_item(catalog.WORK_CLIENT, 50), pilot=True)]
    # 01:30 on the 24th in Berlin is still the 23rd in UTC.
    enterprise.end_pilot(t, now=ENDED.astimezone(timezone(timedelta(hours=2))))
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["metadata"][enterprise.PILOT_ENDED_KEY] == "2026-10-23"


def test_end_pilot_for_a_customer_that_was_never_a_pilot_records_no_end(stripe_mock):
    """Not a pilot, a module paid, no fee: the fee is added, no pilot end is invented."""
    t = _tenant(stripe_customer_id="cus_1")
    stripe_mock.subscriptions = [
        _sub(_item(catalog.WORK_CLIENT, 50), _item(catalog.ATLAS_ANNUAL, 50))
    ]
    enterprise.end_pilot(t, now=ENDED)
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["metadata"] == {enterprise.PILOT_KEY: "false"}
    assert kw["items"] == [{"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1}]


@pytest.mark.parametrize(
    ("metadata", "pilot", "was", "ended"),
    [
        ({enterprise.PILOT_KEY: "true"}, True, True, None),
        (ENDED_METADATA, False, True, "2026-10-23"),
        ({enterprise.PILOT_KEY: "false"}, False, False, None),
        ({enterprise.PILOT_KEY: "false", enterprise.PILOT_ENDED_KEY: ""}, False, False, None),
        ({}, False, False, None),
    ],
    ids=["pilot", "ended", "never", "empty_end", "no_metadata"],
)
def test_was_pilot_reads_the_flag_or_the_end(metadata, pilot, was, ended):
    sub = {"metadata": metadata}
    assert enterprise.is_pilot(sub) is pilot
    assert enterprise.was_pilot(sub) is was
    assert enterprise.pilot_ended_on(sub) == ended


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


# Since module entitlements (#18), the enterprise subscription is routed to
# tenant.stripe_subscriptions instead of being skipped. Either way the Team
# columns must stay as they were.


def _team_tenant() -> Tenant:
    return _tenant(
        stripe_customer_id="cus_1",
        stripe_subscription_id="sub_team",
        subscription_status=SubscriptionStatus.ACTIVE,
        seats_billed=7,
        stripe_subscriptions={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        webhooks._on_subscription_updated,
        webhooks._on_subscription_deleted,
    ],
)
async def test_webhook_enterprise_subscription_leaves_team_state(handler):
    t = _team_tenant()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=t)
    obj = {**_sub(_item(catalog.WORK_CLIENT, 1000)), "customer": "cus_1", "status": "canceled"}
    await handler(db, obj)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.seats_billed == 7
    assert t.stripe_subscriptions["sub_1"]["status"] == "canceled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "status"),
    [(webhooks._on_invoice_paid, "active"), (webhooks._on_invoice_payment_failed, "past_due")],
)
async def test_webhook_enterprise_invoice_leaves_team_state(handler, status):
    t = _team_tenant()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=t)
    obj = {
        "customer": "cus_1",
        "subscription": "sub_1",
        "subscription_details": {"metadata": {enterprise.KIND_KEY: enterprise.KIND_ENTERPRISE}},
    }
    await handler(db, obj)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.stripe_subscriptions["sub_1"]["status"] == status


@pytest.mark.asyncio
async def test_webhook_still_handles_team_subscription():
    t = _tenant(stripe_customer_id="cus_1", subscription_status=SubscriptionStatus.ACTIVE)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=t)
    obj = {"customer": "cus_1", "status": "past_due", "items": {"data": [{"quantity": 7}]}}
    await webhooks._on_subscription_updated(db, obj)
    assert t.subscription_status == SubscriptionStatus.PAST_DUE
    assert t.seats_billed == 7
