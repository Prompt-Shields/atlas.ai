"""#243 · Integration tests for /api/v1/discover/defender-import.

Covers:
  - import → list (extracted + enriched) lifecycle, using the bundled seed
    export when no raw_text is supplied
  - import idempotency (re-running upserts, doesn't duplicate)
  - accept → creates a SaaS vendor (use_case + profile) and flips status
  - status update (dismiss) survives a re-import
  - tenant isolation
  - RBAC (Viewer reads; OrgAdmin+ imports/accepts/patches)
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

SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000022")
SECOND_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000022")

BASE = "/api/v1/discover/defender-import"

_RAW_TEXT = (
    "ChatGPT Enterprise — 312 active users — 1.2 TB transferred\n"
    "GitHub Copilot — 54 active users — 12 GB transferred\n"
    "Some Unknown Tool — 10 active users — 1 GB transferred\n"
)


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other-defender@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


class TestImport:
    async def test_org_admin_can_import_with_seed_export(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(f"{BASE}/import", headers=auth_header(tenant_admin_token), json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched_lines"] == 5
        assert body["created"] == 5
        assert body["updated"] == 0
        assert all(a["status"] == "pending" for a in body["apps"])
        assert all(0 <= a["risk_score"] <= 100 for a in body["apps"])
        assert all(a["compliance_status"] for a in body["apps"])

    async def test_import_skips_unrecognised_lines(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/import",
            headers=auth_header(tenant_admin_token),
            json={"raw_text": _RAW_TEXT},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched_lines"] == 2
        names = {a["name"] for a in body["apps"]}
        assert names == {"ChatGPT Enterprise", "GitHub Copilot"}

    async def test_viewer_cannot_import(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(f"{BASE}/import", headers=auth_header(viewer_token), json={})
        assert resp.status_code == 403, resp.text

    async def test_reimport_is_idempotent(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        first = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        second = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        assert second.json()["created"] == 0
        assert second.json()["updated"] == len(first.json()["apps"])

        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert len(listed.json()["apps"]) == len(first.json()["apps"])


class TestList:
    async def test_list_is_sorted_by_risk_desc(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(f"{BASE}/import", headers=auth_header(tenant_admin_token), json={})
        resp = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        scores = [a["risk_score"] for a in resp.json()["apps"]]
        assert scores == sorted(scores, reverse=True)

    async def test_viewer_can_list(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        await client.post(f"{BASE}/import", headers=auth_header(tenant_admin_token), json={})
        resp = await client.get(BASE, headers=auth_header(viewer_token))
        assert resp.status_code == 200, resp.text


class TestTenantIsolation:
    async def test_other_tenant_sees_nothing(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(f"{BASE}/import", headers=auth_header(tenant_admin_token), json={})
        other = _second_tenant_admin_token()
        resp = await client.get(BASE, headers=auth_header(other))
        assert resp.status_code == 200, resp.text
        assert resp.json()["apps"] == []


class TestUpdateStatus:
    async def test_dismiss_app(self, client: AsyncClient, tenant_admin_token: str) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app_id = imported.json()["apps"][0]["id"]
        resp = await client.patch(
            f"{BASE}/{app_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "dismissed"

    async def test_status_survives_reimport(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app_id = imported.json()["apps"][0]["id"]
        await client.patch(
            f"{BASE}/{app_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        await client.post(f"{BASE}/import", headers=auth_header(tenant_admin_token), json={})
        listed = await client.get(BASE, headers=auth_header(tenant_admin_token))
        app = next(a for a in listed.json()["apps"] if a["id"] == app_id)
        assert app["status"] == "dismissed"

    async def test_update_unknown_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.patch(
            f"{BASE}/{uuid.uuid4()}",
            headers=auth_header(tenant_admin_token),
            json={"status": "dismissed"},
        )
        assert resp.status_code == 404, resp.text


class TestAccept:
    async def test_accept_creates_saas_vendor(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app = imported.json()["apps"][0]
        resp = await client.post(
            f"{BASE}/{app['id']}/accept",
            headers=auth_header(tenant_admin_token),
            json={"department": "IT"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["app"]["status"] == "accepted"
        assert body["app"]["promoted_vendor_id"] == body["vendor_id"]

        vendor_resp = await client.get(
            f"/api/v1/saas-vendor-ai/{body['vendor_id']}", headers=auth_header(tenant_admin_token)
        )
        assert vendor_resp.status_code == 200, vendor_resp.text
        vendor = vendor_resp.json()
        assert vendor["name"] == app["name"]
        assert vendor["department"] == "IT"
        assert vendor["risk_score"] == app["risk_score"]

    async def test_accept_defaults_department_to_unknown(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app = imported.json()["apps"][0]
        resp = await client.post(
            f"{BASE}/{app['id']}/accept", headers=auth_header(tenant_admin_token), json={}
        )
        assert resp.status_code == 200, resp.text
        vendor_resp = await client.get(
            f"/api/v1/saas-vendor-ai/{resp.json()['vendor_id']}",
            headers=auth_header(tenant_admin_token),
        )
        assert vendor_resp.json()["department"] == "Unknown"

    async def test_accept_unknown_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/{uuid.uuid4()}/accept", headers=auth_header(tenant_admin_token), json={}
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_accept(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app_id = imported.json()["apps"][0]["id"]
        resp = await client.post(
            f"{BASE}/{app_id}/accept", headers=auth_header(viewer_token), json={}
        )
        assert resp.status_code == 403, resp.text

    async def test_cannot_accept_other_tenants_app(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        imported = await client.post(
            f"{BASE}/import", headers=auth_header(tenant_admin_token), json={}
        )
        app_id = imported.json()["apps"][0]["id"]
        other = _second_tenant_admin_token()
        resp = await client.post(f"{BASE}/{app_id}/accept", headers=auth_header(other), json={})
        assert resp.status_code == 404, resp.text
