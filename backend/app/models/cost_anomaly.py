"""CostAnomaly — a day whose AI spend departed sharply from its own baseline.

Slice 4 gave the ledger a monthly ceiling. A ceiling is the right instrument for
"are we overspending on AI this month?" and the wrong one for the failure this
product actually exists to catch: a runaway agent, a leaked key, a loop that
retries forever. Those burn a month's budget in a day, and a monthly budget
notices on the day the budget is gone — which is far too late to be a control.

So this is the other half. A budget compares spend to a number a human chose;
an anomaly compares spend to what *that tenant, that provider* normally does.

Why a table rather than computing it on read:

  * **Alerting needs memory.** Without a row, every cron run re-detects the same
    spike and re-mails it, which is the daily-repeat failure slice 4 already
    established as worse than silence.
  * **Acknowledgement is the point.** "We know, it was the migration backfill"
    is information the next detection run should respect. A recomputed view has
    nowhere to put it, so the same explained spike nags forever.

The detection thresholds live in `services/cost/anomalies.py`, not here: they
are policy and will be tuned, while this is the record of what was found.
"""

from __future__ import annotations

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


class CostAnomaly(GRCBase, TenantScopedMixin):
    """One detected spend spike, for one scope, on one day."""

    __tablename__ = "cost_anomalies"
    __table_args__ = (
        # One anomaly per (tenant, provider, day). Re-running detection must
        # update the existing row rather than stack duplicates — the cron runs
        # daily and re-examines a trailing window, so without this a single
        # spike would accumulate a row per run and alert on each.
        UniqueConstraint(
            "tenant_id",
            "provider",
            "usage_date",
            name="uq_grc_cost_anomalies_scope_day",
        ),
        # Declared here as well as in the migration: `alembic check` compares
        # models to the database, so a migration-only index reads as drift and
        # the next autogenerate offers to drop it. Slice 4 learned this.
        Index(
            "uq_grc_cost_anomalies_overall_day",
            "tenant_id",
            "usage_date",
            unique=True,
            postgresql_where=text("provider IS NULL"),
        ),
        {"schema": "grc"},
    )

    # NULL means the anomaly is in tenant-wide spend rather than one provider.
    # Both are worth detecting: a single provider doubling is a provider
    # problem, while every provider drifting up together is an organisation
    # one, and neither shows up reliably in the other's series.
    provider: Mapped[CostProvider | None] = mapped_column(
        Enum(CostProvider, schema="grc", name="cost_provider"),
        nullable=True,
    )

    usage_date: Mapped[date] = mapped_column(nullable=False, index=True)

    # What was actually spent that day.
    observed_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # The median of the trailing window. Median rather than mean on purpose —
    # see the module docstring in services/cost/anomalies.py: one earlier spike
    # in the window would drag a mean upward far enough to hide the next one.
    baseline_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # observed / baseline, stored so the UI and the alert agree on the figure
    # rather than each recomputing it from rounded inputs.
    ratio: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # How many days of history the baseline was drawn from. Kept because a
    # ratio from a 21-day baseline and one from the 7-day minimum are not
    # equally trustworthy, and the reader deserves to see which they have.
    baseline_days: Mapped[int] = mapped_column(nullable=False)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Set when someone says "we know why". Acknowledged anomalies stay in the
    # table as history but never alert again, so an explained spike does not
    # keep interrupting people who already explained it.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Whether the alert for this row has gone out. Separate from detected_at
    # because detection and delivery can fail independently: stamping on
    # detection would silence a spike whose mail never sent.
    alerted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
