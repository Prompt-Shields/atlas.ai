"""Integration tests for the re-ported AI-SPM inventory routers.

Covers /api/v1/ai-assets, /compliance, /model-risk — create/list/get/patch,
tenant isolation, and RBAC. The branch's parallel policy engine is not part of
this port (main ships its own), so no policy coverage here.
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


def _other_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


async def _create_asset(client: AsyncClient, token: str, **over: object) -> dict:
    body = {"name": "Fraud model", "description": "scoring", "is_third_party": False}
    body.update(over)
    r = await client.post("/api/v1/ai-assets", headers=auth_header(token), json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestAIAssets:
    async def test_create_list_get(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create_asset(client, tenant_admin_token)
        assert created["name"] == "Fraud model"
        assert created["id"]

        lst = await client.get("/api/v1/ai-assets", headers=auth_header(tenant_admin_token))
        assert lst.status_code == 200, lst.text
        data = lst.json()
        assert data["total"] == 1
        assert data["assets"][0]["id"] == created["id"]

        one = await client.get(
            f"/api/v1/ai-assets/{created['id']}", headers=auth_header(tenant_admin_token)
        )
        assert one.status_code == 200, one.text
        assert one.json()["name"] == "Fraud model"

    async def test_patch(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create_asset(client, tenant_admin_token)
        r = await client.patch(
            f"/api/v1/ai-assets/{created['id']}",
            headers=auth_header(tenant_admin_token),
            json={"name": "Fraud model v2", "is_third_party": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Fraud model v2"
        assert r.json()["is_third_party"] is True

    async def test_viewer_cannot_create(self, client: AsyncClient, viewer_token: str) -> None:
        r = await client.post(
            "/api/v1/ai-assets", headers=auth_header(viewer_token), json={"name": "x"}
        )
        assert r.status_code == 403, r.text

    async def test_tenant_isolation(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create_asset(client, tenant_admin_token)
        other = _other_tenant_admin_token()
        lst = await client.get("/api/v1/ai-assets", headers=auth_header(other))
        assert lst.status_code == 200, lst.text
        assert lst.json()["total"] == 0
        one = await client.get(f"/api/v1/ai-assets/{created['id']}", headers=auth_header(other))
        assert one.status_code == 404, one.text

    async def test_delete(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create_asset(client, tenant_admin_token)
        r = await client.delete(
            f"/api/v1/ai-assets/{created['id']}", headers=auth_header(tenant_admin_token)
        )
        assert r.status_code == 204, r.text
        gone = await client.get(
            f"/api/v1/ai-assets/{created['id']}", headers=auth_header(tenant_admin_token)
        )
        assert gone.status_code == 404


class TestCompliance:
    async def test_create_and_list(self, client: AsyncClient, tenant_admin_token: str) -> None:
        asset = await _create_asset(client, tenant_admin_token)
        r = await client.post(
            "/api/v1/compliance",
            headers=auth_header(tenant_admin_token),
            json={"asset_id": asset["id"], "framework": "EU_AI_ACT", "passed": True},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["framework"] == "EU_AI_ACT"
        assert body["passed"] is True
        assert body["asset_id"] == asset["id"]

        lst = await client.get("/api/v1/compliance", headers=auth_header(tenant_admin_token))
        assert lst.status_code == 200, lst.text
        assert lst.json()["total"] == 1

    async def test_viewer_cannot_create(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        asset = await _create_asset(client, tenant_admin_token)
        r = await client.post(
            "/api/v1/compliance",
            headers=auth_header(viewer_token),
            json={"asset_id": asset["id"], "framework": "GDPR", "passed": False},
        )
        assert r.status_code == 403, r.text


class TestModelRisk:
    async def test_list_empty(self, client: AsyncClient, tenant_admin_token: str) -> None:
        r = await client.get("/api/v1/model-risk", headers=auth_header(tenant_admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 0

    async def test_get_unknown_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        r = await client.get(
            f"/api/v1/model-risk/{uuid.uuid4()}", headers=auth_header(tenant_admin_token)
        )
        assert r.status_code == 404, r.text
