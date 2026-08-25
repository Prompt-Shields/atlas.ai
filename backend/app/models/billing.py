"""Billing models: TrialEvent, BillingOutbox, StripeWebhookEvent.

These tables use plain integer/string PKs (not GRCBase UUID) and live in
the grc schema, created by migration 020_billing_v0.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# JSON type — JSONB on PostgreSQL, generic JSON on SQLite (used by test suite)
JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# BigInteger PK — SQLite requires exactly INTEGER (not BIGINT) for autoincrement;
# use Integer as the SQLite variant so test-suite inserts work.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class TrialEvent(Base):
    """Daily snapshot of trial-period activity per tenant."""

    __tablename__ = "trial_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "snapshot_date", name="uq_trial_events_tenant_date"),
        Index("ix_trial_events_tenant_date", "tenant_id", "snapshot_date"),
        {"schema": "grc"},
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    users_invited: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    users_activated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompts_observed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompts_redacted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extension_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(8, 4, asdecimal=True), default=Decimal("0"), nullable=False
    )
    csm_nudge_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    converted_to_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    convert_intent_signal: Mapped[str | None] = mapped_column(String(120), nullable=True)


class BillingOutbox(Base):
    """Durable outbox for Stripe seat-sync operations with retry logic.

    Distinct from the existing OutboxMessage (app/models/dispatch.py).
    """

    __tablename__ = "billing_outbox"
    __table_args__ = (
        Index("ix_billing_outbox_pending", "succeeded_at", "next_attempt_at"),
        {"schema": "grc"},
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONColumn, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class StripeWebhookEvent(Base):
    """Idempotency log for Stripe webhook events.

    Inserted in the same DB transaction as every webhook-driven mutation
    to prevent double-processing.
    """

    __tablename__ = "stripe_webhook_events"
    __table_args__ = ({"schema": "grc"},)

    stripe_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
