"""Unit tests for the thin Stripe client wrapper.

Verifies:
- Module is importable
- Config settings are loaded (all default to "")
- Each wrapper function delegates to the correct Stripe SDK call (monkeypatched)
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_stripe_client_importable():
    """stripe_client module must be importable without a real API key."""
    from app.services.billing import stripe_client  # noqa: F401


def test_config_stripe_settings_present():
    """Config must expose the four required Stripe settings (all default '')."""
    from app.config import get_settings

    s = get_settings()
    assert hasattr(s, "stripe_secret_key")
    assert hasattr(s, "stripe_webhook_secret")
    assert hasattr(s, "stripe_price_team_monthly")
    assert hasattr(s, "stripe_price_team_annual")
    assert hasattr(s, "billing_portal_return_url")
    assert hasattr(s, "billing_checkout_success_url")
    assert hasattr(s, "billing_checkout_cancel_url")
    # All default to "" (empty string) when not set
    assert isinstance(s.stripe_secret_key, str)
    assert isinstance(s.stripe_webhook_secret, str)
    assert isinstance(s.stripe_price_team_monthly, str)
    assert isinstance(s.stripe_price_team_annual, str)


def test_create_checkout_session_delegates(monkeypatch):
    """create_checkout_session must call stripe.checkout.Session.create."""
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/test"

    import stripe

    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: fake_session)

    from app.services.billing import stripe_client

    result = stripe_client.create_checkout_session(mode="subscription", line_items=[])
    assert result is fake_session


def test_create_portal_session_delegates(monkeypatch):
    """create_portal_session must call stripe.billing_portal.Session.create."""
    fake_session = MagicMock()
    fake_session.url = "https://billing.stripe.com/session/test"

    import stripe

    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: fake_session)

    from app.services.billing import stripe_client

    result = stripe_client.create_portal_session(
        customer="cus_test", return_url="https://example.com"
    )
    assert result is fake_session


def test_modify_subscription_item_delegates(monkeypatch):
    """modify_subscription_item must call stripe.SubscriptionItem.modify."""
    fake_item = MagicMock()

    import stripe

    monkeypatch.setattr(stripe.SubscriptionItem, "modify", lambda item_id, **kw: fake_item)

    from app.services.billing import stripe_client

    result = stripe_client.modify_subscription_item("si_test123", quantity=5)
    assert result is fake_item


def test_construct_event_delegates(monkeypatch):
    """construct_event must call stripe.Webhook.construct_event with webhook secret."""
    fake_event = MagicMock()
    fake_event.type = "customer.subscription.updated"

    import stripe

    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: fake_event)

    from app.services.billing import stripe_client

    result = stripe_client.construct_event(b'{"type":"test"}', "t=123,v1=abc")
    assert result is fake_event


def test_tax_id_wrappers_delegate(monkeypatch):
    """list_tax_ids / create_tax_id must call the stripe.Customer tax-ID methods."""
    import stripe

    calls = []
    monkeypatch.setattr(
        stripe.Customer, "list_tax_ids", lambda cid, **kw: calls.append(("list", cid, kw))
    )
    monkeypatch.setattr(
        stripe.Customer, "create_tax_id", lambda cid, **kw: calls.append(("create", cid, kw))
    )

    from app.services.billing import stripe_client

    stripe_client.list_tax_ids("cus_1", limit=100)
    stripe_client.create_tax_id("cus_1", type="eu_vat", value="DE123456789")
    assert calls == [
        ("list", "cus_1", {"limit": 100}),
        ("create", "cus_1", {"type": "eu_vat", "value": "DE123456789"}),
    ]


def test_portal_configuration_wrappers_delegate(monkeypatch):
    """Portal configuration wrappers call stripe.billing_portal.Configuration."""
    import stripe

    calls = []
    page = MagicMock()
    page.auto_paging_iter.return_value = iter([{"id": "bpc_1"}])
    monkeypatch.setattr(stripe.billing_portal.Configuration, "list", lambda **kw: page)
    monkeypatch.setattr(
        stripe.billing_portal.Configuration,
        "create",
        lambda **kw: calls.append(("create", kw)) or {"id": "bpc_2"},
    )
    monkeypatch.setattr(
        stripe.billing_portal.Configuration,
        "modify",
        lambda cid, **kw: calls.append(("modify", cid, kw)) or {"id": cid},
    )
    monkeypatch.setattr(stripe.Price, "retrieve", lambda pid: {"id": pid, "product": "prod"})

    from app.services.billing import stripe_client

    assert stripe_client.list_portal_configurations() == [{"id": "bpc_1"}]
    assert stripe_client.create_portal_configuration(features={})["id"] == "bpc_2"
    assert stripe_client.modify_portal_configuration("bpc_1", active=True)["id"] == "bpc_1"
    assert stripe_client.retrieve_price("price_1")["product"] == "prod"
    assert calls == [("create", {"features": {}}), ("modify", "bpc_1", {"active": True})]
