"""Unit tests for the thin Stripe client wrapper.

Verifies:
- Module is importable
- Config settings are loaded (all default to "")
- Each wrapper function delegates to the correct Stripe SDK call (monkeypatched)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


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


# --- Real SDK objects (no fakes) --------------------------------------------
# Since stripe-python 15 a StripeObject is not a dict: `.get()` and `dict(obj)`
# raise. The tests above stub the SDK with dicts and mocks, so they cannot see
# that. These build the SDK's own objects offline and check that every wrapper
# hands the billing code plain dicts.

_SUBSCRIPTION = {
    "id": "sub_1",
    "object": "subscription",
    "customer": "cus_1",
    "status": "active",
    "metadata": {"tenant_id": "t1"},
    "items": {
        "object": "list",
        "data": [
            {
                "id": "si_1",
                "object": "subscription_item",
                "quantity": 7,
                "price": {"id": "price_1", "object": "price", "lookup_key": "team_monthly"},
            }
        ],
    },
}


def _sdk(cls, data):
    import stripe

    return getattr(stripe, cls).construct_from(data, "sk_test_offline")


def test_sdk_objects_are_not_dicts():
    """Guards the reason this module converts: the installed SDK has no dict methods."""
    import stripe

    sub = _sdk("Subscription", _SUBSCRIPTION)
    assert isinstance(sub, stripe.StripeObject)
    assert not isinstance(sub, dict)


def test_retrieve_subscription_returns_plain_dicts(monkeypatch):
    import stripe

    from app.services.billing import stripe_client

    monkeypatch.setattr(
        stripe.Subscription, "retrieve", lambda sid, **kw: _sdk("Subscription", _SUBSCRIPTION)
    )
    sub = stripe_client.retrieve_subscription("sub_1")
    assert type(sub) is dict
    item = sub.get("items", {}).get("data", [])[0]
    assert type(item) is dict
    assert item.get("price", {}).get("lookup_key") == "team_monthly"
    assert sub == _SUBSCRIPTION


def test_list_wrappers_return_plain_dicts(monkeypatch):
    import stripe

    from app.services.billing import stripe_client

    page = _sdk("ListObject", {"object": "list", "data": [_SUBSCRIPTION], "has_more": False})
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: page)
    monkeypatch.setattr(
        stripe.Customer,
        "list_tax_ids",
        lambda cid, **kw: _sdk(
            "ListObject",
            {"object": "list", "data": [{"object": "tax_id", "type": "eu_vat", "value": "DE1"}]},
        ),
    )
    subs = stripe_client.list_subscriptions(customer="cus_1", status="all")
    assert [type(s) for s in subs] == [dict]
    assert subs[0].get("status") == "active"
    tax_ids = stripe_client.list_tax_ids("cus_1", limit=100)
    assert tax_ids.get("data", [])[0].get("value") == "DE1"


def test_session_wrappers_return_url_as_a_key(monkeypatch):
    import stripe

    from app.services.billing import stripe_client

    session = stripe.checkout.Session.construct_from(
        {"id": "cs_1", "object": "checkout.session", "url": "https://checkout.stripe.test/x"},
        "sk_test_offline",
    )
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: session)
    assert stripe_client.create_checkout_session(mode="subscription")["url"] == (
        "https://checkout.stripe.test/x"
    )


def test_requests_pin_the_api_version(monkeypatch):
    """Upgrading the SDK must not move the API version our requests use."""
    import stripe

    from app.services.billing import stripe_client

    monkeypatch.setattr(stripe, "api_version", None)
    monkeypatch.setattr(stripe.Price, "retrieve", lambda pid: _sdk("Price", {"id": pid}))
    assert stripe_client.retrieve_price("price_1") == {"id": "price_1"}
    assert stripe.api_version == stripe_client.STRIPE_API_VERSION == "2024-06-20"


def _signed(payload: str, secret: str) -> str:
    import hashlib
    import hmac
    import time

    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_construct_event_verifies_with_the_real_sdk(monkeypatch):
    """A real signed payload through stripe.Webhook gives a plain dict event."""
    import json

    import stripe

    from app.config import get_settings
    from app.services.billing import stripe_client

    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "whsec_offline")
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "customer.subscription.updated",
            "created": 1700000000,
            "data": {"object": _SUBSCRIPTION},
        }
    )
    event = stripe_client.construct_event(payload.encode(), _signed(payload, "whsec_offline"))
    assert type(event) is dict
    obj = event["data"]["object"]
    assert type(obj) is dict
    assert obj.get("customer") == "cus_1"
    assert obj.get("items", {}).get("data", [])[0].get("quantity") == 7

    with pytest.raises(stripe.SignatureVerificationError):
        stripe_client.construct_event(payload.encode(), _signed(payload, "whsec_other"))


def test_fetch_subscription_item_id_with_a_real_subscription(monkeypatch):
    import stripe

    from app.services.billing import webhooks

    monkeypatch.setattr(
        stripe.Subscription, "retrieve", lambda sid, **kw: _sdk("Subscription", _SUBSCRIPTION)
    )
    assert webhooks._fetch_subscription_item_id("sub_1") == "si_1"


def test_retrieve_product_missing_returns_none(monkeypatch):
    import stripe

    from app.services.billing import stripe_client

    def missing(pid):
        raise stripe.InvalidRequestError("No such product", "id", code="resource_missing")

    monkeypatch.setattr(stripe.Product, "retrieve", missing)
    assert stripe_client.retrieve_product("prod_x") is None
