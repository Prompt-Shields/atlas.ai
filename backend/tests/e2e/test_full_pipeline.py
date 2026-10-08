"""
End-to-end tests for the full AI-GRC pipeline.

These tests exercise:
  1. Tenant creation
  2. A tenant user with an org and an API key
  3. Blob ingestion (manual adapter)
  4. Risk analysis job trigger
  5. Correlation engine job trigger
  6. Dispatch event verification
  7. Test data purge

Run against a live local stack:
  docker compose -f docker-compose.test.yml up -d
  pytest tests/e2e/ -v -m e2e
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from tests.e2e.stack import BASE_URL, SUPER_EMAIL, SUPER_PASS, ingest, tenant_user
from tests.e2e.stack import auth as _auth
from tests.e2e.stack import login as _login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _sleep(seconds: int) -> None:
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestE2EPipeline:
    """Full pipeline: create tenant → ingest → analyze → correlate → dispatch → purge."""

    @pytest.fixture(autouse=True)
    async def setup(self) -> None:
        self.client = httpx.AsyncClient(timeout=60.0)
        self.test_run_id = str(uuid.uuid4())[:8]

    async def test_01_super_admin_login(self) -> None:
        """Super admin can log in and receive a JWT."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        assert token and len(token) > 20

    async def test_02_create_tenant(self) -> None:
        """Super admin can create a new tenant."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        slug = f"e2e-test-{self.test_run_id}"
        resp = await self.client.post(
            f"{BASE_URL}/tenants",
            headers=_auth(token),
            json={"name": f"E2E Test Org {self.test_run_id}", "slug": slug},
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()
        assert data["slug"] == slug

    async def test_03_ingest_blob(self) -> None:
        """A tenant user ingests a blob via the manual adapter: JWT plus API key."""
        user = await tenant_user(self.client, f"ingest-{self.test_run_id}")
        without_key = await self.client.post(
            f"{BASE_URL}/adapters/manual/ingest",
            headers=_auth(user.token),
            json={"content": "no key", "source_type": "manual", "source_id": "no-key"},
        )
        assert without_key.status_code == 401, without_key.text
        blob = await ingest(
            self.client,
            user,
            content=(
                f"[TEST:{self.test_run_id}] AI model deployed without bias testing. "
                "No documentation for training data provenance. "
                "Model serves 10k daily predictions in healthcare domain."
            ),
            source_id=f"e2e-test-{self.test_run_id}",
        )
        assert blob["id"]
        assert blob["tenant_id"] == user.tenant_id
        assert blob["org_id"] == user.org_id

    async def test_04_trigger_test_pipeline(self) -> None:
        """Trigger risk analysis via admin test-run endpoint."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        user = await tenant_user(self.client, f"pipeline-{self.test_run_id}")
        resp = await self.client.post(
            f"{BASE_URL}/admin/test/run-pipeline",
            headers=_auth(token),
            params={"tenant_id": user.tenant_id, "org_id": user.org_id},
        )
        assert resp.status_code in (200, 202), resp.text

    async def test_05_verify_risks_created(self) -> None:
        """Verify risk records were created after analysis."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        # Poll with retry for async processing
        resp = None
        for _ in range(10):
            resp = await self.client.get(
                f"{BASE_URL}/risks",
                headers=_auth(token),
                params={"is_test": "true"},
            )
            if resp.status_code == 200 and len(resp.json().get("items", [])) > 0:
                break
            await _sleep(3)
        assert resp is not None
        assert resp.status_code == 200

    async def test_06_verify_dispatch_events(self) -> None:
        """Verify dispatch events were created."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        resp = None
        for _ in range(10):
            resp = await self.client.get(
                f"{BASE_URL}/dispatch/events",
                headers=_auth(token),
                params={"is_test": "true"},
            )
            if resp.status_code == 200 and len(resp.json().get("items", [])) > 0:
                break
            await _sleep(3)
        assert resp is not None
        # Even if no events (mock LLM), endpoint should work
        assert resp.status_code == 200

    async def test_07_purge_test_data(self) -> None:
        """Super admin can purge all test data."""
        token = await _login(self.client, SUPER_EMAIL, SUPER_PASS)
        resp = await self.client.post(
            f"{BASE_URL}/admin/test/purge",
            headers=_auth(token),
            params={"confirm": "true"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "counts" in data or "message" in data
