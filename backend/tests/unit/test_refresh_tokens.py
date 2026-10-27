"""Refresh tokens: issuing, rotation, and the cases that must be refused.

These tests pin the behaviour of `auth_service.issue_tokens`,
`auth_service.refresh_tokens`, the `/auth/refresh` route and the Redis token
store behind them. Unit tests run without Redis, so the store's client is
swapped for a small in-memory stand-in with the same semantics for the
commands the store uses.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from jose import jwt

from app.auth import token_store
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import get_settings
from app.errors import UnauthorizedError
from app.models.user import Role, User, UserRole
from app.services import auth_service
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TENANT_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
USER_A = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
USER_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")

REFRESH_URL = "/api/v1/auth/refresh"


# ─── In-memory Redis stand-in ────────────────────────────────────────


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def queue(*args, **kwargs) -> _FakePipeline:
            self._ops.append((name, args, kwargs))
            return self

        return queue

    async def execute(self) -> list:
        return [getattr(self._redis, f"_{n}")(*a, **k) for n, a, k in self._ops]


class _FakeRedis:
    """The subset of `redis.asyncio.Redis` the token store calls.

    `get` reads the value when the command is sent and replies after
    yielding to the event loop, as a network round trip does, so two
    concurrent callers can both read a key before either deletes it.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    # Synchronous primitives, shared by direct calls and pipelines.
    def _set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def _sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    def _expire(self, key: str, seconds: int) -> bool:
        if key in self.values or key in self.sets:
            self.ttls[key] = seconds
            return True
        return False

    def _delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.values.pop(key, None) is not None or self.sets.pop(key, None) is not None:
                removed += 1
            self.ttls.pop(key, None)
        return removed

    # Async client API.
    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        await asyncio.sleep(0)
        return value

    async def getdel(self, key: str) -> str | None:
        value = self.values.pop(key, None)
        self.ttls.pop(key, None)
        await asyncio.sleep(0)
        return value

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        return self._set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        return self._delete(*keys)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(token_store, "_redis_client", fake)
    return fake


# ─── Helpers ─────────────────────────────────────────────────────────


async def _seed_user(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    email: str,
    role: Role = Role.ANALYST,
    is_active: bool = True,
) -> None:
    async with TestSessionLocal() as session:
        await ensure_tenant(session, tenant_id, name=f"Tenant {tenant_id.hex[-2:]}")
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=email,
                full_name=email.split("@")[0],
                hashed_password="placeholder",
                is_active=is_active,
                is_email_verified=True,
                is_test_data=True,
                # Already logged in once, so issuing skips the first-login hook.
                first_login_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(UserRole(user_id=user_id, role=role, tenant_id=tenant_id))
        await session.commit()


@pytest_asyncio.fixture
async def two_tenants() -> None:
    await _seed_user(USER_A, TENANT_A, email="alice@tenant-a.example")
    await _seed_user(USER_B, TENANT_B, email="bob@tenant-b.example", role=Role.VIEWER)


async def _issue(user_id: uuid.UUID) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with TestSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.id == user_id).options(selectinload(User.roles))
            )
        ).scalar_one()
        tokens = await auth_service.issue_tokens(session, user)
        await session.commit()
        return tokens


async def _refresh(refresh_token: str) -> dict:
    async with TestSessionLocal() as session:
        return await auth_service.refresh_tokens(session, refresh_token)


def _jti_key(token: str) -> str:
    return f"rt_jti:{decode_token(token)['jti']}"


