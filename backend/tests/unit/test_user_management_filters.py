"""Admin user-management list filters + auth-source (PRO-57).

Adds search / role / status / SSO-vs-local filtering to GET /users and surfaces
`auth_source` on the user response so the admin portal (PRO-58) can drive its
searchable/filterable table.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient

from app.models.user import Role, User, UserRole
from tests.conftest import TestSessionLocal, auth_header

PFX = "pro57x-"  # unique marker so super-admin listing is deterministic via ?q=


async def _seed(email: str, *, active: bool, role: Role, oid: str | None) -> uuid.UUID:
    uid = uuid.uuid4()
    async with TestSessionLocal() as session:
        session.add(
            User(
                id=uid,
                email=email,
                hashed_password=None if oid else "x",
                full_name=email.split("@")[0],
                entra_object_id=oid,
                is_active=active,
                is_test_data=True,
            )
        )
        await session.flush()
        session.add(UserRole(id=uuid.uuid4(), user_id=uid, role=role))
        await session.commit()
    return uid


@pytest_asyncio.fixture
async def seeded() -> dict[str, uuid.UUID]:
    return {
        "alice": await _seed(
            f"{PFX}alice@corp.example", active=True, role=Role.ANALYST, oid="oid-a"
        ),
        "bob": await _seed(f"{PFX}bob@corp.example", active=True, role=Role.VIEWER, oid=None),
        "carol": await _seed(f"{PFX}carol@corp.example", active=False, role=Role.ANALYST, oid=None),
    }


def _emails(payload: dict) -> set[str]:
    return {u["email"] for u in payload["users"]}


async def test_search_q_matches_email_or_name(
    client: AsyncClient, super_admin_token: str, seeded
) -> None:
    resp = await client.get(
        "/api/v1/users", params={"q": f"{PFX}alice"}, headers=auth_header(super_admin_token)
    )
    assert resp.status_code == 200
    assert _emails(resp.json()) == {f"{PFX}alice@corp.example"}


async def test_filter_is_active_false(client: AsyncClient, super_admin_token: str, seeded) -> None:
    resp = await client.get(
        "/api/v1/users",
        params={"q": PFX, "is_active": "false"},
        headers=auth_header(super_admin_token),
    )
    assert _emails(resp.json()) == {f"{PFX}carol@corp.example"}


async def test_filter_by_role(client: AsyncClient, super_admin_token: str, seeded) -> None:
    resp = await client.get(
        "/api/v1/users",
        params={"q": PFX, "role": "VIEWER"},
        headers=auth_header(super_admin_token),
    )
    assert _emails(resp.json()) == {f"{PFX}bob@corp.example"}


async def test_filter_by_source(client: AsyncClient, super_admin_token: str, seeded) -> None:
    sso = await client.get(
        "/api/v1/users",
        params={"q": PFX, "source": "sso"},
        headers=auth_header(super_admin_token),
    )
    assert _emails(sso.json()) == {f"{PFX}alice@corp.example"}

    local = await client.get(
        "/api/v1/users",
        params={"q": PFX, "source": "local"},
        headers=auth_header(super_admin_token),
    )
    assert _emails(local.json()) == {
        f"{PFX}bob@corp.example",
        f"{PFX}carol@corp.example",
    }


async def test_auth_source_field_present(
    client: AsyncClient, super_admin_token: str, seeded
) -> None:
    resp = await client.get(
        "/api/v1/users", params={"q": f"{PFX}alice"}, headers=auth_header(super_admin_token)
    )
    user = resp.json()["users"][0]
    assert user["auth_source"] == "sso"
