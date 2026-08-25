"""Unit tests for RBAC enforcement."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestRBAC:
    async def test_super_admin_can_list_tenants(
        self, client: AsyncClient, super_admin_token: str
    ) -> None:
        resp = await client.get("/api/v1/tenants", headers=auth_header(super_admin_token))
        # Should succeed (200), not 403
        assert resp.status_code == 200

    async def test_viewer_cannot_create_tenant(
        self, client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await client.post(
            "/api/v1/tenants",
            headers=auth_header(viewer_token),
            json={"name": "Evil Tenant", "slug": "evil"},
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_invite(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            "/api/v1/invites",
            headers=auth_header(viewer_token),
            json={
                "email": "new@test.local",
                "role": "ANALYST",
                "tenant_id": "00000000-0000-0000-0000-000000000010",
            },
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_access_admin_test_pipeline(
        self, client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await client.post(
            "/api/v1/admin/test/run-pipeline",
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_purge(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            "/api/v1/admin/test/purge",
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403

    async def test_unauthenticated_denied(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/tenants")
        assert resp.status_code == 401

        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401

    async def test_tenant_admin_cannot_create_tenant(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        """Only super admin can create tenants."""
        resp = await client.post(
            "/api/v1/tenants",
            headers=auth_header(tenant_admin_token),
            json={"name": "Not Allowed", "slug": "not-allowed"},
        )
        assert resp.status_code == 403
