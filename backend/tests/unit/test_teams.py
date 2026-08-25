"""Admin teams endpoints (PRO-57): read APIs over synced Entra groups + members.

GET /teams              — list the tenant's directory groups (teams)
GET /teams/{id}/members — list a team's members

Role-gated to admins, tenant-scoped. Manual (non-Entra) teams are deferred.
Uses random uuid4 tenants + a custom token (this branch predates the
all-digit-UUID SQLite test fix on main).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest_asyncio
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.models.directory import (
    DirectoryGroup,
    DirectoryGroupMembership,
    DirectoryUser,
)
from app.models.user import Role
from tests.conftest import TestSessionLocal, auth_header


def _admin_token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        email="teamadmin@test.local",
        roles=[Role.TENANT_ADMIN.value],
        tenant_id=tenant_id,
        org_id=uuid.uuid4(),
    )


def _viewer_token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        email="teamviewer@test.local",
        roles=[Role.VIEWER.value],
        tenant_id=tenant_id,
        org_id=uuid.uuid4(),
    )


@pytest_asyncio.fixture
async def team_ctx() -> dict:
    """Seed a tenant with 2 groups (one with 2 members) + a foreign group."""
    tenant = uuid.uuid4()
    other = uuid.uuid4()
    integ = uuid.uuid4()
    now = datetime.now(UTC)
    g1, g2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    async with TestSessionLocal() as s:
        s.add_all(
            [
                DirectoryGroup(
                    id=g1,
                    tenant_id=tenant,
                    integration_id=integ,
                    external_group_id="grp-1",
                    display_name="Engineering",
                    description="Eng team",
                    group_type="Security",
                    member_count=2,
                    last_synced_at=now,
                    is_test_data=True,
                ),
                DirectoryGroup(
                    id=g2,
                    tenant_id=tenant,
                    integration_id=integ,
                    external_group_id="grp-2",
                    display_name="Finance",
                    member_count=0,
                    last_synced_at=now,
                    is_test_data=True,
                ),
                DirectoryGroup(
                    id=uuid.uuid4(),
                    tenant_id=other,
                    integration_id=integ,
                    external_group_id="grp-x",
                    display_name="OtherTenantTeam",
                    last_synced_at=now,
                    is_test_data=True,
                ),
            ]
        )
        for uid, ext, name in ((u1, "u1", "Ada"), (u2, "u2", "Bob")):
            s.add(
                DirectoryUser(
                    id=uid,
                    tenant_id=tenant,
                    integration_id=integ,
                    external_user_id=ext,
                    display_name=name,
                    email=f"{ext}@corp.example",
                    last_synced_at=now,
                    is_test_data=True,
                )
            )
        await s.flush()
        for uid in (u1, u2):
            s.add(
                DirectoryGroupMembership(
                    tenant_id=tenant,
                    integration_id=integ,
                    group_id=g1,
                    user_id=uid,
                    last_synced_at=now,
                    is_test_data=True,
                )
            )
        await s.commit()
    return {"tenant": tenant, "g1": g1, "g2": g2}


async def test_list_teams_is_tenant_scoped(client: AsyncClient, team_ctx) -> None:
    resp = await client.get("/api/v1/teams", headers=auth_header(_admin_token(team_ctx["tenant"])))
    assert resp.status_code == 200, resp.text
    names = {t["display_name"] for t in resp.json()["teams"]}
    assert names == {"Engineering", "Finance"}  # foreign tenant's team excluded


async def test_list_teams_forbidden_for_viewer(client: AsyncClient, team_ctx) -> None:
    resp = await client.get("/api/v1/teams", headers=auth_header(_viewer_token(team_ctx["tenant"])))
    assert resp.status_code == 403


async def test_list_team_members(client: AsyncClient, team_ctx) -> None:
    resp = await client.get(
        f"/api/v1/teams/{team_ctx['g1']}/members",
        headers=auth_header(_admin_token(team_ctx["tenant"])),
    )
    assert resp.status_code == 200, resp.text
    emails = {m["email"] for m in resp.json()["members"]}
    assert emails == {"u1@corp.example", "u2@corp.example"}


async def test_members_unknown_team_404(client: AsyncClient, team_ctx) -> None:
    resp = await client.get(
        f"/api/v1/teams/{uuid.uuid4()}/members",
        headers=auth_header(_admin_token(team_ctx["tenant"])),
    )
    assert resp.status_code == 404
