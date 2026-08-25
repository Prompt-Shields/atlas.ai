"""Integration tests for /api/v1/pep (issue #237).

Covers:
  - template catalog is readable by any authenticated user and identical
    regardless of tenant (tenant-agnostic reference data)
  - clone creates a tenant-scoped instance in Guideline (log) mode, even
    when the template's own default_enforcement_mode is stricter
  - RBAC (any authed user reads; OrgAdmin+ writes)
  - tenant isolation on instances
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from app.models.policy_enforcement import (
    PolicyCategory,
    PolicyInstanceStatus,
    PolicySeverity,
    PolicyTemplate,
)
from tests.conftest import (
    TestSessionLocal,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/pep"

SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_TENANT_ID,
    )


async def _seed_template(**overrides: object) -> PolicyTemplate:
    base: dict[str, object] = {
        "slug": "test-owasp-prompt-injection",
        "name": "Prompt Injection Detection",
        "version": "1.0.0",
        "category": PolicyCategory.owasp_llm,
        "author": "atlas.ai",
        "severity": PolicySeverity.high,
        "description": "Detects prompt injection attempts.",
        "rationale": "OWASP LLM01.",
        "example_violation": "Ignore previous instructions.",
        "triggers": [{"stage": "input", "description": "on every prompt"}],
        "detectors": [
            {
                "id": "kw",
                "type": "keyword_list",
                "description": "d",
                "config_ref": "injection_keywords",
            }
        ],
        "actions": [{"type": "block", "description": "block it"}],
        "tunable_parameters": [
            {
                "key": "injection_keywords",
                "label": "Keywords",
                "type": "keywords",
                "default": ["ignore previous"],
                "helpText": "h",
                "level": "basic",
            },
        ],
        # Deliberately a "strict" default so we can assert clone still starts Guideline.
        "default_enforcement_mode": "block",
        "default_applies_to": {"risk_tiers": ["high"]},
        "tags": ["owasp"],
    }
    base.update(overrides)
    async with TestSessionLocal() as db:
        template = PolicyTemplate(**base)
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return template


class TestTemplateCatalog:
    async def test_list_templates_is_tenant_agnostic(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await _seed_template()
        resp_a = await client.get(f"{BASE}/templates", headers=auth_header(tenant_admin_token))
        resp_b = await client.get(
            f"{BASE}/templates", headers=auth_header(_second_tenant_admin_token())
        )
        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a.json() == resp_b.json()
        assert len(resp_a.json()) == 1
        assert resp_a.json()[0]["slug"] == "test-owasp-prompt-injection"
        assert resp_a.json()[0]["category"] == "OWASP_LLM"

    async def test_get_template_by_id(self, client: AsyncClient, tenant_admin_token: str) -> None:
        template = await _seed_template()
        resp = await client.get(
            f"{BASE}/templates/{template.id}", headers=auth_header(tenant_admin_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == str(template.id)

    async def test_get_unknown_template_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.get(
            f"{BASE}/templates/{uuid.uuid4()}", headers=auth_header(tenant_admin_token)
        )
        assert resp.status_code == 404, resp.text


class TestClone:
    async def test_clone_creates_instance_in_guideline_mode(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        template = await _seed_template()
        resp = await client.post(
            f"{BASE}/policies/{template.id}/clone",
            headers=auth_header(tenant_admin_token),
            json={},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Guideline (log) regardless of the template's stricter default ("block").
        assert body["enforcement_mode"] == "log"
        assert body["template_id"] == str(template.id)
        assert body["template_version"] == template.version
        assert body["severity"] == "high"
        assert body["status"] == PolicyInstanceStatus.active.value
        assert body["name"] == "Prompt Injection Detection (copy)"
        assert body["parameter_values"] == {"injection_keywords": ["ignore previous"]}
        assert body["applies_to"] == {"risk_tiers": ["high"]}

    async def test_clone_accepts_name_override(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        template = await _seed_template()
        resp = await client.post(
            f"{BASE}/policies/{template.id}/clone",
            headers=auth_header(tenant_admin_token),
            json={"name": "My Injection Policy"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "My Injection Policy"

    async def test_clone_unknown_template_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/policies/{uuid.uuid4()}/clone",
            headers=auth_header(tenant_admin_token),
            json={},
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_clone(self, client: AsyncClient, viewer_token: str) -> None:
        template = await _seed_template()
        resp = await client.post(
            f"{BASE}/policies/{template.id}/clone",
            headers=auth_header(viewer_token),
            json={},
        )
        assert resp.status_code == 403, resp.text


class TestPoliciesListAndIsolation:
    async def test_list_returns_cloned_instance(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        template = await _seed_template()
        await client.post(
            f"{BASE}/policies/{template.id}/clone", headers=auth_header(tenant_admin_token), json={}
        )
        resp = await client.get(f"{BASE}/policies", headers=auth_header(viewer_token))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 1

    async def test_other_tenant_cannot_see(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        template = await _seed_template()
        created = (
            await client.post(
                f"{BASE}/policies/{template.id}/clone",
                headers=auth_header(tenant_admin_token),
                json={},
            )
        ).json()

        other = _second_tenant_admin_token()
        resp = await client.get(f"{BASE}/policies", headers=auth_header(other))
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

        resp = await client.get(f"{BASE}/policies/{created['id']}", headers=auth_header(other))
        assert resp.status_code == 404, resp.text

    async def test_get_unknown_policy_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.get(
            f"{BASE}/policies/{uuid.uuid4()}", headers=auth_header(tenant_admin_token)
        )
        assert resp.status_code == 404, resp.text


class TestDirectCreate:
    async def test_org_admin_can_create(self, client: AsyncClient, tenant_admin_token: str) -> None:
        template = await _seed_template()
        resp = await client.post(
            f"{BASE}/policies",
            headers=auth_header(tenant_admin_token),
            json={
                "name": "Custom Instance",
                "template_id": str(template.id),
                "severity": "medium",
                "enforcement_mode": "flag",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Custom Instance"
        assert body["enforcement_mode"] == "flag"
        assert body["severity"] == "medium"

    async def test_create_unknown_template_is_404(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/policies",
            headers=auth_header(tenant_admin_token),
            json={"name": "x", "template_id": str(uuid.uuid4()), "severity": "medium"},
        )
        assert resp.status_code == 404, resp.text

    async def test_viewer_cannot_create(self, client: AsyncClient, viewer_token: str) -> None:
        template = await _seed_template()
        resp = await client.post(
            f"{BASE}/policies",
            headers=auth_header(viewer_token),
            json={"name": "x", "template_id": str(template.id), "severity": "medium"},
        )
        assert resp.status_code == 403, resp.text
