"""Tests for scripts/stripe_enterprise.py, the operator entry point of
services/billing/enterprise.py and tax.attach_vat_id.

Stripe is mocked at the ``stripe_client`` boundary, as in
test_billing_enterprise.py. The script's session is pointed at the test
database, so tenants are found by slug or id and the Stripe customer id it
stores is committed.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.models.tenant import Tenant
from app.services.billing import catalog, enterprise, stripe_client
from scripts import stripe_enterprise as cli
from tests.conftest import TestSessionLocal, ensure_tenant

PRICE_IDS = {p.lookup_key: f"price_{p.lookup_key}" for p in catalog.PRICES}
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e5")


@pytest.fixture
def stripe_mock(monkeypatch):
    m = MagicMock()
    m.subscriptions = []
    m.tax_ids = []
    m.list_subscriptions.side_effect = lambda **kw: m.subscriptions
    m.list_prices.side_effect = lambda *, lookup_keys, **kw: [{"id": PRICE_IDS[lookup_keys[0]]}]
    m.create_customer.return_value = {"id": "cus_new"}
    m.create_subscription.side_effect = lambda **kw: {"id": "sub_new", **kw}
    m.modify_subscription.side_effect = lambda sub_id, **kw: {"id": sub_id, **kw}
    m.list_tax_ids.side_effect = lambda customer_id, **kw: {"data": m.tax_ids}
    m.create_tax_id.side_effect = lambda customer_id, **kw: {"id": "txi_1", **kw}
    for name in (
        "list_subscriptions",
        "list_prices",
        "create_customer",
        "create_subscription",
        "modify_subscription",
        "list_tax_ids",
        "create_tax_id",
    ):
        monkeypatch.setattr(stripe_client, name, getattr(m, name))
    return m


@pytest.fixture
async def tenant(monkeypatch):
    @asynccontextmanager
    async def _session():
        async with TestSessionLocal() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(cli, "get_standalone_session", _session)
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TENANT_ID, name="Acme Insurance", slug="acme")
        await session.commit()
    return TENANT_ID


async def _stored_customer() -> str | None:
    async with TestSessionLocal() as session:
        return await session.scalar(select(Tenant.stripe_customer_id).where(Tenant.id == TENANT_ID))


async def _set_customer(customer_id: str) -> None:
    async with TestSessionLocal() as session:
        t = await session.get(Tenant, TENANT_ID)
        t.stripe_customer_id = customer_id
        await session.commit()


def _args(*argv: str):
    return cli._parser().parse_args(list(argv))


def _work_client_sub(*extra: dict, pilot: bool = False) -> dict:
    items = [{"id": "si_wc", "quantity": 10, "price": {"lookup_key": catalog.WORK_CLIENT}}]
    return {
        "id": "sub_1",
        "status": "active",
        "metadata": {
            enterprise.KIND_KEY: enterprise.KIND_ENTERPRISE,
            enterprise.PILOT_KEY: "true" if pilot else "false",
        },
        "items": {"data": items + list(extra)},
    }


async def test_start_creates_work_client_and_stores_customer(stripe_mock, tenant):
    out = await cli._run(
        _args("start", "--tenant", "acme", "--users", "250", "--billing-email", "ap@acme.test")
    )
    assert "sub_new" in out
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["items"] == [{"price": PRICE_IDS[catalog.WORK_CLIENT], "quantity": 250}]
    assert kw["collection_method"] == "send_invoice"
    assert kw["metadata"][enterprise.PILOT_KEY] == "false"
    assert kw["metadata"]["tenant_id"] == str(TENANT_ID)
    assert await _stored_customer() == "cus_new"


async def test_start_by_tenant_id_as_pilot(stripe_mock, tenant):
    await _set_customer("cus_old")
    await cli._run(_args("start", "--tenant", str(TENANT_ID), "--users", "5", "--pilot"))
    stripe_mock.create_customer.assert_not_called()
    kw = stripe_mock.create_subscription.call_args.kwargs
    assert kw["customer"] == "cus_old"
    assert kw["metadata"][enterprise.PILOT_KEY] == "true"


async def test_start_without_customer_or_email_is_refused(stripe_mock, tenant):
    with pytest.raises(enterprise.MissingBillingEmail):
        await cli._run(_args("start", "--tenant", "acme", "--users", "5"))
    stripe_mock.create_subscription.assert_not_called()


async def test_add_module_adds_item_and_platform_fee(stripe_mock, tenant):
    await _set_customer("cus_old")
    stripe_mock.subscriptions = [_work_client_sub()]
    out = await cli._run(
        _args("add-module", "--tenant", "acme", "--module", "learn", "--users", "9")
    )
    assert "learn" in out
    items = stripe_mock.modify_subscription.call_args.kwargs["items"]
    assert {"price": PRICE_IDS[catalog.LEARN_ANNUAL], "quantity": 9} in items
    assert {"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1} in items


async def test_remove_module_and_end_pilot(stripe_mock, tenant):
    await _set_customer("cus_old")
    learn = {"id": "si_learn", "quantity": 9, "price": {"lookup_key": catalog.LEARN_ANNUAL}}
    stripe_mock.subscriptions = [_work_client_sub(learn, pilot=True)]

    await cli._run(_args("remove-module", "--tenant", "acme", "--module", "learn"))
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["items"] == [{"id": "si_learn", "deleted": True}]

    await cli._run(_args("end-pilot", "--tenant", "acme"))
    kw = stripe_mock.modify_subscription.call_args.kwargs
    assert kw["metadata"] == {enterprise.PILOT_KEY: "false"}
    # A module is still on the (unchanged) mock subscription, so the fee is added.
    assert kw["items"] == [{"price": PRICE_IDS[catalog.PLATFORM_FEE_ANNUAL], "quantity": 1}]


async def test_vat_id_attaches_to_tenant_customer(stripe_mock, tenant):
    await _set_customer("cus_old")
    out = await cli._run(_args("vat-id", "--tenant", "acme", "--vat", "de 123 456 789"))
    assert "eu_vat DE123456789" in out
    stripe_mock.create_tax_id.assert_called_once_with("cus_old", type="eu_vat", value="DE123456789")


async def test_vat_id_without_customer_is_refused(stripe_mock, tenant):
    with pytest.raises(cli.CliError, match="no Stripe customer"):
        await cli._run(_args("vat-id", "--tenant", "acme", "--vat", "DE123456789"))
    stripe_mock.create_tax_id.assert_not_called()


async def test_unknown_tenant_is_refused(stripe_mock, tenant):
    with pytest.raises(cli.CliError, match="nope"):
        await cli._run(_args("end-pilot", "--tenant", "nope"))


@pytest.mark.parametrize(
    "exc",
    [
        cli.CliError("no tenant"),
        enterprise.NoEnterpriseSubscription("no subscription"),
        ValueError("not a Norwegian or EU VAT number"),
        LookupError("No active Stripe price"),
    ],
)
def test_main_prints_errors_and_exits_1(monkeypatch, capsys, exc):
    async def boom(args):
        raise exc

    monkeypatch.setattr(cli, "_run", boom)
    assert cli.main(["end-pilot", "--tenant", "acme"]) == 1
    assert str(exc) in capsys.readouterr().err


def test_main_prints_result(monkeypatch, capsys):
    seen = {}

    async def fake_run(args):
        seen.update(vars(args))
        return "done"

    monkeypatch.setattr(cli, "_run", fake_run)
    assert cli.main(["add-module", "--tenant", "acme", "--module", "suite", "--users", "3"]) == 0
    assert seen == {"command": "add-module", "tenant": "acme", "module": "suite", "users": 3}
    assert capsys.readouterr().out.strip() == "done"


def test_unknown_module_is_rejected_by_the_parser(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["add-module", "--tenant", "acme", "--module", "chat", "--users", "3"])
    assert exit_info.value.code == 2
