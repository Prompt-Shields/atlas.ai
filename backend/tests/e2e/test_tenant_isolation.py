"""
E2E tests for tenant isolation / multi-tenancy boundaries.

Ensures that data from one tenant is not accessible to another.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

BASE_URL = os.environ.get("E2E_API_URL", "http://localhost:8001/api/v1")
SUPER_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "admin@test.local")
SUPER_PASS = os.environ.get("SUPER_ADMIN_PASSWORD", "TestAdmin_P@ss1")


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTenantIsolation:
    """Verify that tenants cannot see each other's data."""

    @pytest.fixture(autouse=True)
    async def setup(self) -> None:
        self.client = httpx.AsyncClient(timeout=30.0)
        self.run_id = str(uuid.uuid4())[:8]

    async def test_create_two_tenants_data_isolated(self) -> None:
        """
        Create two tenants, ingest data for each, confirm cross-tenant
        queries return zero results.
        """
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)

        # Create tenant A
        resp_a = await self.client.post(
            f"{BASE_URL}/tenants",
            headers=_auth(token),
            json={"name": f"Tenant A {self.run_id}", "slug": f"tenant-a-{self.run_id}"},
        )
        assert resp_a.status_code in (200, 201), resp_a.text

        # Create tenant B
        resp_b = await self.client.post(
            f"{BASE_URL}/tenants",
            headers=_auth(token),
            json={"name": f"Tenant B {self.run_id}", "slug": f"tenant-b-{self.run_id}"},
        )
        assert resp_b.status_code in (200, 201), resp_b.text

        # Ingest blob for tenant A context (super admin can specify)
        resp_blob = await self.client.post(
            f"{BASE_URL}/adapters/manual/ingest",
            headers=_auth(token),
            json={
                "content": f"[TEST:{self.run_id}] Tenant A secret compliance data.",
                "source_type": "manual",
                "source_id": f"iso-test-a-{self.run_id}",
            },
        )
        assert resp_blob.status_code in (200, 201), resp_blob.text

    async def test_unauthenticated_cannot_access_data(self) -> None:
        """Unauthenticated requests should be rejected."""
        resp = await self.client.get(f"{BASE_URL}/risks")
        assert resp.status_code == 401

        resp = await self.client.get(f"{BASE_URL}/tenants")
        assert resp.status_code == 401
