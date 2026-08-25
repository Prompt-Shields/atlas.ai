"""#235 · Integration tests for /api/v1/agent-control.

Covers:
  - list: control-tower view derives health from last_seen_at, lifecycle from
    discovery status, excludes dismissed agents
  - pause / quarantine: idempotent toggle, feed (audit log) event per action
  - toggle-guardrail: flips guardrail_enabled
  - tenant isolation, RBAC (Viewer reads; OrgAdmin+ acts), 404 on unknown agent
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.jwt import create_access_token
from app.models.agent_discovery import AgentCloudProvider, AgentDiscoveryStatus, DiscoveredAgent
from app.models.audit_log import AuditLog
from tests.conftest import (
    TEST_TENANT_ID,
    TestSessionLocal,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/agent-control"

SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")
SECOND_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")


def _second_tenant_admin_token() -> str:
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000098"),
        email="other-control@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


def _agent(**kw: object) -> DiscoveredAgent:
    defaults: dict[str, object] = {
        "tenant_id": TEST_TENANT_ID,
        "provider": AgentCloudProvider.aws_bedrock_agentcore,
        "external_id": f"arn:aws:bedrock-agentcore:{uuid.uuid4()}",
        "name": "checkout-agent",
        "registry": "Bedrock AgentCore",
        "status": AgentDiscoveryStatus.approved,
        "last_seen_at": datetime.now(UTC),
    }
    defaults.update(kw)
    return DiscoveredAgent(**defaults)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def approved_agent(setup_database) -> DiscoveredAgent:
    async with TestSessionLocal() as session:
        agent = _agent()
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


class TestList:
    async def test_health_and_lifecycle_derived(
        self, client: AsyncClient, tenant_admin_token: str, setup_database
    ) -> None:
        async with TestSessionLocal() as session:
            session.add_all(
                [
                    _agent(name="fresh", last_seen_at=datetime.now(UTC)),
                    _agent(
                        name="stale",
                        last_seen_at=datetime.now(UTC) - timedelta(days=2),
                    ),
                    _agent(
                        name="pending-agent",
                        status=AgentDiscoveryStatus.pending,
                    ),
                    _agent(
                        name="dismissed-agent",
                        status=AgentDiscoveryStatus.dismissed,
                    ),
                ]
            )
            await session.commit()

        resp = await client.get(BASE, headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200, resp.text
        agents = {a["name"]: a for a in resp.json()["agents"]}

        assert "dismissed-agent" not in agents
        assert agents["fresh"]["health"] == "healthy"
        assert agents["fresh"]["lifecycle"] == "active"
        assert agents["stale"]["health"] == "unhealthy"
        assert agents["pending-agent"]["lifecycle"] == "provisioning"
        assert all(a["guardrail_enabled"] is True for a in agents.values())
        assert all(a["last_action"] is None for a in agents.values())

    async def test_tenant_isolation(
        self, client: AsyncClient, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.get(BASE, headers=auth_header(_second_tenant_admin_token()))
        assert resp.status_code == 200, resp.text
        assert resp.json()["agents"] == []


class TestAction:
    async def test_pause_then_resume(
        self, client: AsyncClient, tenant_admin_token: str, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "pause"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["lifecycle"] == "paused"
        assert body["last_action"] == "pause"

        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "pause"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.json()["lifecycle"] == "active"

    async def test_quarantine_toggle(
        self, client: AsyncClient, tenant_admin_token: str, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "quarantine"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.json()["lifecycle"] == "quarantined"

    async def test_toggle_guardrail(
        self, client: AsyncClient, tenant_admin_token: str, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "toggle-guardrail"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.json()["guardrail_enabled"] is False

        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "toggle-guardrail"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.json()["guardrail_enabled"] is True

    async def test_action_writes_audit_event(
        self, client: AsyncClient, tenant_admin_token: str, approved_agent: DiscoveredAgent
    ) -> None:
        await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "pause"},
            headers=auth_header(tenant_admin_token),
        )
        async with TestSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.event_type == "agent_control.action")
            )
            entries = result.scalars().all()
        assert len(entries) == 1
        assert entries[0].action == "pause"
        assert entries[0].resource_id == str(approved_agent.id)
        assert entries[0].tenant_id == TEST_TENANT_ID

    async def test_viewer_cannot_act(
        self, client: AsyncClient, viewer_token: str, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "pause"},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403, resp.text

    async def test_unknown_agent_404(
        self, client: AsyncClient, tenant_admin_token: str, setup_database
    ) -> None:
        resp = await client.post(
            f"{BASE}/{uuid.uuid4()}/action",
            json={"action": "pause"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 404, resp.text

    async def test_cross_tenant_agent_404(
        self, client: AsyncClient, approved_agent: DiscoveredAgent
    ) -> None:
        resp = await client.post(
            f"{BASE}/{approved_agent.id}/action",
            json={"action": "pause"},
            headers=auth_header(_second_tenant_admin_token()),
        )
        assert resp.status_code == 404, resp.text


@pytest_asyncio.fixture(autouse=True)
async def _seed_principals(seeded_principals) -> None:
    """These endpoints persist the caller's user id into an FK column."""
