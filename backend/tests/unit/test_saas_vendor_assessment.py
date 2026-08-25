"""Integration tests for SP-1 assessment-exchange endpoints (#192).

Covers POST /saas-vendor-ai/{id}/request-assessment (outbound) and
/import-assessment (inbound), plus the effect on has_assessment and RBAC.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import (
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/saas-vendor-ai"


async def _create_vendor(client: AsyncClient, token: str) -> dict:
    payload = {
        "name": "ChatGPT Enterprise",
        "department": "Engineering",
        "category": "llm_provider",
        "ai_feature": "GPT-4o",
        "risk_score": 60,
    }
    r = await client.post(BASE, headers=auth_header(token), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


class TestRequestAssessment:
    async def test_request_creates_sent_row_with_token(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        vendor = await _create_vendor(client, tenant_admin_token)
        r = await client.post(
            f"{BASE}/{vendor['id']}/request-assessment",
            headers=auth_header(tenant_admin_token),
            json={"questionnaire": "SIG-Lite", "notes": "Q3 review"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "sent"
        assert body["token"]  # generated
        assert body["sent_at"] is not None
        assert body["questionnaire"] == "SIG-Lite"
        assert body["use_case_id"] == vendor["id"]

    async def test_request_flips_has_assessment(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        vendor = await _create_vendor(client, tenant_admin_token)
        # before: no assessment
        before = await client.get(f"{BASE}/{vendor['id']}", headers=auth_header(tenant_admin_token))
        assert before.json()["has_assessment"] is False
        # request one
        await client.post(
            f"{BASE}/{vendor['id']}/request-assessment",
            headers=auth_header(tenant_admin_token),
            json={},
        )
        after = await client.get(f"{BASE}/{vendor['id']}", headers=auth_header(tenant_admin_token))
        assert after.json()["has_assessment"] is True

    async def test_unknown_vendor_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        r = await client.post(
            f"{BASE}/{uuid.uuid4()}/request-assessment",
            headers=auth_header(tenant_admin_token),
            json={},
        )
        assert r.status_code == 404, r.text

    async def test_viewer_forbidden(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        vendor = await _create_vendor(client, tenant_admin_token)
        r = await client.post(
            f"{BASE}/{vendor['id']}/request-assessment",
            headers=auth_header(viewer_token),
            json={},
        )
        assert r.status_code == 403, r.text


class TestImportAssessment:
    async def test_import_records_row(self, client: AsyncClient, tenant_admin_token: str) -> None:
        vendor = await _create_vendor(client, tenant_admin_token)
        r = await client.post(
            f"{BASE}/{vendor['id']}/import-assessment",
            headers=auth_header(tenant_admin_token),
            json={
                "source": "trust_center",
                "reference": "https://trust.example.com/report",
                "payload": {"score": 82},
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["source"] == "trust_center"
        assert body["reference"] == "https://trust.example.com/report"
        assert body["payload"] == {"score": 82}
        assert body["imported_at"] is not None

    async def test_import_flips_has_assessment(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        vendor = await _create_vendor(client, tenant_admin_token)
        await client.post(
            f"{BASE}/{vendor['id']}/import-assessment",
            headers=auth_header(tenant_admin_token),
            json={"source": "peer_shared"},
        )
        after = await client.get(f"{BASE}/{vendor['id']}", headers=auth_header(tenant_admin_token))
        assert after.json()["has_assessment"] is True

    async def test_unknown_vendor_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        r = await client.post(
            f"{BASE}/{uuid.uuid4()}/import-assessment",
            headers=auth_header(tenant_admin_token),
            json={"source": "upload"},
        )
        assert r.status_code == 404, r.text
