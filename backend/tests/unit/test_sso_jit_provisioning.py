"""SP-Azure — self-serve Entra ID sign-up (JIT tenant provisioning).

Covers `signup_service.provision_tenant` / `provision_or_join_sso_user` and the
`complete_sso_login` self-serve branch:

* JIT creates a tenant + 14-day trial and makes the first user TENANT_ADMIN.
* A second user from the same Azure `tid` joins the existing tenant as a member.
* Flag off ⇒ provisioned-only (unknown identity rejected).
* An existing platform email links (never duplicates / never a new tenant).
"""

from __future__ import annotations

import base64
import json
import types
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.models.user import Role, User, UserRole
from app.services import signup_service, sso_login
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.asyncio

CLIENT_ID = "00000000-aaaa-bbbb-cccc-clientid000001"
TID = "azure-dir-tid-001"
ISS = f"https://login.microsoftonline.com/{TID}/v2.0"


# ─── helpers ─────────────────────────────────────────────────────────


def _settings(*, self_serve: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        microsoft_client_id=CLIENT_ID,
        microsoft_client_secret="secret",
        microsoft_signing_secret="s3cret",
        microsoft_sso_redirect_url="http://localhost:8000/api/v1/auth/sso/microsoft/callback",
        sso_self_serve_signup=self_serve,
    )


def _make_id_token(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


def _claims(**over) -> dict:
    base = {
        "iss": ISS,
        "aud": CLIENT_ID,
        "tid": TID,
        "oid": "entra-oid-new",
        "email": "first@corp.example",
        "name": "First Person",
        "nonce": "n-1",
        "exp": 9999999999,
    }
    base.update(over)
    return base


def _token_client(id_token: str) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "ms-access",
                "id_token": id_token,
                "expires_in": 3600,
                "scope": "openid profile email",
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _async_noop(*_args, **_kwargs) -> None:
    return None


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# ─── provision_tenant (unit) ─────────────────────────────────────────


async def test_provision_tenant_creates_trialing_tenant_and_admin() -> None:
    async with TestSessionLocal() as session:
        tenant, admin = await signup_service.provision_tenant(
            session,
            tenant_name="Corp",
            tenant_slug="corp",
            admin_email="admin@corp.example",
            admin_full_name="Admin",
            admin_password=None,
            azure_tenant_id=TID,
            admin_is_email_verified=True,
        )
        await session.commit()

    async with TestSessionLocal() as session:
        t = (await session.execute(select(Tenant).where(Tenant.id == tenant.id))).scalar_one()
        assert t.plan == Plan.FREE
        assert t.subscription_status == SubscriptionStatus.TRIALING
        assert t.trial_ends_at is not None
        assert t.azure_tenant_id == TID
        u = (await session.execute(select(User).where(User.id == admin.id))).scalar_one()
        assert u.hashed_password is None  # SSO-only admin
        assert u.is_email_verified is True
        roles = (
            (await session.execute(select(UserRole.role).where(UserRole.user_id == admin.id)))
            .scalars()
            .all()
        )
        assert Role.TENANT_ADMIN in roles


# ─── JIT provision-or-join ───────────────────────────────────────────


async def test_jit_first_user_provisions_tenant_admin() -> None:
    async with TestSessionLocal() as session:
        user = await signup_service.provision_or_join_sso_user(
            session,
            azure_tenant_id=TID,
            email="first@corp.example",
            entra_object_id="oid-1",
            full_name="First",
        )
        await session.commit()
        uid = user.id

    async with TestSessionLocal() as session:
        assert await _count(session, Tenant) == 1
        u = (await session.execute(select(User).where(User.id == uid))).scalar_one()
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == u.tenant_id))
        ).scalar_one()
        assert tenant.azure_tenant_id == TID
        assert tenant.subscription_status == SubscriptionStatus.TRIALING
        assert u.entra_object_id == "oid-1"
        roles = (
            (await session.execute(select(UserRole.role).where(UserRole.user_id == uid)))
            .scalars()
            .all()
        )
        assert roles == [Role.TENANT_ADMIN]


