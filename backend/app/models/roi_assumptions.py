"""RoiAssumptions — the per-tenant human-cost model behind AI ROI (slice 3).

ROI is a ratio between something we *measure* and something we *estimate*. The
measured half is the cost ledger slices 1 and 2 built: real dollars, per tenant,
tagged by provenance. The estimated half is what the AI-assisted work would have
cost in human time, and there is no way to observe that directly — it always
rests on assumptions a human supplied.

This table is where those assumptions live, and it exists because they were
previously scattered:

  - `adoption_service` hard-coded a $75/h blended rate,
  - the AI Spend page hard-coded $95/h in the browser and kept the admin's
    edits in `localStorage`,
  - and the adoption headline divided by an *assumed* $15/seat/week rather than
    the real ledger sitting next to it.

So one organisation could read three different ROI figures depending on which
page it opened, none of them shared between colleagues, and the numbers behind
the headline could be edited with no record. Assumptions that drive an
exec-facing number belong in the database, scoped to the tenant, attributed to
whoever last changed them.

Deliberately *not* a versioned history table. The audit trail for a change is
the existing `audit.audit_logs` event; duplicating it here would be a second
place to keep consistent for no extra answer.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class HoursSavedSource(str, enum.Enum):
    """Where the hours-saved figure comes from.

    Stored rather than inferred, because the honest presentation of the result
    depends on it: an ROI built on a typed-in number is a projection, and one
    built on the seeded sample pipeline is an illustration, and neither may be
    rendered the way a measured figure would be.
    """

    # Read from the adoption pipeline. Today that pipeline runs over a seeded
    # sample rather than the tenant's own telemetry, which is exactly why
    # `RoiResult.basis` distinguishes `sampled` from `measured` — this enum
    # records the *intent*, the service records what was actually available.
    adoption_pipeline = "adoption_pipeline"

    # An admin's own figure, from their own time study or estimate.
    manual = "manual"


# Defaults applied when a tenant has never set its own. Deliberately declared
# once, here, and imported everywhere else rather than re-typed — the three
# divergent constants this table replaced are what that rule is for.
DEFAULT_BLENDED_HOURLY_RATE_USD = Decimal("75.00")


class RoiAssumptions(GRCBase, TenantScopedMixin):
    """One row per tenant: the inputs an AI ROI figure rests on."""

    __tablename__ = "roi_assumptions"
    __table_args__ = (
        # One model per tenant. ROI is an organisation-level number; letting a
        # tenant hold two would mean two answers to one question.
        UniqueConstraint("tenant_id", name="uq_grc_roi_assumptions_tenant"),
        {"schema": "grc"},
    )

    # Fully-loaded cost of an hour of the time the AI is standing in for —
    # salary plus employer overhead, not take-home. USD only, matching the
    # ledger's v1 single-currency decision.
    blended_hourly_rate_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=DEFAULT_BLENDED_HOURLY_RATE_USD,
    )

    hours_saved_source: Mapped[HoursSavedSource] = mapped_column(
        Enum(HoursSavedSource, schema="grc", name="hours_saved_source"),
        nullable=False,
        default=HoursSavedSource.adoption_pipeline,
    )

    # Only consulted when `hours_saved_source` is `manual`. Nullable rather
    # than defaulted to 0: absent means "not supplied", and 0 would silently
    # report a real ROI of zero as though it had been measured.
    manual_hours_saved_per_month: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    # Who last changed the numbers behind the headline. SET NULL rather than
    # CASCADE: losing the attribution is bad, losing the assumptions because
    # someone left the company is worse.
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
