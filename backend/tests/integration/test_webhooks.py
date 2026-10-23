"""Integration tests for Stripe webhook receiver.

Tests cover:
- Each of the 5 event handlers (correct tenant mutation)
- Idempotency: duplicate event_id → only one DB row, second call is 200 no-op
- Bad signature → 400
- Unknown event type → 200 acknowledge no-op
- Tenant routing via client_reference_id
- checkout.session.completed never overwrites an existing stripe_customer_id
- Module entitlements from subscription items' price lookup_keys
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.billing import StripeWebhookEvent
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import User
from app.services.billing import webhooks as wh
from app.services.entitlements import Module, modules
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
async def test_checkout_completed_keeps_existing_customer(client, monkeypatch):
    """A tenant with a customer keeps it, and later events for it still route."""
    tid = await _seed_trialing(customer_id="cus_enterprise")
    ev = _checkout_completed_event(tid, event_id="evt_co_keep")  # carries cus_1
    _patch_both(monkeypatch, ev)

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.stripe_customer_id == "cus_enterprise"
        assert t.plan == Plan.TEAM

    # An invoice for the original customer still reaches the tenant.
    _patch_both(monkeypatch, _invoice_payment_failed_event("cus_enterprise", "evt_inv_keep"))
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


# ===========================================================================
# Module subscriptions and module entitlements
# ===========================================================================

_ENTERPRISE = {"promptly_subscription": "enterprise"}  # Work client subscription
_QUOTE = {"billing_kind": "enterprise_modules"}  # created by an accepted Quote


def _item(lookup_key: str, quantity: int | None = 10) -> dict:
    return {
        "id": f"si_{lookup_key}",
        "quantity": quantity,
        "price": {"id": f"price_{lookup_key}", "lookup_key": lookup_key},
    }


def _sub_event(
    event_type: str,
    *,
    sub_id: str,
    customer_id: str,
    items: list[dict],
    metadata: dict,
    event_id: str,
    status: str = "active",
    created: int | None = None,
):
    return {
        "id": event_id,
        "type": event_type,
        **({"created": created} if created is not None else {}),
        "data": {
            "object": {
                "id": sub_id,
                "customer": customer_id,
                "status": status,
                "metadata": metadata,
                "items": {"data": items},
            }
        },
    }


def _module_invoice_event(
    event_type: str,
    *,
    customer_id: str,
    sub_id: str,
    event_id: str,
    created: int | None = None,
):
    # Current API shape: the subscription sits under parent.subscription_details.
    return {
        "id": event_id,
        "type": event_type,
        **({"created": created} if created is not None else {}),
        "data": {
            "object": {
                "customer": customer_id,
                "parent": {
                    "type": "subscription_details",
                    "subscription_details": {"subscription": sub_id, "metadata": {}},
                },
            }
        },
    }


async def _post(client, monkeypatch, event: dict) -> None:
    monkeypatch.setattr(
        "app.services.billing.stripe_client.construct_event",
        lambda payload, sig: event,
    )
    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=abc"},
    )
    assert resp.status_code == 200


async def _load(tid: uuid.UUID) -> Tenant:
    async with TestSessionLocal() as db:
        return await db.scalar(select(Tenant).where(Tenant.id == tid))


async def _seed_enterprise(client, monkeypatch, customer_id: str) -> uuid.UUID:
    """A Team tenant whose customer also holds the Work client with Atlas.AI."""
    tid = await _seed_active(customer_id=customer_id)
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.created",
            sub_id=f"sub_ent_{customer_id}",
            customer_id=customer_id,
            items=[_item("promptly_work_client", 200), _item("promptly_atlas_annual", 50)],
            metadata=_ENTERPRISE,
            event_id=f"evt_ent_{customer_id}",
        ),
    )
    return tid


@pytest.mark.asyncio
async def test_work_client_subscription_switches_modules_on(client, monkeypatch):
    tid = await _seed_enterprise(client, monkeypatch, "cus_mod_on")
    t = await _load(tid)
    assert modules(t) == {Module.WORK_CLIENT, Module.ATLAS}
    assert t.stripe_subscriptions["sub_ent_cus_mod_on"] == {
        "kind": "enterprise",
        "status": "active",
        "items": {"promptly_work_client": 200, "promptly_atlas_annual": 50},
    }


@pytest.mark.asyncio
async def test_module_subscription_leaves_team_columns_alone(client, monkeypatch):
    """Updates to a module subscription never touch the Team status or seats."""
    tid = await _seed_enterprise(client, monkeypatch, "cus_team_safe")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_team_safe",
            customer_id="cus_team_safe",
            status="past_due",
            items=[_item("promptly_work_client", 999)],
            metadata=_ENTERPRISE,
            event_id="evt_team_safe_upd",
        ),
    )
    t = await _load(tid)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.seats_billed == 1  # Tenant default; not the Work client's 999
    assert t.stripe_subscription_id == "sub_active"
    assert t.plan == Plan.TEAM


@pytest.mark.asyncio
async def test_module_subscription_deleted_leaves_team_status_alone(client, monkeypatch):
    """Deleting a quote's module subscription switches its modules off, nothing else."""
    tid = await _seed_enterprise(client, monkeypatch, "cus_quote_del")
    quote_sub = _sub_event(
        "customer.subscription.created",
        sub_id="sub_quote",
        customer_id="cus_quote_del",
        items=[_item("promptly_learn_annual", 50), _item("promptly_platform_fee_annual", 1)],
        metadata={**_QUOTE, "modules": "learn"},
        event_id="evt_quote_created",
    )
    await _post(client, monkeypatch, quote_sub)
    assert modules(await _load(tid)) == {Module.WORK_CLIENT, Module.ATLAS, Module.LEARN}

    deleted = _sub_event(
        "customer.subscription.deleted",
        sub_id="sub_quote",
        customer_id="cus_quote_del",
        status="canceled",
        items=[_item("promptly_learn_annual", 50)],
        metadata={**_QUOTE, "modules": "learn"},
        event_id="evt_quote_deleted",
    )
    await _post(client, monkeypatch, deleted)

    t = await _load(tid)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.plan == Plan.TEAM
    assert t.plan_changed_at is None
    # Learn came from the quote and is gone; the Work client subscription stays.
    assert modules(t) == {Module.WORK_CLIENT, Module.ATLAS}
    assert t.stripe_subscriptions["sub_quote"]["status"] == "canceled"


