"""AI inventory aggregate — multi-dimensional slice of the use-case registry.

Powers the /api/v1/ai-inventory/aggregate endpoint and the dashboard
inventory stats card.

Returns counts grouped along the dimensions the IT lead actually
wants to see when answering Øystein priority #2 ("map existing
usage"):

  - by_status        DRAFT / REVIEW / ACTIVE / RETIRED
  - by_risk_tier     LOW / MEDIUM / HIGH
  - by_source        BOT / FORM / SHADOW_PROMOTE / IMPORT
  - by_department    top N (department names are free text)
  - by_tool          top N (tool names are free text)

Plus a small handful of "things the IT lead needs to act on":

  - unowned_count           use cases with owner_user_id IS NULL
  - active_high_risk_count  ACTIVE + HIGH risk_tier — the hot list

All counts are tenant-scoped (SUPER_ADMIN can pass tenant_id=None to
get global counts, but that's a console feature, not a customer one).

This service is intentionally read-only and stateless — it composes
on top of the existing UseCase table, no separate cache or aggregate
table. The queries are small (single-table groupings) so a per-request
recompute is cheap. If aggregate latency becomes a problem at scale
we'll cache in Redis on a 60s TTL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saas_vendor import SaaSVendorProfile
from app.models.use_case import (
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)

# Top-N for the free-text dimensions (department, tool). Keeps the
# response payload bounded for tenants with thousands of departments.
TOP_N_FREE_TEXT = 10


@dataclass
class DimensionCount:
    label: str
    count: int


@dataclass
class InventoryAggregate:
    total: int = 0
    unowned_count: int = 0
    active_high_risk_count: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_risk_tier: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_department: list[DimensionCount] = field(default_factory=list)
    by_tool: list[DimensionCount] = field(default_factory=list)


async def compute_inventory_aggregate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
) -> InventoryAggregate:
    """Compute the aggregate. `tenant_id=None` → global (SUPER_ADMIN only)."""
    agg = InventoryAggregate()

    # A SaaS vendor is a use_cases row that also carries a 1:1
    # saas_vendor_profiles row (epic #187). Vendors must NOT count toward
    # AI-inventory aggregates, so exclude any use_case that has a profile via
    # a correlated NOT EXISTS anti-join (#196). Always applied, even for the
    # SUPER_ADMIN global (tenant_id=None) view.
    not_vendor = ~(
        select(SaaSVendorProfile.id).where(SaaSVendorProfile.use_case_id == UseCase.id).exists()
    )
    base_filter: tuple = (not_vendor,)
    if tenant_id is not None:
        base_filter = (not_vendor, UseCase.tenant_id == tenant_id)

    # ── by_status (+ total) ──────────────────────────────────────
    status_q = select(UseCase.status, func.count(UseCase.id)).group_by(UseCase.status)
    status_q = status_q.where(*base_filter)
    agg.by_status = {s.value: 0 for s in UseCaseStatus}
    for status_val, n in (await db.execute(status_q)).all():
        count = int(n or 0)
        # SQLAlchemy returns the enum instance for Enum columns; some
        # dialects return the raw string. Handle both.
        key = status_val.value if hasattr(status_val, "value") else str(status_val)
        agg.by_status[key] = count
        agg.total += count

    # ── by_risk_tier ─────────────────────────────────────────────
    risk_q = select(UseCase.risk_tier, func.count(UseCase.id)).group_by(UseCase.risk_tier)
    risk_q = risk_q.where(*base_filter)
    agg.by_risk_tier = {t.value: 0 for t in UseCaseRiskTier}
    for risk_val, n in (await db.execute(risk_q)).all():
        key = risk_val.value if hasattr(risk_val, "value") else str(risk_val)
        agg.by_risk_tier[key] = int(n or 0)

    # ── by_source ────────────────────────────────────────────────
    source_q = select(UseCase.source, func.count(UseCase.id)).group_by(UseCase.source)
    source_q = source_q.where(*base_filter)
    agg.by_source = {s.value: 0 for s in UseCaseSource}
    for src_val, n in (await db.execute(source_q)).all():
        key = src_val.value if hasattr(src_val, "value") else str(src_val)
        agg.by_source[key] = int(n or 0)

    # ── by_department (top N) ────────────────────────────────────
    dept_q = (
        select(UseCase.department, func.count(UseCase.id).label("n"))
        .group_by(UseCase.department)
        .order_by(func.count(UseCase.id).desc())
        .limit(TOP_N_FREE_TEXT)
    )
    dept_q = dept_q.where(*base_filter)
    agg.by_department = [
        DimensionCount(label=str(name), count=int(n or 0))
        for name, n in (await db.execute(dept_q)).all()
    ]

    # ── by_tool (top N) ──────────────────────────────────────────
    tool_q = (
        select(UseCase.tool, func.count(UseCase.id).label("n"))
        .group_by(UseCase.tool)
        .order_by(func.count(UseCase.id).desc())
        .limit(TOP_N_FREE_TEXT)
    )
    tool_q = tool_q.where(*base_filter)
    agg.by_tool = [
        DimensionCount(label=str(name), count=int(n or 0))
        for name, n in (await db.execute(tool_q)).all()
    ]

    # ── unowned_count + active_high_risk_count ───────────────────
    # Combined into one query — both are scalar aggregates.
    flags_q = select(
        func.sum(case((UseCase.owner_user_id.is_(None), 1), else_=0)).label("unowned"),
        func.sum(
            case(
                (
                    (UseCase.status == UseCaseStatus.ACTIVE)
                    & (UseCase.risk_tier == UseCaseRiskTier.HIGH),
                    1,
                ),
                else_=0,
            )
        ).label("active_high"),
    )
    flags_q = flags_q.where(*base_filter)
    row = (await db.execute(flags_q)).one_or_none()
    if row is not None:
        agg.unowned_count = int(row[0] or 0)
        agg.active_high_risk_count = int(row[1] or 0)

    return agg
