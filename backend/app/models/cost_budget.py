"""CostBudget — a monthly spend ceiling per tenant, and the alert state for it.

Slices 1-3 built a ledger that answers "what did AI cost us?" accurately and a
ROI model that answers "was it worth it?" honestly. Both are questions someone
has to remember to go and ask. A budget is the first thing here that speaks
without being asked, which is the whole point: the failure mode of a cost tool
is not a wrong number, it is a correct number nobody looked at until the
invoice arrived.

Two design choices are worth stating, because both are about not lying:

**Alert state lives on the budget row.** `last_alerted_level` and
`last_alerted_period` exist so a crossing alerts *once*. A budget that emails
every day it is over sends a message that reads as urgent on day one and as
noise by day three, and a muted alert is strictly worse than no alert — it is
an alert you believe you have. Recording the level, not just a timestamp, is
what lets 80% and then 100% both notify while neither repeats.

**Scope is either the whole tenant or one provider, never both silently.** A
NULL `provider` means the tenant-wide ceiling. A budget per provider is the
common ask ("cap Cursor at $500") and it must not be confused with the
overall ceiling, so the unique constraint treats them as distinct rows and the
service never sums a provider budget into the tenant total.

Deliberately *not* modelled here: a per-day budget. The ledger's grain is daily
but nobody sets a daily AI budget, and offering one would invite alerts that
fire on a Tuesday spike that a month easily absorbs.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ai_cost_record import CostProvider
from app.models.base import GRCBase, TenantScopedMixin

# The share of a budget at which we warn but do not yet call it breached.
# One number, declared once: the ROI slice's lesson was that a constant
# duplicated across a service and a page becomes two different products.
DEFAULT_WARN_THRESHOLD_PERCENT = Decimal("80.00")


class BudgetAlertLevel(str, enum.Enum):
    """How far through a budget the spend has got.

    Ordered deliberately: `ok` < `warning` < `exceeded`. The service compares
    the level it computes against `last_alerted_level` to decide whether an
    alert is *new*, so the ordering is load-bearing, not cosmetic.
    """

    ok = "ok"
    warning = "warning"
    exceeded = "exceeded"


# Rank for "is this a worse level than the one we last told them about?".
# A dict rather than an IntEnum so the stored values stay readable strings in
# the database, where someone debugging a missed alert will be looking.
ALERT_LEVEL_RANK: dict[BudgetAlertLevel, int] = {
    BudgetAlertLevel.ok: 0,
    BudgetAlertLevel.warning: 1,
    BudgetAlertLevel.exceeded: 2,
}


class CostBudget(GRCBase, TenantScopedMixin):
    """A monthly USD ceiling, tenant-wide or scoped to one provider."""

    __tablename__ = "cost_budgets"
    __table_args__ = (
        # One budget per (tenant, provider), with NULL provider being the
        # tenant-wide row. Postgres treats NULLs as distinct in a unique
        # index, so the tenant-wide row is additionally guarded by a partial
        # unique index created in the migration — without it a tenant could
        # hold two overall budgets and get two different answers.
        UniqueConstraint("tenant_id", "provider", name="uq_grc_cost_budgets_tenant_provider"),
        # The other half of that uniqueness, and it must be declared here as
        # well as in the migration: `alembic check` compares the models to the
        # database, so an index created only by the migration reads as drift
        # and the next autogenerate would helpfully offer to drop it.
        Index(
            "uq_grc_cost_budgets_tenant_overall",
            "tenant_id",
            unique=True,
            postgresql_where=text("provider IS NULL"),
        ),
        {"schema": "grc"},
    )

    # NULL means the tenant-wide ceiling across every provider.
    provider: Mapped[CostProvider | None] = mapped_column(
        Enum(CostProvider, schema="grc", name="cost_provider"),
        nullable=True,
    )

    # The monthly ceiling. Numeric(14, 2) rather than the ledger's
    # Numeric(14, 6): a budget is a figure a human types, and six decimal
    # places on a number someone entered as "500" is false precision.
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    warn_threshold_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=DEFAULT_WARN_THRESHOLD_PERCENT,
    )

    # Whether this budget may send mail at all. Kept separate from deleting
    # the row so a tenant can silence a noisy budget without losing the
    # ceiling they agreed on.
    alerts_enabled: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
    )

    # ─── alert state ──────────────────────────────────────────────
    # The first day of the month an alert was last sent for. Storing the
    # period rather than only a timestamp is what makes the reset at a month
    # boundary explicit instead of arithmetic on `last_alerted_at`.
    last_alerted_period: Mapped[date | None] = mapped_column(nullable=True)

    last_alerted_level: Mapped[BudgetAlertLevel | None] = mapped_column(
        Enum(BudgetAlertLevel, schema="grc", name="budget_alert_level"),
        nullable=True,
    )

    last_alerted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Who set the ceiling. SET NULL for the same reason as roi_assumptions:
    # losing the attribution is bad, losing the budget because someone left
    # the company is worse.
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