@pytest.mark.asyncio
async def test_unmarked_subscription_with_catalogue_prices_is_a_module_subscription(
    client, monkeypatch
):
    """No marker, but Promptly prices: still kept away from the Team status."""
    tid = await _seed_active(customer_id="cus_unmarked")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.deleted",
            sub_id="sub_overage",
            customer_id="cus_unmarked",
            status="canceled",
            items=[_item("promptly_step_overage", None)],
            metadata={},
            event_id="evt_unmarked_del",
        ),
    )
    t = await _load(tid)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.stripe_subscriptions["sub_overage"]["kind"] == "unmarked"


@pytest.mark.asyncio
async def test_items_added_and_removed_switch_modules(client, monkeypatch):
    tid = await _seed_enterprise(client, monkeypatch, "cus_items")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_items",
            customer_id="cus_items",
            items=[_item("promptly_work_client", 200), _item("promptly_suite_annual", 50)],
            metadata=_ENTERPRISE,
            event_id="evt_items_suite",
        ),
    )
    assert modules(await _load(tid)) == {
        Module.WORK_CLIENT,
        Module.ATLAS,
        Module.LEARN,
        Module.ADOPT,
    }

    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_items",
            customer_id="cus_items",
            items=[_item("promptly_work_client", 200), _item("promptly_adopt_annual", 50)],
            metadata=_ENTERPRISE,
            event_id="evt_items_adopt",
        ),
    )
    assert modules(await _load(tid)) == {Module.WORK_CLIENT, Module.ADOPT}


