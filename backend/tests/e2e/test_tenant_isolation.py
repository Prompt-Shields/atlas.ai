"""
E2E tests for tenant isolation / multi-tenancy boundaries.

Ensures that data from one tenant is not accessible to another.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.e2e.stack import BASE_URL, auth, ingest, tenant_user

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestTenantIsolation:
    """Verify that tenants cannot see each other's data."""

    @pytest.fixture(autouse=True)
    async def setup(self) -> None:
        self.client = httpx.AsyncClient(timeout=30.0)
        self.run_id = str(uuid.uuid4())[:8]

    async def test_create_two_tenants_data_isolated(self) -> None:
        """Two tenants each ingest a blob, and each sees only its own."""
        a = await tenant_user(self.client, f"iso-a-{self.run_id}")
        b = await tenant_user(self.client, f"iso-b-{self.run_id}")
        blob_a = await ingest(
            self.client, a, f"[TEST:{self.run_id}] Tenant A secret.", f"iso-a-{self.run_id}"
        )
        blob_b = await ingest(
            self.client, b, f"[TEST:{self.run_id}] Tenant B secret.", f"iso-b-{self.run_id}"
        )

        for user, own, other in ((a, blob_a, blob_b), (b, blob_b, blob_a)):
            resp = await self.client.get(
                f"{BASE_URL}/adapters/blobs",
                headers=auth(user.token),
                params={"page_size": 100},
            )
            assert resp.status_code == 200, resp.text
            seen = {blob["id"] for blob in resp.json()["blobs"]}
            assert own["id"] in seen
            assert other["id"] not in seen
            assert all(blob["tenant_id"] == user.tenant_id for blob in resp.json()["blobs"])

    async def test_unauthenticated_cannot_access_data(self) -> None:
        """Unauthenticated requests should be rejected."""
        resp = await self.client.get(f"{BASE_URL}/risks")
        assert resp.status_code == 401

        resp = await self.client.get(f"{BASE_URL}/tenants")
        assert resp.status_code == 401
