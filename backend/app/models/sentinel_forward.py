"""Sentinel forwarder state — the delivery cursor and the dead-letter queue.

Spec §6 sets the audit guarantee the forwarder must meet: *every event either
lands in Sentinel or is visibly in the dead-letter queue — no silent loss*.
These two tables are how that guarantee is kept observable.

``SentinelForwardCursor`` is the resume point (one row per connected
integration). ``SentinelDeadLetter`` holds batches that could not be delivered,
with the full wire payload so a replay needs no re-mapping — the rows go back
out byte-identical, carrying the same ``EventId`` values, which is what makes
replay safe against a partially-accepted batch.
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
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin
from app.models.developer import JSONColumn


class SentinelDeadLetterStatus(str, enum.Enum):
    PENDING = "PENDING"  # Awaiting replay; counts against the audit guarantee.
    REPLAYED = "REPLAYED"  # Re-sent and accepted by Azure Monitor.
    DISCARDED = "DISCARDED"  # Operator judged it unsendable (e.g. schema drift).


class SentinelForwardCursor(GRCBase, TenantScopedMixin):
    """Where the forwarder got to for one Sentinel integration.

    The cursor is the ``(created_at, id)`` pair of the last prompt event with a
    *durable outcome* — forwarded, skipped for a recorded reason, or written to
    the dead-letter queue. It moves in the same transaction as those dead-letter
    rows, so a crash before the commit re-reads the window rather than losing
    it: duplicates in Sentinel are recoverable, gaps in an audit trail are not.

    A dead-lettered batch is re-delivered by replay, not by rewinding this
    cursor — rewinding would re-read the same events on every cycle and write a
    fresh duplicate dead letter each time.
    """

    __tablename__ = "sentinel_forward_cursors"
    __table_args__ = (
        UniqueConstraint("integration_id", name="uq_grc_sentinel_forward_cursors_integration"),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Resume point. Null on a fresh connect — the first run starts from
    # `backfill_from` (see sentinel_forwarder.forward_integration) rather than
    # from the beginning of time, so connecting Sentinel does not replay a
    # year of telemetry into the customer's billed workspace.
    last_event_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    events_forwarded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batches_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batches_dead_lettered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SentinelDeadLetter(GRCBase, TenantScopedMixin):
    """One batch that could not be delivered, kept with its full payload.

    Durability target is the spec's "last 7 days minimum"; rows are retained
    until an operator replays or discards them, so retention is a deliberate
    operator action rather than a sweep.
    """

    __tablename__ = "sentinel_dead_letters"
    __table_args__ = (
        Index(
            "ix_grc_sentinel_dead_letters_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[SentinelDeadLetterStatus] = mapped_column(
        Enum(SentinelDeadLetterStatus, schema="grc", name="sentinel_dead_letter_status"),
        nullable=False,
        default=SentinelDeadLetterStatus.PENDING,
    )

    # Short classification (`http_403`, `schema_invalid`, `exhausted_retries`,
    # …) — what an operator triages on. `error_detail` carries the message.
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # The serialised wire rows, exactly as they would have been POSTed.
    payload: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # `EventId` wire values bounding the batch — enough for an operator to
    # locate the affected window without loading the whole payload.
    first_event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
