"""#233 · Integration tests for /api/v1/agent-discovery.

Covers:
  - scan → list (grouped by status) lifecycle
  - scan idempotency (re-running upserts, doesn't duplicate)
  - status update (approve/dismiss) survives a re-scan
  - tenant isolation
  - RBAC (Viewer reads; OrgAdmin+ scans/patches)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from tests.conftest import (
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")
SECOND_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

BASE = "/api/v1/agent-discovery"


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


class TestScan:
    async def test_org_admin_can_scan(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scanned"] > 0
        assert body["created"] == body["scanned"]
        assert body["updated"] == 0
        assert all(a["status"] == "shadow" for a in body["agents"])

    async def test_viewer_cannot_scan(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(f"{BASE}/scan", headers=auth_header(viewer_token))
        assert resp.status_code == 403, resp.text

    async def test_rescan_is_idempotent(self, client: AsyncClient, tenant_admin_token: str) -> None:
        first = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        second = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        assert second.json()["scanned"] == first.json()["scanned"]
        assert second.json()["created"] == 0
        assert second.json()["updated"] == first.json()["scanned"]

        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        total = sum(len(v) for v in listed.json().values())
        assert total == first.json()["scanned"]


class TestList:
    async def test_list_groups_by_status(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        resp = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"shadow", "pending", "approved", "dismissed"}
        assert len(body["shadow"]) > 0
        assert body["approved"] == []

    async def test_viewer_can_list(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        resp = await client.get(BASE, headers=auth_header(viewer_token))
        assert resp.status_code == 200, resp.text


class TestTenantIsolation:
    async def test_other_tenant_sees_nothing(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        other = _second_tenant_admin_token()
        resp = await client.get(BASE, headers=auth_header(other))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert all(v == [] for v in body.values())


class TestUpdateStatus:
    async def test_approve_agent(self, client: AsyncClient, tenant_admin_token: str) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        agent_id = scanned.json()["agents"][0]["id"]
        resp = await client.patch(
            f"{BASE}/{agent_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "approved"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

    async def test_status_survives_rescan(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        agent_id = scanned.json()["agents"][0]["id"]
        await client.patch(
            f"{BASE}/{agent_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        dismissed_ids = {a["id"] for a in listed.json()["dismissed"]}
        assert agent_id in dismissed_ids

    async def test_update_unknown_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.patch(
            f"{BASE}/{uuid.uuid4()}",
            headers=auth_header(tenant_admin_token),
            json={"status": "approved"},
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_update(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        agent_id = scanned.json()["agents"][0]["id"]
        resp = await client.patch(
            f"{BASE}/{agent_id}",
            headers=auth_header(viewer_token),
            json={"status": "approved"},
        )
        assert resp.status_code == 403, resp.text
