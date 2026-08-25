"""Integration tests for /api/v1/integrations/ardoq (issue #248).

Covers admin-gating and that the manifest totals match the ZIP's
per-file row counts. CSV column-content correctness is already
covered by `tests/unit/test_ardoq_export.py`.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from httpx import AsyncClient

from app.models.ai_asset import AIAsset
from app.models.compliance_assessment import ComplianceAssessment
from app.models.spm_entities import OrganizationalUnit, Person
from tests.conftest import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    TestSessionLocal,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/integrations/ardoq"


def _scope() -> dict[str, uuid.UUID]:
    return {"tenant_id": TEST_TENANT_ID, "org_id": TEST_ORG_ID}


async def _seed() -> None:
    async with TestSessionLocal() as db:
        person = Person(**_scope(), component_name="Sarah Chen", email="sarah@example.test")
        db.add(person)
        db.add(OrganizationalUnit(**_scope(), component_name="Legal"))
        asset = AIAsset(**_scope(), name="Support Copilot", deployment_status="active")
        db.add(asset)
        await db.flush()

        db.add(
            ComplianceAssessment(
                **_scope(),
                asset_id=asset.id,
                framework="GDPR",
                passed=True,
            )
        )
        await db.commit()


class TestAuth:
    async def test_manifest_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/manifest")
        assert resp.status_code == 401

    async def test_manifest_rejects_viewer(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.get(f"{BASE}/manifest", headers=auth_header(viewer_token))
        assert resp.status_code == 403

    async def test_export_rejects_viewer(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.get(f"{BASE}/export", headers=auth_header(viewer_token))
        assert resp.status_code == 403


class TestManifest:
    async def test_manifest_counts_match_seeded_rows(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await _seed()
        resp = await client.get(f"{BASE}/manifest", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["totals"]["people"] == 1
        assert body["totals"]["organizations"] == 1
        assert body["totals"]["applications"] == 1
        assert body["totals"]["compliance_assessments"] == 1
        assert body["generated_at"]

    async def test_manifest_empty_tenant_is_all_zero(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.get(f"{BASE}/manifest", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200
        assert all(count == 0 for count in resp.json()["totals"].values())


class TestExport:
    async def test_export_zip_matches_manifest(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await _seed()
        manifest = await client.get(f"{BASE}/manifest", headers=auth_header(tenant_admin_token))
        totals = manifest.json()["totals"]

        resp = await client.get(f"{BASE}/export", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "ardoq-ai-lens-export.zip" in resp.headers["content-disposition"]

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert set(zf.namelist()) == {
            "01_technical_capabilities.csv",
            "02_applications.csv",
            "03_technology_products.csv",
            "04_technology_services.csv",
            "05_people.csv",
            "06_organization.csv",
            "07_data_stores.csv",
            "08_compliance_assessments.csv",
            "09_references.csv",
        }

        people_rows = zf.read("05_people.csv").decode().strip().splitlines()
        assert len(people_rows) - 1 == totals["people"]
        app_rows = zf.read("02_applications.csv").decode().strip().splitlines()
        assert len(app_rows) - 1 == totals["applications"]
        assessment_rows = zf.read("08_compliance_assessments.csv").decode().strip().splitlines()
        assert len(assessment_rows) - 1 == totals["compliance_assessments"]