async def test_jit_second_user_same_tid_joins_as_member() -> None:
    async with TestSessionLocal() as session:
        await signup_service.provision_or_join_sso_user(
            session,
            azure_tenant_id=TID,
            email="first@corp.example",
            entra_object_id="oid-1",
            full_name="First",
        )
        await session.commit()

    async with TestSessionLocal() as session:
        second = await signup_service.provision_or_join_sso_user(
            session,
            azure_tenant_id=TID,
            email="second@corp.example",
            entra_object_id="oid-2",
            full_name="Second",
        )
        await session.commit()
        second_id = second.id

    async with TestSessionLocal() as session:
        # Still exactly one tenant — the second user joined, not provisioned.
        assert await _count(session, Tenant) == 1
        assert await _count(session, User) == 2
        u = (await session.execute(select(User).where(User.id == second_id))).scalar_one()
        roles = (
            (await session.execute(select(UserRole.role).where(UserRole.user_id == second_id)))
            .scalars()
            .all()
        )
        assert roles == [signup_service.JIT_MEMBER_ROLE]
        assert u.entra_object_id == "oid-2"


# ─── complete_sso_login integration ──────────────────────────────────


async def test_complete_sso_login_flag_off_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-1")
    id_token = _make_id_token(_claims())
    async with TestSessionLocal() as session:
        async with _token_client(id_token) as client:
            with pytest.raises(sso_login.SSOAccountNotProvisionedError):
                await sso_login.complete_sso_login(
                    session,
                    code="c",
                    state=state,
                    settings=_settings(self_serve=False),
                    client=client,
                    now_ts=1000,
                )
        # Nothing provisioned.
        assert await _count(session, Tenant) == 0


async def test_complete_sso_login_flag_on_jit_provisions_and_issues(monkeypatch) -> None:
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-1")
    id_token = _make_id_token(_claims())
    async with TestSessionLocal() as session:
        async with _token_client(id_token) as client:
            result = await sso_login.complete_sso_login(
                session,
                code="c",
                state=state,
                settings=_settings(self_serve=True),
                client=client,
                now_ts=1000,
            )
        await session.commit()

    assert result["token_type"] == "bearer"
    assert result["access_token"] and result["refresh_token"]

    async with TestSessionLocal() as session:
        assert await _count(session, Tenant) == 1
        tenant = (await session.execute(select(Tenant))).scalars().one()
        assert tenant.azure_tenant_id == TID


async def test_complete_sso_login_existing_email_links_no_new_tenant(monkeypatch) -> None:
    """Self-serve on, but an active platform user already has this email:
    resolve_sso_user links (backfills oid) — no JIT, no new tenant, no dup."""
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    existing_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        session.add(
            User(
                id=existing_id,
                email="first@corp.example",
                hashed_password=None,
                full_name="Existing",
                entra_object_id=None,
                is_active=True,
                is_test_data=True,
            )
        )
        await session.flush()
        session.add(UserRole(id=uuid.uuid4(), user_id=existing_id, role=Role.ANALYST))
        await session.commit()

    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-1")
    id_token = _make_id_token(_claims(oid="oid-fresh", email="first@corp.example"))
    async with TestSessionLocal() as session:
        async with _token_client(id_token) as client:
            result = await sso_login.complete_sso_login(
                session,
                code="c",
                state=state,
                settings=_settings(self_serve=True),
                client=client,
                now_ts=1000,
            )
        await session.commit()

    from app.auth.jwt import decode_token

    assert decode_token(result["access_token"])["sub"] == str(existing_id)
    async with TestSessionLocal() as session:
        assert await _count(session, Tenant) == 0  # linked, not provisioned
        assert await _count(session, User) == 1  # no duplicate
        u = (await session.execute(select(User).where(User.id == existing_id))).scalar_one()
        assert u.entra_object_id == "oid-fresh"  # backfilled
