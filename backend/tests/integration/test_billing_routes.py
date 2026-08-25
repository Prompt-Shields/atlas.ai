"""Integration tests for billing routes.

Tests:
  - checkout-session: email-unverified → 403
  - checkout-session: trial_period_days omitted when trial already expired (remaining < 1)
  - checkout-session: trial_period_days included when trial days remain (remaining >= 1)
  - checkout-session: client_reference_id + metadata present in Stripe call args
  - checkout-session: quantity = max(5, activated_users)
  - portal-session: requires stripe_customer_id
  - entitlements: shape + hard_locked=True when trial expired without subscription
  - entitlements: hard_locked=False when subscription present
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.auth.jwt import create_access_token
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import Role, User, UserRole
from tests.conftest import TestSessionLocal, auth_header

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_tenant_admin(
    *,
    email_verified: bool,
    trial_delta_days: float = 7,
    sub_id: str | None = None,
    stripe_customer_id: str | None = None,
    activated_users: int = 0,
) -> tuple[uuid.UUID, str]:
    """Create a tenant + admin user; return (tenant_id, JWT token)."""
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    email = f"admin-{uid.hex[:8]}@acme.test"

    async with TestSessionLocal() as db:
        trial_ends = datetime.now(UTC) + timedelta(days=trial_delta_days)
        t = Tenant(
            id=tid,
            name="Acme",
            slug=f"acme-{tid.hex[:8]}",
            is_active=True,
            plan=Plan.FREE,
            subscription_status=SubscriptionStatus.TRIALING,
            trial_ends_at=trial_ends,
            stripe_subscription_id=sub_id,
            stripe_customer_id=stripe_customer_id,
        )
        db.add(t)
        u = User(
            id=uid,
            email=email,
            hashed_password="x",
            full_name="Admin User",
            tenant_id=tid,
            is_email_verified=email_verified,
            is_active=True,
        )
        db.add(u)
        await db.flush()
        db.add(UserRole(user_id=uid, role=Role.TENANT_ADMIN, tenant_id=tid))

        # Add extra activated users (first_login_at set)
        for _ in range(activated_users):
            extra_uid = uuid.uuid4()
            extra = User(
                id=extra_uid,
                email=f"user-{extra_uid.hex[:8]}@acme.test",
                hashed_password="x",
                full_name="Team Member",
                tenant_id=tid,
                is_email_verified=True,
                is_active=True,
                first_login_at=datetime.now(UTC),
            )
            db.add(extra)

        await db.commit()

    token = create_access_token(
        user_id=uid,
        email=email,
        roles=["TENANT_ADMIN"],
        tenant_id=tid,
        org_id=None,
    )
    return tid, token


# ---------------------------------------------------------------------------
# checkout-session tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_requires_verified_email(client):
    """Unverified email → 403 with email_not_verified code."""
    _, token = await _seed_tenant_admin(email_verified=False)
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "monthly"},
        headers=auth_header(token),
    )
    assert resp.status_code == 403
    body = resp.json()
    # AppException wraps in {"error": {"code": ...}}
    assert body.get("error", {}).get("code") == "email_not_verified"


@pytest.mark.asyncio
async def test_checkout_omits_trial_period_when_expired(client, monkeypatch):
    """When trial is expired (remaining < 1 day), trial_period_days must be absent."""
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return type("S", (), {"url": "https://checkout.stripe.test/abc"})()

    monkeypatch.setattr("app.services.billing.stripe_client.create_checkout_session", fake_create)

    # trial_delta_days=-1 → remaining < 0 → omit
    _, token = await _seed_tenant_admin(email_verified=True, trial_delta_days=-1)
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "monthly"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"url": "https://checkout.stripe.test/abc"}

    # trial_period_days must NOT appear anywhere in the Stripe call
    subscription_data = captured.get("subscription_data", {})
    assert "trial_period_days" not in subscription_data


@pytest.mark.asyncio
async def test_checkout_includes_trial_period_when_days_remain(client, monkeypatch):
    """When trial has ≥1 day remaining, trial_period_days is forwarded."""
    captured = {}

    monkeypatch.setattr(
        "app.services.billing.stripe_client.create_checkout_session",
        lambda **kw: captured.update(kw) or type("S", (), {"url": "u"})(),
    )

    _, token = await _seed_tenant_admin(email_verified=True, trial_delta_days=7)
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "monthly"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert captured["subscription_data"]["trial_period_days"] >= 1


@pytest.mark.asyncio
async def test_checkout_client_reference_id_and_metadata(client, monkeypatch):
    """client_reference_id and metadata.tenant_id must be the tenant UUID."""
    captured = {}

    monkeypatch.setattr(
        "app.services.billing.stripe_client.create_checkout_session",
        lambda **kw: captured.update(kw) or type("S", (), {"url": "u"})(),
    )

    tid, token = await _seed_tenant_admin(email_verified=True, trial_delta_days=5)
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "annual"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert captured["client_reference_id"] == str(tid)
    assert captured["metadata"]["tenant_id"] == str(tid)


@pytest.mark.asyncio
async def test_checkout_quantity_uses_max5_or_activated(client, monkeypatch):
    """quantity = max(5, activated_users) — both branches."""
    captured_calls: list[dict] = []

    def fake_create(**kwargs):
        captured_calls.append(dict(kwargs))
        return type("S", (), {"url": "u"})()

    monkeypatch.setattr("app.services.billing.stripe_client.create_checkout_session", fake_create)

    # 2 activated users → quantity should be max(5, 2) = 5
    _, token_few = await _seed_tenant_admin(
        email_verified=True, trial_delta_days=5, activated_users=2
    )
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "monthly"},
        headers=auth_header(token_few),
    )
    assert resp.status_code == 200
    assert captured_calls[-1]["line_items"][0]["quantity"] == 5

    # 8 activated users → quantity should be max(5, 8) = 8
    _, token_many = await _seed_tenant_admin(
        email_verified=True, trial_delta_days=5, activated_users=8
    )
    resp = await client.post(
        "/api/v1/billing/checkout-session",
        json={"interval": "monthly"},
        headers=auth_header(token_many),
    )
    assert resp.status_code == 200
    assert captured_calls[-1]["line_items"][0]["quantity"] == 8


# ---------------------------------------------------------------------------
# portal-session tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_requires_stripe_customer_id(client, monkeypatch):
    """Tenant without stripe_customer_id → 409 / no_customer."""
    monkeypatch.setattr(
        "app.services.billing.stripe_client.create_portal_session",
        lambda **kw: type("S", (), {"url": "u"})(),
    )

    _, token = await _seed_tenant_admin(email_verified=True, stripe_customer_id=None)
    resp = await client.post(
        "/api/v1/billing/portal-session",
        headers=auth_header(token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_customer"


@pytest.mark.asyncio
async def test_portal_returns_url_when_customer_present(client, monkeypatch):
    """Tenant with stripe_customer_id → 200 with url."""
    monkeypatch.setattr(
        "app.services.billing.stripe_client.create_portal_session",
        lambda **kw: type("S", (), {"url": "https://portal.stripe.test/xyz"})(),
    )

    _, token = await _seed_tenant_admin(email_verified=True, stripe_customer_id="cus_abc123")
    resp = await client.post(
        "/api/v1/billing/portal-session",
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"url": "https://portal.stripe.test/xyz"}


# ---------------------------------------------------------------------------
# entitlements tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entitlements_hard_locked_when_trial_expired(client):
    """Expired trial, no subscription → hard_locked=True."""
    _, token = await _seed_tenant_admin(email_verified=True, trial_delta_days=-1, sub_id=None)
    resp = await client.get("/api/v1/billing/entitlements", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["hard_locked"] is True


@pytest.mark.asyncio
async def test_entitlements_not_hard_locked_when_subscription_present(client):
    """Expired trial but subscription attached → hard_locked=False."""
    _, token = await _seed_tenant_admin(
        email_verified=True,
        trial_delta_days=-1,
        sub_id="sub_abc123",
    )
    resp = await client.get("/api/v1/billing/entitlements", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["hard_locked"] is False


@pytest.mark.asyncio
async def test_entitlements_shape(client):
    """Response shape: all required keys present with correct types."""
    _, token = await _seed_tenant_admin(email_verified=True, trial_delta_days=5, activated_users=3)
    resp = await client.get("/api/v1/billing/entitlements", headers=auth_header(token))
    assert resp.status_code == 200
    body = resp.json()

    # Required keys
    required_keys = {
        "plan",
        "subscription_status",
        "trial_ends_at",
        "seats_used",
        "seats_billed",
        "capabilities",
        "hard_locked",
    }
    assert required_keys.issubset(body.keys())

    assert isinstance(body["plan"], str)
    assert isinstance(body["capabilities"], list)
    assert isinstance(body["hard_locked"], bool)
    assert isinstance(body["seats_used"], int)
    assert isinstance(body["seats_billed"], int)
    # 3 activated_users seeded (not counting the admin, whose first_login_at is None)
    assert body["seats_used"] == 3
