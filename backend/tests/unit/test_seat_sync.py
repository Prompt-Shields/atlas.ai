"""Unit tests for services/billing/seat_sync.py.

Tests:
  - no-op when stripe_subscription_item_id is None
  - no-op when subscription_status is CANCELED
  - quantity floor of 5 applied (activated < 5)
  - quantity > 5 used when activated > 5 (happy path)
  - transient Stripe errors (APIConnectionError, RateLimitError, APIError) → outbox enqueued
  - InvalidRequestError → log/alert only, NOT enqueued, no raise
  - on_first_login swallows any unexpected error (login-never-500 resilience)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe

from app.models.tenant import SubscriptionStatus, Tenant
from app.services.billing import seat_sync


def _tenant(**kw):
    t = Tenant(name="t", slug="t")
    for k, v in kw.items():
        setattr(t, k, v)
    t.id = uuid.uuid4()
    return t


# ---------------------------------------------------------------------------
# Pre-Stripe guard — no-op cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_when_no_subscription_item(monkeypatch):
    """No Stripe call when stripe_subscription_item_id is None."""
    called = MagicMock()
    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", called)
    t = _tenant(stripe_subscription_item_id=None, subscription_status=SubscriptionStatus.TRIALING)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=3)
    called.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_canceled(monkeypatch):
    """No Stripe call when subscription_status is CANCELED."""
    called = MagicMock()
    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", called)
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.CANCELED)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=10)
    called.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_status_none(monkeypatch):
    """No Stripe call when subscription_status is None (no subscription yet)."""
    called = MagicMock()
    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", called)
    t = _tenant(stripe_subscription_item_id=None, subscription_status=None)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=3)
    called.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path — quantity floor and pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_floor_of_five(monkeypatch):
    """activated_users < 5 → quantity is floored to 5."""
    captured = {}
    monkeypatch.setattr(
        "app.services.billing.stripe_client.modify_subscription_item",
        lambda item_id, **kw: captured.update(item_id=item_id, **kw),
    )
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    await seat_sync.sync_seat_count(db, t, activated_users=2)
    assert captured["quantity"] == 5
    assert captured["proration_behavior"] == "create_prorations"
    assert captured["item_id"] == "si_1"


@pytest.mark.asyncio
async def test_above_floor_uses_actual_count(monkeypatch):
    """activated_users > 5 → quantity is the actual count."""
    captured = {}
    monkeypatch.setattr(
        "app.services.billing.stripe_client.modify_subscription_item",
        lambda item_id, **kw: captured.update(item_id=item_id, **kw),
    )
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    await seat_sync.sync_seat_count(db, t, activated_users=9)
    assert captured["quantity"] == 9


# ---------------------------------------------------------------------------
# Exception routing — transient → outbox, InvalidRequestError → alert only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_error_enqueues_outbox(monkeypatch):
    """APIConnectionError → outbox.enqueue called once, no raise."""

    def raise_conn(item_id, **kw):
        raise stripe.error.APIConnectionError("down")

    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", raise_conn)
    enq = AsyncMock()
    monkeypatch.setattr("app.services.billing.outbox.enqueue", enq)
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=7)
    enq.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_error_enqueues_outbox(monkeypatch):
    """RateLimitError → outbox.enqueue called once, no raise."""

    def raise_rate(item_id, **kw):
        raise stripe.error.RateLimitError("rate")

    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", raise_rate)
    enq = AsyncMock()
    monkeypatch.setattr("app.services.billing.outbox.enqueue", enq)
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=7)
    enq.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_error_enqueues_outbox(monkeypatch):
    """Generic APIError → outbox.enqueue called once, no raise."""

    def raise_api(item_id, **kw):
        raise stripe.error.APIError("internal")

    monkeypatch.setattr("app.services.billing.stripe_client.modify_subscription_item", raise_api)
    enq = AsyncMock()
    monkeypatch.setattr("app.services.billing.outbox.enqueue", enq)
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=7)
    enq.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_request_alerts_not_enqueues(monkeypatch):
    """InvalidRequestError → log only, NOT enqueued, no raise."""

    def raise_invalid(item_id, **kw):
        raise stripe.error.InvalidRequestError("bad", param="x")

    monkeypatch.setattr(
        "app.services.billing.stripe_client.modify_subscription_item", raise_invalid
    )
    enq = AsyncMock()
    monkeypatch.setattr("app.services.billing.outbox.enqueue", enq)
    t = _tenant(stripe_subscription_item_id="si_1", subscription_status=SubscriptionStatus.ACTIVE)
    await seat_sync.sync_seat_count(AsyncMock(), t, activated_users=7)  # must NOT raise
    enq.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_first_login resilience — login must never 500 due to seat-sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_first_login_swallows_unexpected_exception(monkeypatch):
    """Any unexpected error inside on_first_login must be swallowed (login-never-500)."""

    async def boom(db, tenant, *, activated_users):
        raise RuntimeError("unexpected DB explosion")

    monkeypatch.setattr("app.services.billing.seat_sync.sync_seat_count", boom)

    from app.models.user import User

    u = User(email="x@x.com", hashed_password="h", full_name="X")
    u.id = uuid.uuid4()
    u.tenant_id = uuid.uuid4()

    # DB mock that returns a valid Tenant and a user count
    tenant_obj = _tenant(
        stripe_subscription_item_id=None,
        subscription_status=SubscriptionStatus.TRIALING,
    )
    tenant_obj.id = u.tenant_id

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[tenant_obj, 1])

    result = await seat_sync.on_first_login(db, u)
    assert result is None  # must return None, not raise
