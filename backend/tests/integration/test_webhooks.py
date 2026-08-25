"""Integration tests for Stripe webhook receiver.

Tests cover:
- Each of the 5 event handlers (correct tenant mutation)
- Idempotency: duplicate event_id → only one DB row, second call is 200 no-op
- Bad signature → 400
- Unknown event type → 200 acknowledge no-op
- Tenant routing via client_reference_id
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.billing import StripeWebhookEvent
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import User
from tests.conftest import TestSessionLocal

# ---------------------------------------------------------------------------
# Helpers: event fixtures
# ---------------------------------------------------------------------------


def _checkout_completed_event(tenant_id, event_id: str = "evt_1"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(tenant_id),
                "metadata": {"tenant_id": str(tenant_id)},
                "customer": "cus_1",
                "subscription": "sub_1",
            }
        },
    }


def _subscription_updated_event(
    customer_id: str, status: str, quantity: int, event_id: str = "evt_sub_upd"
):
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "customer": customer_id,
                "status": status,
                "items": {"data": [{"id": "si_x", "quantity": quantity}]},
            }
        },
    }


def _subscription_deleted_event(customer_id: str, event_id: str = "evt_sub_del"):
    return {
        "id": event_id,
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": customer_id,
                "status": "canceled",
                "items": {"data": []},
            }
        },
    }


def _invoice_payment_failed_event(customer_id: str, event_id: str = "evt_inv_fail"):
    return {
        "id": event_id,
        "type": "invoice.payment_failed",
        "data": {"object": {"customer": customer_id}},
    }


def _invoice_paid_event(customer_id: str, event_id: str = "evt_inv_paid"):
    return {
        "id": event_id,
        "type": "invoice.paid",
        "data": {"object": {"customer": customer_id}},
    }


def _unknown_event(event_id: str = "evt_unknown"):
    return {
        "id": event_id,
        "type": "some.unknown.event",
        "data": {"object": {}},
    }


# ---------------------------------------------------------------------------
# Helpers: DB seeding
# ---------------------------------------------------------------------------


async def _seed_trialing(*, customer_id: str | None = None) -> uuid.UUID:
    """Insert a trialing tenant; return its id."""
    tid = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tid,
                name="Trialing Co",
                slug=f"trialing-{tid.hex[:8]}",
                is_active=True,
                plan=Plan.FREE,
                subscription_status=SubscriptionStatus.TRIALING,
                trial_ends_at=datetime.now(UTC) + timedelta(days=5),
                stripe_customer_id=customer_id,
            )
        )
        await db.commit()
    return tid


async def _seed_active(*, customer_id: str = "cus_active") -> uuid.UUID:
    """Insert an active (paying) tenant; return its id."""
    tid = uuid.uuid4()
    async with TestSessionLocal() as db:
        db.add(
            Tenant(
                id=tid,
                name="Active Co",
                slug=f"active-{tid.hex[:8]}",
                is_active=True,
                plan=Plan.TEAM,
                subscription_status=SubscriptionStatus.ACTIVE,
                stripe_customer_id=customer_id,
                stripe_subscription_id="sub_active",
                stripe_subscription_item_id="si_active",
            )
        )
        await db.commit()
    return tid


async def _seed_activated_users(tenant_id: uuid.UUID, count: int) -> None:
    """Insert *count* activated users (first_login_at set) for tenant_id."""
    from datetime import datetime

    async with TestSessionLocal() as db:
        for i in range(count):
            db.add(
                User(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    email=f"user{i}-{tenant_id.hex[:6]}@example.com",
                    hashed_password="x",
                    full_name=f"User {i}",
                    is_active=True,
                    first_login_at=datetime.now(UTC),
                )
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Fixtures for construct_event mock + subscription-item stub
# ---------------------------------------------------------------------------


def _patch_both(monkeypatch, event: dict):
    """Patch construct_event to return *event* and stub _fetch_subscription_item_id."""
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: event,
    )
    monkeypatch.setattr(
        "app.services.billing.webhooks._fetch_subscription_item_id",
        lambda sub_id: "si_1",
    )


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_checkout_completed_activates_tenant(client, monkeypatch):
    """checkout.session.completed sets plan=team, stripe IDs, status=active, seats_billed>=5."""
    tid = await _seed_trialing()
    ev = _checkout_completed_event(tid, event_id="evt_co_1")
    _patch_both(monkeypatch, ev)

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.plan == Plan.TEAM
        assert t.stripe_customer_id == "cus_1"
        assert t.stripe_subscription_id == "sub_1"
        assert t.stripe_subscription_item_id == "si_1"
        assert t.subscription_status == SubscriptionStatus.ACTIVE
        # Spec §140: seats_billed = max(5, activated_users) — no activated users → floor of 5
        assert t.seats_billed == 5


@pytest.mark.asyncio
async def test_checkout_completed_routes_via_client_reference_id(client, monkeypatch):
    """Tenant routing uses client_reference_id (metadata.tenant_id is fallback)."""
    tid = await _seed_trialing()
    # Event has client_reference_id set, metadata.tenant_id blank — should still work
    ev = {
        "id": "evt_co_ref",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": str(tid),
                "metadata": {},
                "customer": "cus_ref",
                "subscription": "sub_ref",
            }
        },
    }
    _patch_both(monkeypatch, ev)

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.plan == Plan.TEAM
        assert t.stripe_customer_id == "cus_ref"


@pytest.mark.asyncio
async def test_checkout_completed_seeds_seats_billed_above_floor(client, monkeypatch):
    """seats_billed tracks activated users when count > 5 (no clamping to floor)."""
    tid = await _seed_trialing()
    await _seed_activated_users(tid, count=7)
    ev = _checkout_completed_event(tid, event_id="evt_co_seats")
    _patch_both(monkeypatch, ev)

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.seats_billed == 7  # max(5, 7) == 7


@pytest.mark.asyncio
async def test_duplicate_event_is_noop(client, monkeypatch):
    """Sending the same event_id twice → only 1 row in stripe_webhook_events, both 200."""
    tid = await _seed_trialing()
    ev = _checkout_completed_event(tid, event_id="evt_dup")
    _patch_both(monkeypatch, ev)

    r1 = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    r2 = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    async with TestSessionLocal() as db:
        n = await db.scalar(select(func.count(StripeWebhookEvent.stripe_event_id)))
        assert n == 1  # Only one row persisted


@pytest.mark.asyncio
async def test_duplicate_event_does_not_mutate_twice(client, monkeypatch):
    """Second identical checkout event must not overwrite already-set stripe IDs."""
    tid = await _seed_trialing()
    ev = _checkout_completed_event(tid, event_id="evt_no_double_mut")
    _patch_both(monkeypatch, ev)

    await client.post("/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "x"})

    # Manually update stripe_customer_id to something different to verify it doesn't revert
    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        t.stripe_customer_id = "cus_overridden"
        await db.commit()

    # Second replay with same event_id should be a no-op
    await client.post("/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "x"})

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.stripe_customer_id == "cus_overridden"  # not reverted back to "cus_1"


@pytest.mark.asyncio
async def test_bad_signature_returns_400(client, monkeypatch):
    """construct_event raising ValueError → 400 (Stripe stops retrying)."""

    def boom(payload, sig):
        raise ValueError("bad sig")

    monkeypatch.setattr("app.services.billing.stripe_client.construct_event", boom)

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_event_type_returns_200(client, monkeypatch):
    """Unknown event type → 200 acknowledge no-op (not an error)."""
    ev = _unknown_event(event_id="evt_unk_1")
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    # Should still be logged in stripe_webhook_events
    async with TestSessionLocal() as db:
        row = await db.scalar(
            select(StripeWebhookEvent).where(StripeWebhookEvent.stripe_event_id == "evt_unk_1")
        )
        assert row is not None
        assert row.event_type == "some.unknown.event"


@pytest.mark.asyncio
async def test_subscription_updated_changes_status_and_quantity(client, monkeypatch):
    """customer.subscription.updated → updates subscription_status + seats_billed."""
    tid = await _seed_active(customer_id="cus_upd")
    ev = _subscription_updated_event(
        customer_id="cus_upd",
        status="past_due",
        quantity=8,
        event_id="evt_sub_upd_1",
    )
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.PAST_DUE
        assert t.seats_billed == 8


@pytest.mark.asyncio
async def test_subscription_updated_active_status(client, monkeypatch):
    """customer.subscription.updated with status=active → ACTIVE."""
    tid = await _seed_active(customer_id="cus_upd_act")
    ev = _subscription_updated_event(
        customer_id="cus_upd_act",
        status="active",
        quantity=5,
        event_id="evt_sub_upd_act",
    )
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_subscription_deleted_sets_canceled(client, monkeypatch):
    """customer.subscription.deleted → subscription_status=canceled."""
    tid = await _seed_active(customer_id="cus_del")
    ev = _subscription_deleted_event(customer_id="cus_del", event_id="evt_sub_del_1")
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.CANCELED


@pytest.mark.asyncio
async def test_invoice_payment_failed_sets_past_due(client, monkeypatch):
    """invoice.payment_failed → subscription_status=past_due."""
    tid = await _seed_active(customer_id="cus_fail")
    ev = _invoice_payment_failed_event(customer_id="cus_fail", event_id="evt_inv_fail_1")
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.PAST_DUE


@pytest.mark.asyncio
async def test_invoice_paid_sets_active(client, monkeypatch):
    """invoice.paid → subscription_status=active."""
    tid = await _seed_active(customer_id="cus_paid")
    ev = _invoice_paid_event(customer_id="cus_paid", event_id="evt_inv_paid_1")
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )
    # Manually set tenant to past_due first
    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        t.subscription_status = SubscriptionStatus.PAST_DUE
        await db.commit()

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_webhook_event_logged_to_stripe_webhook_events(client, monkeypatch):
    """Each processed event is written to stripe_webhook_events."""
    await _seed_active(customer_id="cus_log")  # seeds the tenant; id unused here
    ev = _invoice_paid_event(customer_id="cus_log", event_id="evt_log_1")
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: ev,
    )

    await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )

    async with TestSessionLocal() as db:
        row = await db.scalar(
            select(StripeWebhookEvent).where(StripeWebhookEvent.stripe_event_id == "evt_log_1")
        )
        assert row is not None
        assert row.event_type == "invoice.paid"
