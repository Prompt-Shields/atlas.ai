"""Unit tests for the customer portal configurations and sessions (promptly-guide#64).

Stripe is replaced by an in-memory fake patched over the ``stripe_client``
wrappers, as in ``test_stripe_catalog.py``. No amounts appear here: the
portal only names price ids.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from app.config import get_settings
from app.models.tenant import Plan, Tenant
from app.services.billing import portal, stripe_client

pytestmark = pytest.mark.unit


class FakeStripe:
    def __init__(self) -> None:
        self.configurations: list[dict] = []
        self.prices = {
            "price_plus_m": {"id": "price_plus_m", "product": "prod_plus"},
            "price_plus_y": {"id": "price_plus_y", "product": {"id": "prod_plus"}},
        }
        self.writes: list[str] = []
        self.sessions: list[dict] = []
        self._ids = itertools.count(1)

    def list_portal_configurations(self):
        return list(self.configurations)

    def create_portal_configuration(self, **kw):
        conf = {"id": f"bpc_{next(self._ids)}", "active": True, **kw}
        self.writes.append(f"create {kw['metadata'][portal.PORTAL_KEY]}")
        self.configurations.append(conf)
        return conf

    def modify_portal_configuration(self, conf_id, **kw):
        self.writes.append(f"modify {conf_id}")
        conf = next(c for c in self.configurations if c["id"] == conf_id)
        conf.update(kw)
        return conf

    def retrieve_price(self, price_id):
        return self.prices[price_id]

    def create_portal_session(self, **kw):
        self.sessions.append(kw)
        return {"url": "https://billing.stripe.test/p"}


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()
    for name in (
        "list_portal_configurations",
        "create_portal_configuration",
        "modify_portal_configuration",
        "retrieve_price",
        "create_portal_session",
    ):
        monkeypatch.setattr(stripe_client, name, getattr(f, name))
    return f


PLUS_PRICES = ["price_plus_m", "price_plus_y"]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def test_enterprise_sees_invoices_and_payment_details_only() -> None:
    f = portal.enterprise_features()
    assert f["invoice_history"]["enabled"] is True
    assert f["payment_method_update"]["enabled"] is True
    assert "tax_id" in f["customer_update"]["allowed_updates"]
    # Seats change in the admin console; modules are sold by Quote.
    assert f["subscription_update"] == {"enabled": False}
    assert f["subscription_cancel"] == {"enabled": False}


def test_plus_manages_plan_payment_method_and_cancellation() -> None:
    f = portal.plus_features({"prod_plus": ["price_plus_y", "price_plus_m"]})
    assert f["payment_method_update"]["enabled"] is True
    assert f["invoice_history"]["enabled"] is True
    assert f["subscription_update"]["enabled"] is True
    assert f["subscription_update"]["default_allowed_updates"] == ["price"]
    assert f["subscription_update"]["products"] == [
        {"product": "prod_plus", "prices": ["price_plus_m", "price_plus_y"]}
    ]
    cancel = f["subscription_cancel"]
    assert cancel["enabled"] is True and cancel["mode"] == "at_period_end"
    # Consumers have no VAT ID.
    assert "tax_id" not in f["customer_update"]["allowed_updates"]


def test_plus_without_prices_can_cancel_but_not_switch() -> None:
    f = portal.plus_features({})
    assert f["subscription_update"] == {"enabled": False}
    assert f["subscription_cancel"]["enabled"] is True


def test_plus_products_groups_prices_by_product(fake) -> None:
    assert portal.plus_products(["price_plus_m", "", "price_plus_y", "price_plus_m"]) == {
        "prod_plus": ["price_plus_m", "price_plus_y"]
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_first_run_creates_both_then_second_run_changes_nothing(fake) -> None:
    first = portal.sync_portal_configurations(PLUS_PRICES)
    assert [(a.verb, a.kind) for a in first] == [("create", "enterprise"), ("create", "plus")]
    assert all(a.configuration_id for a in first)
    assert fake.writes == ["create enterprise", "create plus"]

    fake.writes.clear()
    second = portal.sync_portal_configurations(PLUS_PRICES)
    assert [a.verb for a in second] == ["ok", "ok"]
    assert [a.configuration_id for a in second] == [a.configuration_id for a in first]
    assert fake.writes == []


def test_drifted_configuration_is_updated_and_reactivated(fake) -> None:
    portal.sync_portal_configurations(PLUS_PRICES)
    ent = fake.configurations[0]
    ent["features"] = {**ent["features"], "subscription_cancel": {"enabled": True}}
    ent["active"] = False

    actions = portal.sync_portal_configurations(PLUS_PRICES)
    assert actions[0].verb == "update" and "features" in actions[0].detail
    assert "active" in actions[0].detail
    assert ent["active"] is True and ent["features"]["subscription_cancel"] == {"enabled": False}
    assert actions[1].verb == "ok"


def test_configurations_not_ours_are_left_alone(fake) -> None:
    fake.configurations.append({"id": "bpc_default", "metadata": {}, "features": {}})
    portal.sync_portal_configurations(PLUS_PRICES)
    assert fake.configurations[0] == {"id": "bpc_default", "metadata": {}, "features": {}}


def test_dry_run_writes_nothing(fake) -> None:
    actions = portal.sync_portal_configurations(PLUS_PRICES, dry_run=True)
    assert [a.verb for a in actions] == ["create", "create"]
    assert fake.writes == []


def test_offline_plan_calls_no_stripe(monkeypatch) -> None:
    def boom(*a, **kw):
        raise AssertionError("Stripe called")

    monkeypatch.setattr(stripe_client, "list_portal_configurations", boom)
    monkeypatch.setattr(stripe_client, "retrieve_price", boom)
    actions = portal.sync_portal_configurations(PLUS_PRICES, offline=True)
    assert [a.verb for a in actions] == ["create", "create"]


def test_contains_compares_only_fields_we_set() -> None:
    have = {"enabled": True, "products": [{"product": "p", "prices": ["a"], "x": 1}], "y": 2}
    assert portal._contains(
        have, {"enabled": True, "products": [{"product": "p", "prices": ["a"]}]}
    )
    assert not portal._contains(have, {"products": [{"product": "p", "prices": ["a", "b"]}]})
    assert not portal._contains(None, {"enabled": True})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _tenant(plan=Plan.TEAM, subs=None) -> Tenant:
    return Tenant(
        id=uuid.uuid4(), plan=plan, stripe_customer_id="cus_t", stripe_subscriptions=subs or {}
    )


def test_enterprise_tenant_gets_enterprise_configuration(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_portal_configuration_enterprise", "bpc_ent")
    subs = {"sub_1": {"kind": "enterprise", "status": "active", "items": {}}}
    params = portal.tenant_portal_params(_tenant(subs=subs), s)
    assert params == {
        "customer": "cus_t",
        "return_url": s.billing_portal_return_url,
        "configuration": "bpc_ent",
    }
    assert portal.tenant_portal_params(_tenant(plan=Plan.ENTERPRISE), s)["configuration"] == (
        "bpc_ent"
    )


def test_team_tenant_keeps_stripe_default(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_portal_configuration_enterprise", "bpc_ent")
    assert "configuration" not in portal.tenant_portal_params(_tenant(), s)


def test_enterprise_without_configuration_falls_back_to_default(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_portal_configuration_enterprise", "")
    params = portal.tenant_portal_params(_tenant(plan=Plan.ENTERPRISE), s)
    assert "configuration" not in params


def test_plus_portal_reuses_customer_and_never_creates_one() -> None:
    params = portal.plus_portal_params(
        customer_id="cus_plus", return_url="https://r", configuration="bpc_plus"
    )
    assert params == {
        "customer": "cus_plus",
        "return_url": "https://r",
        "configuration": "bpc_plus",
    }
    for missing in (None, ""):
        with pytest.raises(portal.NoCustomerError):
            portal.plus_portal_params(customer_id=missing, return_url="https://r")


def test_plus_portal_without_configuration_uses_default() -> None:
    params = portal.plus_portal_params(customer_id="cus_plus", return_url="https://r")
    assert "configuration" not in params


def test_create_plus_portal_session_uses_plus_settings(fake, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "stripe_portal_configuration_plus", "bpc_plus")
    monkeypatch.setattr(s, "billing_portal_return_url_plus", "https://guide.test/account")
    assert portal.create_plus_portal_session("cus_plus", s) == "https://billing.stripe.test/p"
    assert fake.sessions == [
        {
            "customer": "cus_plus",
            "return_url": "https://guide.test/account",
            "configuration": "bpc_plus",
        }
    ]
    with pytest.raises(portal.NoCustomerError):
        portal.create_plus_portal_session(None, s)
    assert len(fake.sessions) == 1


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------


def test_script_refuses_live_key_without_flag(monkeypatch) -> None:
    from scripts import stripe_portal

    monkeypatch.setattr(stripe_portal.get_settings(), "stripe_secret_key", "sk_live_x")
    assert stripe_portal.main([]) == 2


def test_script_dry_run_without_key_plans_offline(monkeypatch, capsys) -> None:
    from scripts import stripe_portal

    monkeypatch.setattr(stripe_portal.get_settings(), "stripe_secret_key", "")
    assert stripe_portal.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "enterprise" in out and "plus" in out


def test_script_prints_settings_to_set(fake, monkeypatch, capsys) -> None:
    from scripts import stripe_portal

    s = stripe_portal.get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_price_plus_monthly", "price_plus_m")
    monkeypatch.setattr(s, "stripe_price_plus_yearly", "price_plus_y")
    assert stripe_portal.main([]) == 0
    out = capsys.readouterr().out
    assert "STRIPE_PORTAL_CONFIGURATION_ENTERPRISE=bpc_" in out
    assert "STRIPE_PORTAL_CONFIGURATION_PLUS=bpc_" in out