def _signed(payload: dict, *, key: str | None = None) -> str:
    settings = get_settings()
    return jwt.encode(payload, key or settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# ─── Issuing ─────────────────────────────────────────────────────────


async def test_issue_returns_a_bearer_pair(two_tenants, fake_redis: _FakeRedis) -> None:
    settings = get_settings()
    tokens = await _issue(USER_A)

    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == settings.jwt_access_token_expire_minutes * 60

    access = decode_token(tokens["access_token"])
    refresh = decode_token(tokens["refresh_token"])
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"
    assert access["sub"] == refresh["sub"] == str(USER_A)
    assert access["tenant_id"] == str(TENANT_A)
    # The refresh token carries no tenant, email or roles: those come from the
    # database each time it is used.
    assert {"tenant_id", "email", "roles"}.isdisjoint(refresh)


async def test_issue_records_the_jti_for_the_refresh_lifetime(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    settings = get_settings()
    tokens = await _issue(USER_A)
    jti = decode_token(tokens["refresh_token"])["jti"]
    lifetime = settings.jwt_refresh_token_expire_days * 86400

    assert fake_redis.values[f"rt_jti:{jti}"] == str(USER_A)
    assert fake_redis.ttls[f"rt_jti:{jti}"] == lifetime
    assert fake_redis.sets[f"rt_family:{USER_A}"] == {jti}
    assert fake_redis.ttls[f"rt_family:{USER_A}"] == lifetime

    refresh = decode_token(tokens["refresh_token"])
    assert refresh["exp"] - refresh["iat"] == lifetime


async def test_each_issue_gets_its_own_jti(two_tenants, fake_redis: _FakeRedis) -> None:
    first = await _issue(USER_A)
    second = await _issue(USER_A)
    jtis = {decode_token(t["refresh_token"])["jti"] for t in (first, second)}

    assert len(jtis) == 2
    assert fake_redis.sets[f"rt_family:{USER_A}"] == jtis


# ─── Rotation ────────────────────────────────────────────────────────


async def test_refresh_rotates_to_a_new_pair(two_tenants, fake_redis: _FakeRedis) -> None:
    old = await _issue(USER_A)
    new = await _refresh(old["refresh_token"])

    assert new["refresh_token"] != old["refresh_token"]
    old_jti = decode_token(old["refresh_token"])["jti"]
    new_jti = decode_token(new["refresh_token"])["jti"]
    assert new_jti != old_jti

    # The old jti is consumed, the new one is live, and both stay in the family.
    assert f"rt_jti:{old_jti}" not in fake_redis.values
    assert fake_redis.values[f"rt_jti:{new_jti}"] == str(USER_A)
    assert fake_redis.sets[f"rt_family:{USER_A}"] == {old_jti, new_jti}

    access = decode_token(new["access_token"])
    assert access["type"] == "access"
    assert access["sub"] == str(USER_A)
    assert access["tenant_id"] == str(TENANT_A)
    assert access["roles"] == [Role.ANALYST.value]


async def test_the_rotated_token_can_be_used_in_turn(two_tenants) -> None:
    first = await _issue(USER_A)
    second = await _refresh(first["refresh_token"])
    third = await _refresh(second["refresh_token"])

    assert decode_token(third["refresh_token"])["sub"] == str(USER_A)


async def test_refresh_endpoint_rotates(two_tenants, client: AsyncClient) -> None:
    old = await _issue(USER_A)
    resp = await client.post(REFRESH_URL, json={"refresh_token": old["refresh_token"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["refresh_token"] != old["refresh_token"]
    assert decode_token(body["access_token"])["sub"] == str(USER_A)


# ─── Reuse ───────────────────────────────────────────────────────────


async def test_reused_token_is_refused(two_tenants) -> None:
    old = await _issue(USER_A)
    await _refresh(old["refresh_token"])

    with pytest.raises(UnauthorizedError, match="reuse detected"):
        await _refresh(old["refresh_token"])


async def test_reuse_revokes_the_whole_family(two_tenants, fake_redis: _FakeRedis) -> None:
    old = await _issue(USER_A)
    other_session = await _issue(USER_A)
    rotated = await _refresh(old["refresh_token"])

    with pytest.raises(UnauthorizedError):
        await _refresh(old["refresh_token"])

    # Every token the user holds is now dead, including the legitimately
    # rotated one and a second session's token.
    assert f"rt_family:{USER_A}" not in fake_redis.sets
    for tokens in (rotated, other_session):
        assert _jti_key(tokens["refresh_token"]) not in fake_redis.values
        with pytest.raises(UnauthorizedError, match="reuse detected"):
            await _refresh(tokens["refresh_token"])


async def test_reuse_by_one_user_leaves_another_users_tokens_alone(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    alice = await _issue(USER_A)
    bob = await _issue(USER_B)
    await _refresh(alice["refresh_token"])

    with pytest.raises(UnauthorizedError):
        await _refresh(alice["refresh_token"])

    assert _jti_key(bob["refresh_token"]) in fake_redis.values
    refreshed = await _refresh(bob["refresh_token"])
    assert decode_token(refreshed["access_token"])["sub"] == str(USER_B)


async def test_refresh_endpoint_refuses_reuse_with_401(two_tenants, client: AsyncClient) -> None:
    old = await _issue(USER_A)
    first = await client.post(REFRESH_URL, json={"refresh_token": old["refresh_token"]})
    assert first.status_code == 200

    again = await client.post(REFRESH_URL, json={"refresh_token": old["refresh_token"]})
    assert again.status_code == 401


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known open item waiting on the owner: atomic consumption of a refresh "
        "token under concurrent use. consume_refresh_token reads the jti with "
        "GET and deletes it in a second command, so two concurrent uses of one "
        "token can both succeed."
    ),
)
async def test_concurrent_use_of_one_token_lets_only_one_through() -> None:
    jti = str(uuid.uuid4())
    await token_store.store_refresh_token(USER_A, jti, 60)

    results = await asyncio.gather(
        token_store.consume_refresh_token(jti, USER_A),
        token_store.consume_refresh_token(jti, USER_A),
    )

    assert sorted(results) == [False, True]


# ─── Expiry ──────────────────────────────────────────────────────────


async def test_expired_token_is_refused_without_touching_the_store(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    live = await _issue(USER_A)
    now = datetime.now(UTC)
    expired = _signed(
        {
            "sub": str(USER_A),
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iat": now - timedelta(days=8),
            "exp": now - timedelta(seconds=1),
        }
    )

    with pytest.raises(UnauthorizedError, match="Invalid refresh token"):
        await _refresh(expired)

    # A token refused at signature/expiry time is not treated as a replay.
    assert _jti_key(live["refresh_token"]) in fake_redis.values


async def test_token_whose_jti_lapsed_in_the_store_is_refused(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    tokens = await _issue(USER_A)
    # The store's TTL ran out before the token was used.
    fake_redis._delete(_jti_key(tokens["refresh_token"]))

    with pytest.raises(UnauthorizedError, match="reuse detected"):
        await _refresh(tokens["refresh_token"])


async def test_refresh_endpoint_refuses_expired_token_with_401(
    two_tenants, client: AsyncClient
) -> None:
    now = datetime.now(UTC)
    expired = _signed(
        {
            "sub": str(USER_A),
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iat": now - timedelta(days=8),
            "exp": now - timedelta(seconds=1),
        }
    )
    resp = await client.post(REFRESH_URL, json={"refresh_token": expired})
    assert resp.status_code == 401


# ─── Revocation ──────────────────────────────────────────────────────


async def test_token_revoked_by_logout_everywhere_is_refused(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    first = await _issue(USER_A)
    second = await _issue(USER_A)

    assert await token_store.revoke_all_for_user(USER_A) == 2
    assert f"rt_family:{USER_A}" not in fake_redis.sets

    for tokens in (first, second):
        with pytest.raises(UnauthorizedError):
            await _refresh(tokens["refresh_token"])


async def test_revoking_a_user_with_no_tokens_is_a_no_op(fake_redis: _FakeRedis) -> None:
    assert await token_store.revoke_all_for_user(USER_A) == 0


async def test_logout_endpoint_revokes_refresh_tokens(two_tenants, client: AsyncClient) -> None:
    tokens = await _issue(USER_A)
    resp = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 204, resp.text

    again = await client.post(REFRESH_URL, json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


async def test_deactivated_user_is_refused(two_tenants) -> None:
    from sqlalchemy import update

    tokens = await _issue(USER_A)
    async with TestSessionLocal() as session:
        await session.execute(update(User).where(User.id == USER_A).values(is_active=False))
        await session.commit()

    with pytest.raises(UnauthorizedError, match="not found or inactive"):
        await _refresh(tokens["refresh_token"])


# ─── Wrong kind of token ─────────────────────────────────────────────


async def test_access_token_is_refused_as_a_refresh_token(two_tenants) -> None:
    tokens = await _issue(USER_A)

    with pytest.raises(UnauthorizedError, match="Invalid refresh token"):
        await _refresh(tokens["access_token"])


async def test_token_without_jti_is_refused(two_tenants, fake_redis: _FakeRedis) -> None:
    live = await _issue(USER_A)
    now = datetime.now(UTC)
    no_jti = _signed(
        {"sub": str(USER_A), "type": "refresh", "iat": now, "exp": now + timedelta(days=1)}
    )

    with pytest.raises(UnauthorizedError, match="Malformed"):
        await _refresh(no_jti)

    assert _jti_key(live["refresh_token"]) in fake_redis.values


async def test_garbage_is_refused(client: AsyncClient) -> None:
    resp = await client.post(REFRESH_URL, json={"refresh_token": "not.a.token"})
    assert resp.status_code == 401


# ─── Another user or tenant ──────────────────────────────────────────


async def test_token_signed_with_another_key_is_refused(
    two_tenants, fake_redis: _FakeRedis
) -> None:
    bob = await _issue(USER_B)
    bob_jti = decode_token(bob["refresh_token"])["jti"]
    now = datetime.now(UTC)
    # Someone takes Bob's jti and claims to be Bob, but cannot sign as us.
    forged = _signed(
        {
            "sub": str(USER_B),
            "type": "refresh",
            "jti": bob_jti,
            "iat": now,
            "exp": now + timedelta(days=1),
        },
        key="a-different-signing-key-of-sufficient-length",
    )

    with pytest.raises(UnauthorizedError, match="Invalid refresh token"):
        await _refresh(forged)

    # Bob's real token is untouched.
    assert fake_redis.values[f"rt_jti:{bob_jti}"] == str(USER_B)


async def test_refresh_returns_only_the_token_owners_identity(two_tenants) -> None:
    alice = await _issue(USER_A)
    bob = await _issue(USER_B)

    for tokens, user_id, tenant_id, role in (
        (alice, USER_A, TENANT_A, Role.ANALYST),
        (bob, USER_B, TENANT_B, Role.VIEWER),
    ):
        new = await _refresh(tokens["refresh_token"])
        access = decode_token(new["access_token"])
        assert access["sub"] == str(user_id)
        assert access["tenant_id"] == str(tenant_id)
        assert access["roles"] == [role.value]
        assert decode_token(new["refresh_token"])["sub"] == str(user_id)


async def test_refresh_takes_the_tenant_from_the_database_not_the_old_token(
    two_tenants,
) -> None:
    from sqlalchemy import update

    tokens = await _issue(USER_A)
    assert decode_token(tokens["access_token"])["tenant_id"] == str(TENANT_A)

    async with TestSessionLocal() as session:
        await session.execute(update(User).where(User.id == USER_A).values(tenant_id=TENANT_B))
        await session.commit()

    new = await _refresh(tokens["refresh_token"])
    assert decode_token(new["access_token"])["tenant_id"] == str(TENANT_B)


async def test_refresh_token_for_an_unknown_user_is_refused(two_tenants) -> None:
    stranger = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
    token = create_refresh_token(user_id=stranger)
    await token_store.store_refresh_token(stranger, decode_token(token)["jti"], 60)

    with pytest.raises(UnauthorizedError, match="not found or inactive"):
        await _refresh(token)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "consume_refresh_token does not check that the stored owner of a jti "
        "matches the user it is called with. Not reachable through "
        "/auth/refresh, where sub and jti come from the same signed token."
    ),
)
async def test_store_refuses_a_jti_presented_for_another_user(
    fake_redis: _FakeRedis,
) -> None:
    alice_jti = str(uuid.uuid4())
    await token_store.store_refresh_token(USER_A, alice_jti, 60)

    assert await token_store.consume_refresh_token(alice_jti, USER_B) is False


async def test_access_token_minted_for_another_user_does_not_refresh(two_tenants) -> None:
    # An access token, even a validly signed one for Bob, is never a refresh token.
    access = create_access_token(
        user_id=USER_B, email="bob@tenant-b.example", roles=["VIEWER"], tenant_id=TENANT_B
    )
    with pytest.raises(UnauthorizedError, match="Invalid refresh token"):
        await _refresh(access)
