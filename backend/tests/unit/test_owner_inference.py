"""Unit tests for GET /api/v1/use-cases/owner-suggestions (#244).

Covers:
  - Empty tenant → empty suggestions.
  - Owned use cases are excluded; unowned ones get ranked candidates.
  - Directory-matched business candidates require a linked atlas User.
  - No-directory tenants fall back to active tenant Users.
  - IT owner candidates come from ORG_ADMIN+/TENANT_ADMIN roles.
  - Read is Viewer+ (no write access needed); tenant isolation holds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.models.directory import DirectoryUser
from app.models.integration import Integration, IntegrationProvider
from app.models.use_case import UseCase, UseCaseSource, UseCaseStatus
from app.models.user import Role, User, UserRole
from tests.conftest import TestSessionLocal, auth_header, ensure_tenant

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _token(tenant_id: uuid.UUID, role: Role = Role.VIEWER) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        email=f"{role.value.lower()}@test.local",
        roles=[role.value],
        tenant_id=tenant_id,
        org_id=uuid.uuid4(),
    )


async def _make_use_case(
    tenant_id: uuid.UUID,
    department: str,
    owner_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    uc_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        await ensure_tenant(session, tenant_id)
        session.add(
            UseCase(
                id=uc_id,
                tenant_id=tenant_id,
                title=f"Use case in {department}",
                tool="Microsoft Copilot",
                department=department,
                owner_user_id=owner_user_id,
                status=UseCaseStatus.DRAFT,
                source=UseCaseSource.FORM,
                is_test_data=True,
            )
        )
        await session.commit()
    return uc_id


async def _make_user(tenant_id: uuid.UUID, role: Role, active: bool = True) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with TestSessionLocal() as session:
        await ensure_tenant(session, tenant_id)
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@test.local",
                full_name=f"User {user_id}",
                tenant_id=tenant_id,
                is_active=active,
                is_test_data=True,
            )
        )
        session.add(UserRole(id=uuid.uuid4(), user_id=user_id, role=role, tenant_id=tenant_id))
        await session.commit()
    return user_id


async def _make_directory_user(
    tenant_id: uuid.UUID,
    department: str,
    linked_user_id: uuid.UUID | None = None,
    job_title: str | None = None,
    manager_external_user_id: str | None = None,
) -> None:
    async with TestSessionLocal() as session:
        await ensure_tenant(session, tenant_id)
        # DirectoryUser.integration_id is a FK; a bare uuid4() has nothing to
        # point at once Postgres enforces it.
        integration_id = uuid.uuid4()
        session.add(
            Integration(
                id=integration_id,
                tenant_id=tenant_id,
                provider=IntegrationProvider.MICROSOFT_ENTRA_ID,
                display_name="Directory (test)",
                is_test_data=True,
            )
        )
        await session.flush()
        session.add(
            DirectoryUser(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                integration_id=integration_id,
                external_user_id=str(uuid.uuid4()),
                display_name="Directory Person",
                email="directory.person@test.local",
                department=department,
                job_title=job_title,
                manager_external_user_id=manager_external_user_id,
                is_active=True,
                linked_user_id=linked_user_id,
                last_synced_at=datetime.now(UTC),
                is_test_data=True,
            )
        )
        await session.commit()


class TestEmpty:
    async def test_no_use_cases_returns_empty(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["suggestions"] == []
        assert body["total_unowned"] == 0


class TestOwnedExcluded:
    async def test_owned_use_case_not_suggested(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        owner_id = await _make_user(tenant_id, Role.ANALYST)
        await _make_use_case(tenant_id, "Finance", owner_user_id=owner_id)

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []


class TestDirectoryMatch:
    async def test_business_candidate_requires_linked_user(self, client: AsyncClient) -> None:
        """A directory contact with no linked atlas User isn't assignable."""
        tenant_id = uuid.uuid4()
        await _make_use_case(tenant_id, "Grants Management")
        await _make_directory_user(tenant_id, "Grants Management", linked_user_id=None)

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        suggestion = resp.json()["suggestions"][0]
        assert suggestion["business_owner_candidates"] == []

    async def test_business_candidate_matches_department(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        atlas_user_id = await _make_user(tenant_id, Role.ANALYST)
        await _make_use_case(tenant_id, "Grants Management")
        await _make_directory_user(
            tenant_id,
            "Grants Management",
            linked_user_id=atlas_user_id,
            job_title="Grants Manager",
            manager_external_user_id=None,
        )

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        suggestion = resp.json()["suggestions"][0]
        assert suggestion["department"] == "Grants Management"
        candidates = suggestion["business_owner_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["user_id"] == str(atlas_user_id)
        # Senior title + no manager on file → boosted above the 60 base.
        assert candidates[0]["confidence"] > 60.0

    async def test_business_candidate_ignores_other_department(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        atlas_user_id = await _make_user(tenant_id, Role.ANALYST)
        await _make_use_case(tenant_id, "Grants Management")
        await _make_directory_user(tenant_id, "Finance", linked_user_id=atlas_user_id)

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.json()["suggestions"][0]["business_owner_candidates"] == []


class TestFallback:
    async def test_no_directory_falls_back_to_tenant_users(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        await _make_user(tenant_id, Role.ANALYST)
        await _make_use_case(tenant_id, "Grants Management")

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        candidates = resp.json()["suggestions"][0]["business_owner_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["confidence"] == 40.0


class TestItOwnerCandidates:
    async def test_org_admin_and_tenant_admin_ranked(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        tenant_admin_id = await _make_user(tenant_id, Role.TENANT_ADMIN)
        org_admin_id = await _make_user(tenant_id, Role.ORG_ADMIN)
        await _make_user(tenant_id, Role.VIEWER)  # not IT-eligible
        await _make_use_case(tenant_id, "Grants Management")

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        assert resp.status_code == 200
        candidates = resp.json()["suggestions"][0]["it_owner_candidates"]
        candidate_ids = [c["user_id"] for c in candidates]
        assert str(tenant_admin_id) in candidate_ids
        assert str(org_admin_id) in candidate_ids
        assert len(candidates) == 2
        # TENANT_ADMIN ranks above ORG_ADMIN.
        assert candidates[0]["user_id"] == str(tenant_admin_id)
        assert candidates[0]["confidence"] > candidates[1]["confidence"]

    async def test_it_aligned_title_boosts_confidence(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        org_admin_id = await _make_user(tenant_id, Role.ORG_ADMIN)
        await _make_directory_user(
            tenant_id,
            "IT",
            linked_user_id=org_admin_id,
            job_title="Head of Information Technology",
        )
        await _make_use_case(tenant_id, "Grants Management")

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id)),
        )
        candidates = resp.json()["suggestions"][0]["it_owner_candidates"]
        assert candidates[0]["confidence"] > 65.0


class TestAccessControl:
    async def test_viewer_can_read(self, client: AsyncClient) -> None:
        tenant_id = uuid.uuid4()
        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_id, Role.VIEWER)),
        )
        assert resp.status_code == 200

    async def test_unauthenticated_rejected(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/use-cases/owner-suggestions")
        assert resp.status_code == 401


class TestTenantIsolation:
    async def test_other_tenant_not_visible(self, client: AsyncClient) -> None:
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        await _make_use_case(tenant_a, "Grants Management")

        resp = await client.get(
            "/api/v1/use-cases/owner-suggestions",
            headers=auth_header(_token(tenant_b)),
        )
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []
