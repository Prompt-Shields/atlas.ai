"""Integration tests for /api/v1/integrations/sentinel (issue #247).

Covers:
  - connect + events both require authentication
  - connect requires OrgAdmin (Viewer rejected)
  - events returns connected=False until the tenant connects
  - connect stores the workspace/table/event-type mapping and events
    reflects it (filtered, newest first)
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/integrations/sentinel"


class TestAuth:
    async def test_connect_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{BASE}/connect", json={"workspace_name": "Acme SOC"})
        assert resp.status_code == 401

    async def test_events_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/events")
        assert resp.status_code == 401

    async def test_connect_rejects_viewer(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC"},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403


class TestEventsBeforeConnect:
    async def test_not_connected_returns_empty_stream(
        self, client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await client.get(f"{BASE}/events", headers=auth_header(viewer_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is False
        assert body["events"] == []


class TestConnectAndStream:
    async def test_connect_returns_connected_card(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/connect",
            json={
                "workspace_name": "Acme SOC",
                "table_name": "PromptShieldsActivity_CL",
                "enabled_event_types": ["Blocked", "BiasFlagged"],
            },
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        card = resp.json()
        assert card["status"] == "CONNECTED"
        assert card["meta"]["provider"] == "SENTINEL"
        assert card["external_id"] == "Acme SOC"

    async def test_events_after_connect_filtered_to_mapping(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json={
                "workspace_name": "Acme SOC",
                "enabled_event_types": ["Blocked"],
            },
            headers=auth_header(tenant_admin_token),
        )

        resp = await client.get(f"{BASE}/events", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["workspace_name"] == "Acme SOC"
        assert body["events"], "expected at least one seeded Blocked event"
        assert all(e["event_type"] == "Blocked" for e in body["events"])
        # Never ships the prompt body — only a hash.
        for e in body["events"]:
            assert e.get("prompt_hash")

    async def test_events_newest_first(self, client: AsyncClient, tenant_admin_token: str) -> None:
        await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC"},
            headers=auth_header(tenant_admin_token),
        )
        resp = await client.get(f"{BASE}/events", headers=auth_header(tenant_admin_token))
        events = resp.json()["events"]
        timestamps = [e["time_generated"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_reconnect_updates_mapping(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC", "enabled_event_types": ["Blocked"]},
            headers=auth_header(tenant_admin_token),
        )
        resp = await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC v2", "enabled_event_types": ["Coached"]},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["external_id"] == "Acme SOC v2"

        events_resp = await client.get(f"{BASE}/events", headers=auth_header(tenant_admin_token))
        events = events_resp.json()["events"]
        assert all(e["event_type"] == "Coached" for e in events)


@pytest_asyncio.fixture(autouse=True)
async def _seed_principals(seeded_principals) -> None:
    """These endpoints persist the caller's user id into an FK column."""
