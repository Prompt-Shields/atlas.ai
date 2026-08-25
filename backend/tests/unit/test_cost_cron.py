"""Tests for the all-tenant cron sweep (``POST /api/v1/cost/sync``).

The endpoint authenticates with a shared secret (``X-Cron-Secret`` header
compared via ``hmac.compare_digest``), then enumerates every tenant, sets that
tenant's GUC, loads its CONNECTED cost integrations (RLS + explicit tenant
filter), and syncs each one.

DUAL-ENGINE PITFALL: the endpoint opens ``get_standalone_session()``, which
builds its session from the *app* engine — not conftest's ``TestSessionLocal``.
On SQLite those are separate in-memory connections, so the test's seeded rows
would be invisible to the handler. We therefore patch the router's
``get_standalone_session`` to yield the conftest test session (where we seeded
the rows), and patch ``set_tenant_guc`` / ``sync_integration`` with mocks so we
can assert orchestration + per-tenant GUC handling without a live connector.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.config import get_settings
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.tenant import Tenant
from app.services.cost.sync_service import SyncResult
from tests.conftest import TestSessionLocal

pytestmark = [pytest.mark.unit]

CRON_SECRET = "s3cr3t-cron-token"

# Two distinct, non-zero tenant ids (avoid the SQLite UUID affinity trap).
TENANT_A = uuid.UUID("c0571111-000a-4000-8000-000000000010")
TENANT_B = uuid.UUID("c0571111-000b-4000-8000-000000000010")

SINCE = date(2026, 6, 15)
UNTIL = date(2026, 6, 17)


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    """Default to a configured cron secret; individual tests override."""
    settings = get_settings()
    monkeypatch.setattr(settings, "cost_sync_cron_secret", CRON_SECRET)


@pytest_asyncio.fixture
async def cost_integrations(setup_database):
    """Seed two tenants + their cost integrations; return the synced ids.

    Tenant A has TWO CONNECTED cost integrations (ANTHROPIC + CURSOR) — this
    is the case that exposes the transaction-local-GUC bug: each sync commits
    and clears the GUC, so the 2nd integration must have it re-set. Tenant B
    has one CONNECTED OPENAI (cost) + one CONNECTED SLACK (non-cost, ignored)
    + one NOT_CONNECTED CURSOR (idle, ignored). The sweep must sync exactly
    A's ANTHROPIC, A's CURSOR, and B's OPENAI.
    """
    async with TestSessionLocal() as session:
        tenant_a = Tenant(id=TENANT_A, name="Tenant A", slug="tenant-a")
        tenant_b = Tenant(id=TENANT_B, name="Tenant B", slug="tenant-b")

        a1 = Integration(
            tenant_id=TENANT_A,
            provider=IntegrationProvider.ANTHROPIC,
            status=IntegrationStatus.CONNECTED,
            display_name="Anthropic A",
        )
        a2 = Integration(
            tenant_id=TENANT_A,
            provider=IntegrationProvider.CURSOR,
            status=IntegrationStatus.CONNECTED,
            display_name="Cursor A",
        )
        b = Integration(
            tenant_id=TENANT_B,
            provider=IntegrationProvider.OPENAI,
            status=IntegrationStatus.CONNECTED,
            display_name="OpenAI B",
        )
        # A non-cost CONNECTED integration that must be ignored.
        slack = Integration(
            tenant_id=TENANT_B,
            provider=IntegrationProvider.SLACK,
            status=IntegrationStatus.CONNECTED,
            display_name="Slack B",
        )
        # A cost integration that is NOT connected — must be ignored.
        idle = Integration(
            tenant_id=TENANT_B,
            provider=IntegrationProvider.CURSOR,
            status=IntegrationStatus.NOT_CONNECTED,
            display_name="Cursor idle",
        )
        session.add_all([tenant_a, tenant_b, a1, a2, b, slack, idle])
        await session.commit()
        return a1.id, a2.id, b.id


def _patch_session(monkeypatch):
    """Patch the router's get_standalone_session to yield a test session."""

    @asynccontextmanager
    async def _fake_session():
        async with TestSessionLocal() as session:
            yield session

    monkeypatch.setattr("app.routers.cost.get_standalone_session", _fake_session)


