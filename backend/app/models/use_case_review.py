"""UseCaseReview — periodic re-attestation of registered AI use cases.

Customer-driven (Øystein Endal priority #3):
  "Holde informasjonen oppdatert, f.eks. systemer får ny
   funksjonalitet, tas i bruk til andre formål eller bytter modeller"
  → Keep information up to date — e.g. systems gain new functionality,
    get used for new purposes, or switch models.

Pattern: every 90 days an automated job creates a Review row for
each Active use case. The owner (or, if none, OrgAdmin+) confirms
the registered fields still match reality. Stale reviews surface
in /dashboard/reattestation so the IT lead can see what needs
attention.

States
  DUE          waiting for the owner to attest
  REVIEWED     owner confirmed the use case is still accurate
  OVERDUE      past due_at + grace period without a review
  DISMISSED    IT lead chose to skip this round (with reason)

A new Review row is created each period; we never overwrite.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class ReviewStatus(str, enum.Enum):
    DUE = "DUE"
    REVIEWED = "REVIEWED"
    OVERDUE = "OVERDUE"
    DISMISSED = "DISMISSED"


class UseCaseReview(GRCBase, TenantScopedMixin):
    """One row per (use_case, review_period). Audit-trail preserving."""

    __tablename__ = "use_case_reviews"
    __table_args__ = (
        Index("ix_grc_use_case_reviews_last_reminder_sent_at", "last_reminder_sent_at"),
        {"schema": "grc"},
    )

    use_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.use_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Cadence: defaults to a 90-day cycle. The enqueue service sets
    # `due_at` to a fixed offset from the use case's last review (or
    # registration date if never reviewed).
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, schema="grc"),
        nullable=False,
        default=ReviewStatus.DUE,
        index=True,
    )

    # Set when status transitions to REVIEWED or DISMISSED.
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Free-form notes the reviewer leaves:
    #   "still in active use, no scope change"
    #   "switched from GPT-3.5 to GPT-4o on 2026-04-15"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # If the reviewer flagged a material change (model swap, new
    # data class, broader user base) → drift_observed=True so the
    # IT lead can drill in and decide whether to re-classify.
    drift_observed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Reason given when status=DISMISSED (e.g. "use case retired,
    # cleanup pending").
    dismiss_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Last time the owner was Slack-DMd about this overdue review.
    # NULL = never sent. The reminder dispatcher uses this to
    # throttle re-sends (default cooldown: 24h).
    last_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