@pytest.mark.asyncio
async def test_module_invoice_failed_then_paid(client, monkeypatch):
    """A failed module invoice keeps only the Work client; payment restores the rest.

    The Team subscription's status is untouched throughout.
    """
    tid = await _seed_enterprise(client, monkeypatch, "cus_inv")
    await _post(
        client,
        monkeypatch,
        _module_invoice_event(
            "invoice.payment_failed",
            customer_id="cus_inv",
            sub_id="sub_ent_cus_inv",
            event_id="evt_mod_inv_fail",
        ),
    )
    t = await _load(tid)
    assert modules(t) == {Module.WORK_CLIENT}
    assert t.subscription_status == SubscriptionStatus.ACTIVE

    await _post(
        client,
        monkeypatch,
        _module_invoice_event(
            "invoice.paid",
            customer_id="cus_inv",
            sub_id="sub_ent_cus_inv",
            event_id="evt_mod_inv_paid",
        ),
    )
    t = await _load(tid)
    assert modules(t) == {Module.WORK_CLIENT, Module.ATLAS}
    assert t.subscription_status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_unpaid_module_subscription_keeps_only_work_client(client, monkeypatch):
    tid = await _seed_enterprise(client, monkeypatch, "cus_unpaid")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_unpaid",
            customer_id="cus_unpaid",
            status="unpaid",
            items=[_item("promptly_work_client", 200), _item("promptly_atlas_annual", 50)],
            metadata=_ENTERPRISE,
            event_id="evt_unpaid_upd",
        ),
    )
    assert modules(await _load(tid)) == {Module.WORK_CLIENT}


@pytest.mark.asyncio
async def test_late_invoice_paid_does_not_revive_cancelled_modules(client, monkeypatch):
    tid = await _seed_enterprise(client, monkeypatch, "cus_late")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.deleted",
            sub_id="sub_ent_cus_late",
            customer_id="cus_late",
            status="canceled",
            items=[_item("promptly_work_client", 200), _item("promptly_atlas_annual", 50)],
            metadata=_ENTERPRISE,
            event_id="evt_late_del",
        ),
    )
    assert modules(await _load(tid)) == set()

    await _post(
        client,
        monkeypatch,
        _module_invoice_event(
            "invoice.paid",
            customer_id="cus_late",
            sub_id="sub_ent_cus_late",
            event_id="evt_late_paid",
        ),
    )
    t = await _load(tid)
    assert modules(t) == set()
    assert t.subscription_status == SubscriptionStatus.ACTIVE  # Team plan untouched


@pytest.mark.asyncio
async def test_module_subscription_found_by_metadata_tenant_id(client, monkeypatch):
    """A tenant with no Stripe customer yet is found by metadata.tenant_id."""
    tid = await _seed_trialing()
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.created",
            sub_id="sub_ent_new",
            customer_id="cus_ent_new",
            items=[_item("promptly_work_client", 300)],
            metadata={**_ENTERPRISE, "tenant_id": str(tid)},
            event_id="evt_ent_new",
        ),
    )
    t = await _load(tid)
    assert t.stripe_customer_id == "cus_ent_new"
    assert t.stripe_subscription_id is None  # The Team column stays empty
    assert t.subscription_status == SubscriptionStatus.TRIALING
    assert modules(t) == {Module.WORK_CLIENT}


@pytest.mark.asyncio
async def test_metadata_never_repoints_a_tenant_to_another_customer(client, monkeypatch):
    tid = await _seed_active(customer_id="cus_owner")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.created",
            sub_id="sub_other",
            customer_id="cus_other",
            items=[_item("promptly_suite_annual")],
            metadata={**_ENTERPRISE, "tenant_id": str(tid)},
            event_id="evt_repoint",
        ),
    )
    t = await _load(tid)
    assert t.stripe_customer_id == "cus_owner"
    assert t.stripe_subscriptions == {}


