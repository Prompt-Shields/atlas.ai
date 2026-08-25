"""Tests for the manual "Sync now" endpoint (``app.routers.cost``).

Runs on the default SQLite unit suite. We seed a real ``Integration`` row via
``TestSessionLocal`` and patch the router's ``sync_integration`` with an
``AsyncMock`` so we exercise the router's lookup/validation/serialisation
without touching a live connector.

SQLite UUID trap: all-zero UUIDs get stored as integers by SQLite type
affinity. We use non-zero UUIDs for resource ids.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.auth.jwt import create_access_token
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.services.cost.sync_service import SyncResult
from tests.conftest import TestSessionLocal, auth_header

pytestmark = [pytest.mark.unit]

# Non-zero UUIDs — conftest's all-zeros TEST_TENANT_ID hits SQLite's
# integer type-affinity trap on UUID result processing. The JWT tenant must
# match the seeded rows' tenant so RLS-style scoping in the router resolves.
COST_TENANT_ID = uuid.UUID("c0571111-0001-4000-8000-000000000010")
COST_USER_ID = uuid.UUID("c0571111-0001-4000-8000-000000000002")
COST_ORG_ID = uuid.UUID("c0571111-0001-4000-8000-000000000010")


@pytest.fixture
def tenant_admin_token() -> str:
    return create_access_token(
        user_id=COST_USER_ID,
        email="cost-admin@test.local",
        roles=["TENANT_ADMIN"],
        tenant_id=COST_TENANT_ID,
        org_id=COST_ORG_ID,
    )


async def _seed_integration(
    provider: IntegrationProvider = IntegrationProvider.ANTHROPIC,
) -> uuid.UUID:
    async with TestSessionLocal() as session:
        row = Integration(
            tenant_id=COST_TENANT_ID,
            provider=provider,
            status=IntegrationStatus.NOT_CONNECTED,
            display_name=f"{provider.value} (test)",
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest_asyncio.fixture
async def anthropic_integration_id(setup_database) -> uuid.UUID:
    return await _seed_integration(IntegrationProvider.ANTHROPIC)


@pytest_asyncio.fixture
async def slack_integration_id(setup_database) -> uuid.UUID:
    return await _seed_integration(IntegrationProvider.SLACK)


async def test_sync_now_returns_200_and_body(
    client, tenant_admin_token, anthropic_integration_id, monkeypatch
) -> None:
    fake_result = SyncResult(
        records_upserted=7,
        since=date(2026, 6, 15),
        until=date(2026, 6, 17),
        status=IntegrationStatus.CONNECTED,
        error=None,
    )
    mock_sync = AsyncMock(return_value=fake_result)
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)

    resp = await client.post(
        f"/api/v1/cost/integrations/{anthropic_integration_id}/sync",
        headers=auth_header(tenant_admin_token),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["records_upserted"] == 7
    assert body["since"] == "2026-06-15"
    assert body["until"] == "2026-06-17"
    assert body["status"] == "CONNECTED"
    assert body["error"] is None
    mock_sync.assert_awaited_once()


async def test_sync_now_unknown_integration_404(
    client, tenant_admin_token, setup_database, monkeypatch
) -> None:
    mock_sync = AsyncMock()
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)

    unknown_id = uuid.UUID("dead0000-0000-4000-8000-000000000001")
    resp = await client.post(
        f"/api/v1/cost/integrations/{unknown_id}/sync",
        headers=auth_header(tenant_admin_token),
    )

    assert resp.status_code == 404, resp.text
    mock_sync.assert_not_awaited()


async def test_sync_now_non_cost_provider_400(
    client, tenant_admin_token, slack_integration_id, monkeypatch
) -> None:
    mock_sync = AsyncMock()
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)

    resp = await client.post(
        f"/api/v1/cost/integrations/{slack_integration_id}/sync",
        headers=auth_header(tenant_admin_token),
    )

    assert resp.status_code == 400, resp.text
    mock_sync.assert_not_awaited()
