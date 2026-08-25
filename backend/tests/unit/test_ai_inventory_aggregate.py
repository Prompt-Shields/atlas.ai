"""SP-1 verification (#196): AI-inventory aggregates exclude SaaS-vendor rows.

A SaaS vendor is a `use_cases` row that also carries a 1:1
`saas_vendor_profiles` row (epic #187). `compute_inventory_aggregate` must
anti-join on the profile so vendors never inflate the AI-inventory counts —
otherwise the seeded demo vendors would silently pad every dimension.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.use_case import (
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.services.ai_inventory_aggregate import compute_inventory_aggregate
from scripts.seed_saas_vendors import SEED_VENDORS, seed_vendors
from tests.conftest import TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _add_real_use_case(db) -> UseCase:
    """One genuine (non-vendor) ACTIVE + HIGH-risk AI use-case."""
    uc = UseCase(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        title="Real AI Use Case",
        tool="Internal Copilot",
        department="Engineering",
        status=UseCaseStatus.ACTIVE,
        risk_tier=UseCaseRiskTier.HIGH,
        source=UseCaseSource.FORM,
    )
    db.add(uc)
    await db.flush()
    return uc


async def test_tenant_aggregate_excludes_vendor_rows() -> None:
    async with TestSessionLocal() as db:
        # one real use-case + the full demo vendor set (each a use_case+profile)
        await _add_real_use_case(db)
        seeded = await seed_vendors(db, TEST_TENANT_ID)
        assert seeded == len(SEED_VENDORS)
        assert seeded > 0  # guard: the seed must actually add vendor rows

        agg = await compute_inventory_aggregate(db, tenant_id=TEST_TENANT_ID)

        # Only the single real use-case is counted; every vendor is anti-joined out.
        assert agg.total == 1
        assert agg.by_status[UseCaseStatus.ACTIVE.value] == 1
        assert agg.by_risk_tier[UseCaseRiskTier.HIGH.value] == 1
        assert agg.active_high_risk_count == 1
        # tool/department dimensions reflect the real use-case, not vendors
        assert [d.label for d in agg.by_tool] == ["Internal Copilot"]


async def test_global_aggregate_also_excludes_vendors() -> None:
    async with TestSessionLocal() as db:
        await _add_real_use_case(db)
        await seed_vendors(db, TEST_TENANT_ID)

        # SUPER_ADMIN global view (tenant_id=None) must apply the anti-join too.
        agg = await compute_inventory_aggregate(db, tenant_id=None)
        assert agg.total == 1
