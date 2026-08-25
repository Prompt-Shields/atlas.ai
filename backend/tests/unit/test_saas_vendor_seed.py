"""Tests for the demo SaaS-vendor seed (SP-1, issue #193)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.use_case import UseCase
from app.services import saas_vendor_service
from scripts.seed_saas_vendors import SEED_VENDORS, seed_vendors
from tests.conftest import TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_seed_is_idempotent_and_marked_test_data() -> None:
    async with TestSessionLocal() as db:
        # first run creates the full set
        first = await seed_vendors(db, TEST_TENANT_ID)
        assert first == len(SEED_VENDORS)

        # second run is a no-op (idempotent by use-case title)
        second = await seed_vendors(db, TEST_TENANT_ID)
        assert second == 0

        # every seeded use-case is flagged demo/test data
        rows = (
            (await db.execute(select(UseCase).where(UseCase.tenant_id == TEST_TENANT_ID)))
            .scalars()
            .all()
        )
        assert len(rows) == len(SEED_VENDORS)
        assert all(r.is_test_data for r in rows)


async def test_seed_renders_through_vendor_service() -> None:
    async with TestSessionLocal() as db:
        await seed_vendors(db, TEST_TENANT_ID)
        vendors = await saas_vendor_service.list_vendors(db, TEST_TENANT_ID)
        assert len(vendors) == len(SEED_VENDORS)
        names = {v.name for v in vendors}
        assert "ChatGPT Enterprise" in names
        # KPI aggregate computes over the seeded set
        kpi = await saas_vendor_service.compute_kpi(db, TEST_TENANT_ID)
        assert kpi.total == len(SEED_VENDORS)
        assert kpi.high_risk >= 1  # ChatGPT Enterprise is 72
