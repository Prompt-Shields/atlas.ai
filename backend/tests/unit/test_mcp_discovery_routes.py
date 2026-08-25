"""#241 · Integration tests for /api/v1/discover/mcp.

Covers:
  - scan → list (correlated + scored) lifecycle
  - scan idempotency (re-running upserts, doesn't duplicate)
  - promote → creates an AI-inventory asset and flips status
  - status update (dismiss) survives a re-scan
  - tenant isolation
  - RBAC (Viewer reads; OrgAdmin+ scans/promotes/patches)
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

SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")
SECOND_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")

BASE = "/api/v1/discover/mcp"


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000098"),
        email="other-mcp@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


class TestScan:
    async def test_org_admin_can_scan(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scanned_observations"] > 0
        assert body["created"] == len(body["servers"])
        assert body["updated"] == 0
        assert all(s["status"] == "candidate" for s in body["servers"])
        assert all(0 <= s["confidence_score"] <= 100 for s in body["servers"])

    async def test_viewer_cannot_scan(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(f"{BASE}/scan", headers=auth_header(viewer_token))
        assert resp.status_code == 403, resp.text

    async def test_rescan_is_idempotent(self, client: AsyncClient, tenant_admin_token: str) -> None:
        first = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        second = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        assert second.json()["created"] == 0
        assert second.json()["updated"] == len(first.json()["servers"])

        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert len(listed.json()["servers"]) == len(first.json()["servers"])

    async def test_multi_source_candidate_scores_higher(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        servers = {s["name"]: s for s in scanned.json()["servers"]}
        # "filesystem" is corroborated by 2 collectors, "slack-mcp" by 1.
        assert servers["filesystem"]["confidence_score"] > servers["slack-mcp"]["confidence_score"]
        assert len(servers["filesystem"]["sources"]) == 2


class TestList:
    async def test_list_is_sorted_by_confidence_desc(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        resp = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        scores = [s["confidence_score"] for s in resp.json()["servers"]]
        assert scores == sorted(scores, reverse=True)

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
        assert resp.json()["servers"] == []


class TestUpdateStatus:
    async def test_dismiss_server(self, client: AsyncClient, tenant_admin_token: str) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server_id = scanned.json()["servers"][0]["id"]
        resp = await client.patch(
            f"{BASE}/{server_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "dismissed"

    async def test_status_survives_rescan(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server_id = scanned.json()["servers"][0]["id"]
        await client.patch(
            f"{BASE}/{server_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        server = next(s for s in listed.json()["servers"] if s["id"] == server_id)
        assert server["status"] == "dismissed"

    async def test_update_unknown_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.patch(
            f"{BASE}/{uuid.uuid4()}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_update(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server_id = scanned.json()["servers"][0]["id"]
        resp = await client.patch(
            f"{BASE}/{server_id}",
            headers=auth_header(viewer_token),
            json={"status": "dismissed"},
        )
        assert resp.status_code == 403, resp.text


class TestPromote:
    async def test_promote_creates_ai_asset(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server = scanned.json()["servers"][0]
        resp = await client.post(
            f"{BASE}/{server['id']}/promote", headers=auth_header(tenant_admin_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server"]["status"] == "promoted"
        assert body["server"]["promoted_asset_id"] == body["asset_id"]

        asset_resp = await client.get(
            f"/api/v1/ai-assets/{body['asset_id']}", headers=auth_header(tenant_admin_token)
        )
        assert asset_resp.status_code == 200, asset_resp.text
        asset = asset_resp.json()
        assert asset["name"] == server["name"]
        assert asset["model_type"] == "mcp_server"
        assert asset["is_third_party"] is True

    async def test_promote_unknown_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/{uuid.uuid4()}/promote", headers=auth_header(tenant_admin_token)
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_promote(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server_id = scanned.json()["servers"][0]["id"]
        resp = await client.post(f"{BASE}/{server_id}/promote", headers=auth_header(viewer_token))
        assert resp.status_code == 403, resp.text

    async def test_cannot_promote_other_tenants_server(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        scanned = await client.post(f"{BASE}/scan", headers=auth_header(tenant_admin_token))
        server_id = scanned.json()["servers"][0]["id"]
        other = _second_tenant_admin_token()
        resp = await client.post(f"{BASE}/{server_id}/promote", headers=auth_header(other))
        assert resp.status_code == 404, resp.text