@pytest.mark.asyncio
async def test_team_subscription_created_is_ignored(client, monkeypatch):
    """checkout.session.completed handles the Team subscription, not .created."""
    tid = await _seed_active(customer_id="cus_team_created")
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.created",
            sub_id="sub_team_new",
            customer_id="cus_team_created",
            status="past_due",
            items=[{"id": "si_team", "quantity": 9, "price": {"id": "price_team"}}],
            metadata={},
            event_id="evt_team_created",
        ),
    )
    t = await _load(tid)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.seats_billed == 1
    assert t.stripe_subscriptions == {}


@pytest.mark.asyncio
async def test_replayed_module_event_does_not_revert_items(client, monkeypatch):
    """A replayed event id is a no-op, so it cannot undo a later item change."""
    tid = await _seed_enterprise(client, monkeypatch, "cus_replay")
    first = _sub_event(
        "customer.subscription.updated",
        sub_id="sub_ent_cus_replay",
        customer_id="cus_replay",
        items=[_item("promptly_work_client"), _item("promptly_learn_annual")],
        metadata=_ENTERPRISE,
        event_id="evt_replay_1",
    )
    await _post(client, monkeypatch, first)
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_replay",
            customer_id="cus_replay",
            items=[_item("promptly_work_client")],
            metadata=_ENTERPRISE,
            event_id="evt_replay_2",
        ),
    )
    await _post(client, monkeypatch, first)  # Stripe retries the first delivery

    assert modules(await _load(tid)) == {Module.WORK_CLIENT}


# ---------------------------------------------------------------------------
# Ordering and concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_older_subscription_update_arriving_late_is_ignored(client, monkeypatch):
    """Stripe does not deliver in order: an older update must not restore removed items."""
    tid = await _seed_enterprise(client, monkeypatch, "cus_order")
    newer = _sub_event(
        "customer.subscription.updated",
        sub_id="sub_ent_cus_order",
        customer_id="cus_order",
        items=[_item("promptly_work_client", 200)],
        metadata=_ENTERPRISE,
        event_id="evt_order_new",
        created=2_000,
    )
    older = _sub_event(
        "customer.subscription.updated",
        sub_id="sub_ent_cus_order",
        customer_id="cus_order",
        items=[_item("promptly_work_client", 200), _item("promptly_suite_annual", 50)],
        metadata=_ENTERPRISE,
        event_id="evt_order_old",
        created=1_000,
    )
    await _post(client, monkeypatch, newer)
    await _post(client, monkeypatch, older)

    t = await _load(tid)
    assert modules(t) == {Module.WORK_CLIENT}
    assert t.stripe_subscriptions["sub_ent_cus_order"]["event_created"] == 2_000


@pytest.mark.asyncio
async def test_older_invoice_paid_after_payment_failed_is_ignored(client, monkeypatch):
    tid = await _seed_enterprise(client, monkeypatch, "cus_inv_order")
    for event_type, event_id, created in [
        ("invoice.payment_failed", "evt_io_fail", 2_000),
        ("invoice.paid", "evt_io_paid_old", 1_000),
    ]:
        await _post(
            client,
            monkeypatch,
            _module_invoice_event(
                event_type,
                customer_id="cus_inv_order",
                sub_id="sub_ent_cus_inv_order",
                event_id=event_id,
                created=created,
            ),
        )
    assert modules(await _load(tid)) == {Module.WORK_CLIENT}


@pytest.mark.asyncio
async def test_nothing_revives_a_cancelled_module_subscription(client, monkeypatch):
    """After deletion, a late update or a failed invoice must not grant anything.

    A failed invoice would set past_due, which keeps the free Work client.
    """
    tid = await _seed_enterprise(client, monkeypatch, "cus_dead")
    items = [_item("promptly_work_client", 200), _item("promptly_atlas_annual", 50)]
    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.deleted",
            sub_id="sub_ent_cus_dead",
            customer_id="cus_dead",
            status="canceled",
            items=items,
            metadata=_ENTERPRISE,
            event_id="evt_dead_del",
            created=1_000,
        ),
    )
    await _post(
        client,
        monkeypatch,
        _module_invoice_event(
            "invoice.payment_failed",
            customer_id="cus_dead",
            sub_id="sub_ent_cus_dead",
            event_id="evt_dead_fail",
            created=1_100,
        ),
    )
    assert modules(await _load(tid)) == set()

    await _post(
        client,
        monkeypatch,
        _sub_event(
            "customer.subscription.updated",
            sub_id="sub_ent_cus_dead",
            customer_id="cus_dead",
            status="active",
            items=items,
            metadata=_ENTERPRISE,
            event_id="evt_dead_upd",
            created=1_200,
        ),
    )
    t = await _load(tid)
    assert modules(t) == set()
    assert t.stripe_subscriptions["sub_ent_cus_dead"]["status"] == "canceled"


