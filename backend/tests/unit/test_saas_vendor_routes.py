"""Integration tests for /api/v1/saas-vendor-ai (SP-1, issue #191).

Covers:
  - create → list → detail → kpi → csv → patch lifecycle
  - tenant isolation (Tenant B can't see Tenant A's vendors)
  - RBAC (Viewer reads; OrgAdmin+ writes)
  - KPI aggregation (high-risk / trains-on-data / no-DPA)
  - has_assessment stays False until #192 wires assessment rows
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

BASE = "/api/v1/saas-vendor-ai"


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "ChatGPT Enterprise",
        "department": "Engineering",
        "category": "llm_provider",
        "ai_feature": "OpenAI GPT-4o",
        "risk_score": 80,
        "models": ["gpt-4o"],
        "sub_processors": ["Microsoft Azure"],
        "data_flows": [
            {"description": "prompts", "classification": "confidential", "trains_on_data": True}
        ],
        "certifications": ["SOC2"],
        "dpa": False,
    }
    base.update(overrides)
    return base


async def _create(client: AsyncClient, token: str, **overrides: object) -> dict:
    resp = await client.post(BASE, headers=auth_header(token), json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreate:
    async def test_org_admin_can_create(self, client: AsyncClient, tenant_admin_token: str) -> None:
        body = await _create(client, tenant_admin_token)
        assert body["name"] == "ChatGPT Enterprise"
        assert body["ai_feature"] == "OpenAI GPT-4o"  # maps to use_case.tool
        assert body["category"] == "llm_provider"
        assert body["risk_score"] == 80
        assert body["source"] == "FORM"
        assert body["has_assessment"] is False
        assert body["id"]

    async def test_viewer_cannot_create(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(BASE, headers=auth_header(viewer_token), json=_payload())
        assert resp.status_code == 403, resp.text


class TestListAndGet:
    async def test_list_returns_created_vendor(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        await _create(client, tenant_admin_token)
        resp = await client.get(BASE, headers=auth_header(viewer_token))
        assert resp.status_code == 200, resp.text
        vendors = resp.json()
        assert len(vendors) == 1
        assert vendors[0]["name"] == "ChatGPT Enterprise"

    async def test_get_by_id(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create(client, tenant_admin_token)
        resp = await client.get(f"{BASE}/{created['id']}", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == created["id"]

    async def test_get_unknown_is_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.get(f"{BASE}/{uuid.uuid4()}", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 404, resp.text


class TestTenantIsolation:
    async def test_other_tenant_cannot_see(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await _create(client, tenant_admin_token)
        other = _second_tenant_admin_token()
        # not in the other tenant's list
        resp = await client.get(BASE, headers=auth_header(other))
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
        # and not fetchable by id
        resp = await client.get(f"{BASE}/{created['id']}", headers=auth_header(other))
        assert resp.status_code == 404, resp.text


class TestKpi:
    async def test_kpi_aggregates(self, client: AsyncClient, tenant_admin_token: str) -> None:
        # high risk (80), trains-on-data True, no DPA
        await _create(client, tenant_admin_token)
        # low risk (10), no training, has DPA
        await _create(
            client,
            tenant_admin_token,
            name="Grammarly",
            risk_score=10,
            dpa=True,
            data_flows=[
                {"description": "text", "classification": "internal", "trains_on_data": False}
            ],
        )
        resp = await client.get(f"{BASE}/kpi", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        kpi = resp.json()
        assert kpi["total"] == 2
        assert kpi["high_risk"] == 1
        assert kpi["trains_on_data"] == 1
        assert kpi["no_dpa"] == 1


class TestCsv:
    async def test_export_csv(self, client: AsyncClient, tenant_admin_token: str) -> None:
        await _create(client, tenant_admin_token)
        resp = await client.get(f"{BASE}/export.csv", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        text = resp.text
        lines = text.strip().splitlines()
        assert lines[0].startswith("name,department,category,ai_feature,risk_score")
        assert "ChatGPT Enterprise" in text
        assert "gpt-4o" in text  # list joined with "; "


class TestUpdate:
    async def test_patch_fields(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await _create(client, tenant_admin_token)
        resp = await client.patch(
            f"{BASE}/{created['id']}",
            headers=auth_header(tenant_admin_token),
            json={"risk_score": 20, "dpa": True, "name": "ChatGPT Team"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["risk_score"] == 20
        assert body["dpa"] is True
        assert body["name"] == "ChatGPT Team"

    async def test_patch_unknown_is_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.patch(
            f"{BASE}/{uuid.uuid4()}",
            headers=auth_header(tenant_admin_token),
            json={"risk_score": 5},
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_patch(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        created = await _create(client, tenant_admin_token)
        resp = await client.patch(
            f"{BASE}/{created['id']}",
            headers=auth_header(viewer_token),
            json={"risk_score": 5},
        )
        assert resp.status_code == 403, resp.text