# ── 1. Secret not configured → 503, no work ─────────────────────────────────


async def test_missing_secret_config_503(client, monkeypatch, setup_database):
    settings = get_settings()
    monkeypatch.setattr(settings, "cost_sync_cron_secret", None)

    mock_sync = AsyncMock()
    mock_guc = AsyncMock()
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)
    monkeypatch.setattr("app.routers.cost.set_tenant_guc", mock_guc)

    resp = await client.post("/api/v1/cost/sync", headers={"X-Cron-Secret": "whatever"})

    assert resp.status_code == 503, resp.text
    mock_sync.assert_not_awaited()
    mock_guc.assert_not_awaited()


# ── 2. Wrong / missing secret → 401 ─────────────────────────────────────────


async def test_wrong_secret_401(client, monkeypatch, setup_database):
    mock_sync = AsyncMock()
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)

    resp = await client.post("/api/v1/cost/sync", headers={"X-Cron-Secret": "nope"})

    assert resp.status_code == 401, resp.text
    mock_sync.assert_not_awaited()


async def test_missing_secret_header_401(client, monkeypatch, setup_database):
    mock_sync = AsyncMock()
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)

    resp = await client.post("/api/v1/cost/sync")

    assert resp.status_code == 401, resp.text
    mock_sync.assert_not_awaited()


# ── 3. Correct secret → all-tenant sweep ────────────────────────────────────


async def test_correct_secret_syncs_all_tenants(client, monkeypatch, cost_integrations):
    id_a1, id_a2, id_b = cost_integrations
    _patch_session(monkeypatch)

    # Record the interleaved order of GUC-set vs sync calls so we can assert
    # the GUC is (re)set with the right tenant immediately before each sync —
    # the invariant that guards the transaction-local-GUC bug.
    events: list[tuple[str, uuid.UUID]] = []

    async def _guc(db, tid):
        events.append(("guc", tid))

    async def _sync(db, integration, since, until):
        events.append(("sync", integration.tenant_id))
        return SyncResult(
            records_upserted=3,
            since=since,
            until=until,
            status=IntegrationStatus.CONNECTED,
            error=None,
        )

    mock_sync = AsyncMock(side_effect=_sync)
    mock_guc = AsyncMock(side_effect=_guc)
    monkeypatch.setattr("app.routers.cost.sync_integration", mock_sync)
    monkeypatch.setattr("app.routers.cost.set_tenant_guc", mock_guc)

    resp = await client.post("/api/v1/cost/sync", headers={"X-Cron-Secret": CRON_SECRET})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Exactly the 3 cost+CONNECTED integrations (Slack + idle Cursor excluded).
    assert body["integrations_synced"] == 3
    returned_ids = {item["integration_id"] for item in body["results"]}
    assert returned_ids == {str(id_a1), str(id_a2), str(id_b)}
    for item in body["results"]:
        assert item["status"] == "CONNECTED"
        assert item["records_upserted"] == 3
        assert item["error"] is None

    assert mock_sync.await_count == 3

    # The core invariant: every sync is immediately preceded by a GUC-set for
    # the SAME tenant. This fails if the GUC is only set once per tenant (the
    # 2nd integration of Tenant A would then be preceded by no fresh GUC-set).
    last_guc: uuid.UUID | None = None
    syncs_checked = 0
    for kind, tid in events:
        if kind == "guc":
            last_guc = tid
        else:  # sync
            assert last_guc == tid, (
                f"sync for tenant {tid} not immediately preceded by its GUC-set "
                f"(last GUC was for {last_guc})"
            )
            syncs_checked += 1
    assert syncs_checked == 3
