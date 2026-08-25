"""HTTP tests for promotion from the intake registry to the SPM catalogue."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from tests.conftest import auth_header

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


async def _create_use_case(client: AsyncClient, token: str) -> dict:
    response = await client.post(
        "/api/v1/use-cases",
        headers=auth_header(token),
        json={
            "title": "Customer support drafting",
            "tool": "Microsoft Copilot",
            "department": "Support",
            "status": "REVIEW",
            "source": "FORM",
            "data_classes": ["customer_pii", "strategy_docs"],
            "notes": "Draft response suggestions for support agents.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestUseCasePromotion:
    async def test_promote_creates_spm_asset_and_use_case(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        intake = await _create_use_case(client, tenant_admin_token)

        response = await client.post(
            f"/api/v1/use-cases/{intake['id']}/promote",
            headers=auth_header(tenant_admin_token),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["use_case_id"] == intake["id"]
        assert body["asset_id"]
        assert body["ai_use_case_id"]
        assert body["created"] is True

        asset_response = await client.get(
            f"/api/v1/ai-assets/{body['asset_id']}",
            headers=auth_header(tenant_admin_token),
        )
        assert asset_response.status_code == 200, asset_response.text
        assert asset_response.json()["name"] == intake["title"]
        assert asset_response.json()["deployment_status"] == "testing"
        assert asset_response.json()["model_type"] == intake["tool"]

        ai_use_cases_response = await client.get(
            f"/api/v1/ai-assets/{body['asset_id']}/use-cases",
            headers=auth_header(tenant_admin_token),
        )
        assert ai_use_cases_response.status_code == 200, ai_use_cases_response.text
        ai_use_case = ai_use_cases_response.json()[0]
        assert ai_use_case["id"] == body["ai_use_case_id"]
        assert ai_use_case["discovered_via"] == f"promoted_from:{intake['id']}"
        assert ai_use_case["data_classification"] == "customer_pii"
        assert ai_use_case["status"] == "review"

    async def test_second_promote_returns_existing_asset(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        intake = await _create_use_case(client, tenant_admin_token)

        first = await client.post(
            f"/api/v1/use-cases/{intake['id']}/promote",
            headers=auth_header(tenant_admin_token),
        )
        second = await client.post(
            f"/api/v1/use-cases/{intake['id']}/promote",
            headers=auth_header(tenant_admin_token),
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert second.json()["created"] is False
        assert second.json()["asset_id"] == first.json()["asset_id"]
        assert second.json()["ai_use_case_id"] == first.json()["ai_use_case_id"]

    async def test_promote_missing_use_case_returns_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        response = await client.post(
            f"/api/v1/use-cases/{uuid.uuid4()}/promote",
            headers=auth_header(tenant_admin_token),
        )

        assert response.status_code == 404, response.text

    async def test_cross_tenant_or_viewer_cannot_promote(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        intake = await _create_use_case(client, tenant_admin_token)

        cross_tenant_response = await client.post(
            f"/api/v1/use-cases/{intake['id']}/promote",
            headers=auth_header(_other_tenant_admin_token()),
        )
        viewer_response = await client.post(
            f"/api/v1/use-cases/{intake['id']}/promote",
            headers=auth_header(viewer_token),
        )

        assert cross_tenant_response.status_code == 403, cross_tenant_response.text
        assert viewer_response.status_code == 403, viewer_response.text
