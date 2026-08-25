"""Tests for the cost sync orchestrator (``app.services.cost.sync_service``).

Runs on the default SQLite unit suite. We register a FAKE async connector
under ``CostProvider.anthropic`` to control connector output, seed a real
``Integration`` row via ``TestSessionLocal``, and assert idempotent upsert,
error handling, and provider mapping.

SQLite UUID trap: all-zero UUIDs get stored as integers by SQLite type
affinity and crash UUID result processing. We use non-zero UUIDs here.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.services.cost import registry, sync_service
from app.services.cost.base import CostRecord
from tests.conftest import TestSessionLocal

pytestmark = [pytest.mark.unit]

# Non-zero UUIDs (avoid SQLite integer-affinity trap)
COST_TENANT_ID = uuid.UUID("c0571111-0001-4000-8000-000000000001")

SINCE = date(2026, 6, 1)
UNTIL = date(2026, 6, 2)


def _record(usage_date: date, subject_ref: str, cost_usd: str) -> CostRecord:
    return {
        "usage_date": usage_date,
        "cost_kind": CostKind.metered_usage,
        "subject_kind": CostSubjectKind.model,
        "subject_ref": subject_ref,
        "tokens_in": 100,
        "tokens_out": 50,
        "seats": None,
        "quantity": None,
        "cost_usd": Decimal(cost_usd),
        "cost_source": CostSource.vendor_reported,
        "is_provisional": False,
        "raw_metadata": {"k": "v"},
    }


class _FakeConnector:
    """Async connector returning a fixed list (or raising)."""

    provider: CostProvider = CostProvider.anthropic

    def __init__(
        self, records: list[CostRecord] | None = None, exc: Exception | None = None
    ) -> None:
        self._records = records or []
        self._exc = exc

    async def fetch_cost(self, integration, since, until):  # type: ignore[no-untyped-def]
        if self._exc is not None:
            raise self._exc
        return self._records


@pytest_asyncio.fixture
async def integration(setup_database):
    """Seed a CONNECTED-eligible Integration row, return its id."""
    async with TestSessionLocal() as session:
        row = Integration(
            tenant_id=COST_TENANT_ID,
            provider=IntegrationProvider.ANTHROPIC,
            status=IntegrationStatus.NOT_CONNECTED,
            display_name="Anthropic (test)",
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest_asyncio.fixture(autouse=True)
def _restore_registry():
    """Restore whatever connector was registered for anthropic afterwards."""
    saved = registry.get_connector(CostProvider.anthropic)
    yield
    registry.register(saved)


async def _row_count(session) -> int:  # type: ignore[no-untyped-def]
    return (await session.execute(select(func.count()).select_from(AICostRecord))).scalar_one()


# ── 1. Success + idempotency ────────────────────────────────────────────────


async def test_success_then_idempotent_update(integration):
    integration_id = integration

    registry.register(
        _FakeConnector(
            records=[
                _record(SINCE, "claude-opus", "1.50"),
                _record(SINCE, "claude-sonnet", "0.25"),
            ]
        )
    )

    async with TestSessionLocal() as session:
        row = (
            await session.execute(select(Integration).where(Integration.id == integration_id))
        ).scalar_one()
        result = await sync_service.sync_integration(session, row, SINCE, UNTIL)

        assert result.records_upserted == 2
        assert result.status == IntegrationStatus.CONNECTED
        assert result.error is None
        assert await _row_count(session) == 2
        assert row.status == IntegrationStatus.CONNECTED
        assert row.last_error is None

    # Re-sync: one of the same grain keys with a new cost → UPDATE, not insert.
    registry.register(_FakeConnector(records=[_record(SINCE, "claude-opus", "9.99")]))

    async with TestSessionLocal() as session:
        row = (
            await session.execute(select(Integration).where(Integration.id == integration_id))
        ).scalar_one()
        result = await sync_service.sync_integration(session, row, SINCE, UNTIL)

        assert result.records_upserted == 1
        assert await _row_count(session) == 2  # still 2 rows, no duplicate

        updated = (
            await session.execute(
                select(AICostRecord).where(
                    AICostRecord.subject_ref == "claude-opus",
                    AICostRecord.usage_date == SINCE,
                )
            )
        ).scalar_one()
        assert updated.cost_usd == Decimal("9.99")


# ── 2. Error path ───────────────────────────────────────────────────────────


async def test_error_path_truncates_and_writes_nothing(integration):
    integration_id = integration
    long_msg = "boom " * 200  # >500 chars
    registry.register(_FakeConnector(exc=RuntimeError(long_msg)))

    async with TestSessionLocal() as session:
        row = (
            await session.execute(select(Integration).where(Integration.id == integration_id))
        ).scalar_one()
        result = await sync_service.sync_integration(session, row, SINCE, UNTIL)

        assert result.status == IntegrationStatus.ERROR
        assert result.records_upserted == 0
        assert result.error is not None
        assert len(result.error) <= 500
        assert row.status == IntegrationStatus.ERROR
        assert row.last_error is not None
        assert len(row.last_error) <= 500
        assert await _row_count(session) == 0


# ── 3. Provider mapping ─────────────────────────────────────────────────────


def test_cost_provider_for_anthropic():
    integ = Integration(
        tenant_id=COST_TENANT_ID,
        provider=IntegrationProvider.ANTHROPIC,
        status=IntegrationStatus.NOT_CONNECTED,
        display_name="x",
    )
    assert sync_service._cost_provider_for(integ) == CostProvider.anthropic


def test_cost_provider_for_non_cost_provider_raises():
    integ = Integration(
        tenant_id=COST_TENANT_ID,
        provider=IntegrationProvider.SLACK,
        status=IntegrationStatus.NOT_CONNECTED,
        display_name="x",
    )
    with pytest.raises(ValueError, match="not a cost provider"):
        sync_service._cost_provider_for(integ)