@pytest.mark.asyncio
async def test_invoice_for_a_not_yet_known_subscription_leaves_team_alone(client, monkeypatch):
    """An unmarked module invoice that beats its subscription.created event."""
    tid = await _seed_active(customer_id="cus_early_inv")
    await _post(
        client,
        monkeypatch,
        _module_invoice_event(
            "invoice.payment_failed",
            customer_id="cus_early_inv",
            sub_id="sub_overage_new",
            event_id="evt_early_inv",
        ),
    )
    t = await _load(tid)
    assert t.subscription_status == SubscriptionStatus.ACTIVE
    assert t.stripe_subscriptions == {}


@pytest.mark.asyncio
async def test_concurrent_module_events_do_not_lose_an_entry(client, monkeypatch):
    """Two events for one tenant in flight at once both land in stripe_subscriptions.

    Without the row lock, both transactions read the same JSON and the later
    commit drops the other's entry.
    """
    tid = await _seed_active(customer_id="cus_race")
    ent = _sub_event(
        "customer.subscription.created",
        sub_id="sub_race_ent",
        customer_id="cus_race",
        items=[_item("promptly_work_client", 200)],
        metadata=_ENTERPRISE,
        event_id="evt_race_ent",
    )
    quote = _sub_event(
        "customer.subscription.created",
        sub_id="sub_race_quote",
        customer_id="cus_race",
        items=[_item("promptly_learn_annual", 50)],
        metadata=_QUOTE,
        event_id="evt_race_quote",
    )

    async def second() -> None:
        async with TestSessionLocal() as db:
            await wh.process_event(db, quote)
            await db.commit()

    async with TestSessionLocal() as first:
        await wh.process_event(first, ent)
        task = asyncio.create_task(second())
        await asyncio.sleep(0.5)
        assert not task.done(), "second event should wait for the tenant row lock"
        await first.commit()
    await asyncio.wait_for(task, timeout=10)

    t = await _load(tid)
    assert set(t.stripe_subscriptions) == {"sub_race_ent", "sub_race_quote"}
    assert modules(t) == {Module.WORK_CLIENT, Module.LEARN}


@pytest.mark.asyncio
async def test_real_signed_event_through_the_sdk(client, monkeypatch):
    """The route with the real stripe.Webhook.construct_event, not a stub.

    Since stripe-python 15 the SDK's Event is not a dict, so this is the test that
    sees the route and handlers working on what the SDK actually returns.
    """
    import hashlib
    import hmac
    import json
    import time

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "whsec_route")
    tid = await _seed_active(customer_id="cus_real_sdk")
    payload = json.dumps(
        {
            "id": "evt_real_sdk_1",
            "object": "event",
            "type": "customer.subscription.updated",
            "created": int(time.time()),
            "data": {
                "object": {
                    "id": "sub_real",
                    "object": "subscription",
                    "customer": "cus_real_sdk",
                    "status": "past_due",
                    "metadata": {},
                    "items": {
                        "object": "list",
                        "data": [{"id": "si_real", "object": "subscription_item", "quantity": 9}],
                    },
                }
            },
        }
    )
    ts = int(time.time())
    sig = hmac.new(b"whsec_route", f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()

    resp = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload.encode(),
        headers={"Stripe-Signature": f"t={ts},v1={sig}"},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as db:
        t = await db.scalar(select(Tenant).where(Tenant.id == tid))
        assert t.subscription_status == SubscriptionStatus.PAST_DUE
        assert t.seats_billed == 9
