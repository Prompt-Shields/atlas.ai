"""Dispatch event and Outbox models — relational (non-vector) tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin


class DispatchEvent(GRCBase, TenantScopedMixin, TestDataMixin):
    """Stores dispatched correlation results per tenant.

    Table: grc.dispatch_events
    This is a RELATIONAL table (non-vector) for events/dispatch tracking.
    """

    __tablename__ = "dispatch_events"

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.correlation_action_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.organisations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="correlation_created, action_plan_updated, risk_escalated",
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON payload of the event")
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    delivered: Mapped[bool] = mapped_column(default=False, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class OutboxMessage(GRCBase, TenantScopedMixin):
    """Outbox table for durable event delivery.

    Table: grc.outbox_messages
    Relational table — ensures at-least-once delivery of real-time events.
    """

    __tablename__ = "outbox_messages"

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
        comment="pending, processing, delivered, failed, dead_letter",
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
