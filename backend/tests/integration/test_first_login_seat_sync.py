"""Integration tests for first-login hook in auth_service.login().

Phase 2 scope: verifies first_login_at is stamped on the first login
and is idempotent (unchanged on subsequent logins).
Phase 4 extension: login survives a Stripe APIConnectionError in seat_sync —
tokens are still returned AND a billing_outbox row is created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import stripe
from sqlalchemy import select

from app.models.billing import BillingOutbox
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import Role, User, UserRole
from app.services.auth_service import create_user, login
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_first_login_stamps_first_login_at():
    async with TestSessionLocal() as db:
        await create_user(
            db,
            email="u1@x.com",
            password="Str0ng!Passw0rd",
            full_name="U",
            role=Role.TENANT_ADMIN,
        )
        await db.commit()

    async with TestSessionLocal() as db:
        await login(db, email="u1@x.com", password="Str0ng!Passw0rd")
        await db.commit()

    async with TestSessionLocal() as db:
        row = (await db.execute(select(User).where(User.email == "u1@x.com"))).scalar_one()
        assert row.first_login_at is not None
        first = row.first_login_at

    async with TestSessionLocal() as db:
        await login(db, email="u1@x.com", password="Str0ng!Passw0rd")
        await db.commit()

    async with TestSessionLocal() as db:
        row = (await db.execute(select(User).where(User.email == "u1@x.com"))).scalar_one()
        assert row.first_login_at == first  # idempotent — second login doesn't overwrite


@pytest.mark.asyncio
async def test_login_succeeds_when_stripe_raises_api_connection_error(monkeypatch):
    """First login returns tokens AND enqueues an outbox row when Stripe is down.

    Mirrors the spec's resilience test: tenant has an active subscription so
    seat_sync tries to call Stripe; Stripe raises APIConnectionError; login
    still succeeds (200 + tokens) and a billing_outbox row is created.
    """
    # Seed: a tenant with an active Stripe subscription item.
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    email = f"user-{uid.hex[:8]}@acme.test"
    password = "Str0ng!Passw0rd"

    async with TestSessionLocal() as db:
        tenant = Tenant(
            id=tid,
            name="Acme",
            slug=f"acme-{tid.hex[:8]}",
            is_active=True,
            plan=Plan.TEAM,
            subscription_status=SubscriptionStatus.ACTIVE,
            stripe_subscription_item_id="si_test_seat_sync",
            stripe_subscription_id="sub_test",
            stripe_customer_id="cus_test",
            trial_ends_at=datetime.now(UTC),
        )
        db.add(tenant)
        user = User(
            id=uid,
            email=email,
            hashed_password=__import__(
                "app.auth.passwords", fromlist=["hash_password"]
            ).hash_password(password),
            full_name="Test User",
            tenant_id=tid,
            is_email_verified=True,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=uid, role=Role.TENANT_ADMIN, tenant_id=tid))
        await db.commit()

    # Monkeypatch Stripe to raise a transient error.
    monkeypatch.setattr(
        "app.services.billing.stripe_client.modify_subscription_item",
        lambda *a, **kw: (_ for _ in ()).throw(stripe.error.APIConnectionError("down")),
    )

    # Perform login — must return tokens without raising.
    async with TestSessionLocal() as db:
        result = await login(db, email=email, password=password)
        await db.commit()

    assert "access_token" in result
    assert "refresh_token" in result
    assert result["token_type"] == "bearer"

    # A billing_outbox row must have been enqueued.
    async with TestSessionLocal() as db:
        rows = (await db.execute(select(BillingOutbox))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "sync_seat"
    assert row.tenant_id == tid
