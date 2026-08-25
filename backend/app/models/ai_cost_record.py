"""AI cost ledger — one daily-grain row per (integration, day, cost subject)."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin

# JSONB on PostgreSQL (GIN-indexable), falls back to JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class CostProvider(str, enum.Enum):
    anthropic = "anthropic"
    openai = "openai"
    cursor = "cursor"
    github_copilot = "github_copilot"
    vercel = "vercel"


class CostKind(str, enum.Enum):
    metered_usage = "metered_usage"
    seat_subscription = "seat_subscription"
    infra = "infra"


class CostSubjectKind(str, enum.Enum):
    model = "model"
    member = "member"
    sku = "sku"
    other = "other"


class CostSource(str, enum.Enum):
    vendor_reported = "vendor_reported"
    derived_tokens = "derived_tokens"
    derived_seats = "derived_seats"


class AICostRecord(GRCBase, TenantScopedMixin):
    """Daily-grain cost row. Upserted on the idempotent key below."""

    __tablename__ = "ai_cost_records"
    __table_args__ = (
        Index("ix_grc_ai_cost_records_tenant_date", "tenant_id", "usage_date"),
        Index(
            "ix_grc_ai_cost_records_tenant_provider_date",
            "tenant_id",
            "provider",
            "usage_date",
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "usage_date",
            "cost_kind",
            "subject_kind",
            "subject_ref",
            name="uq_grc_ai_cost_record_grain",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[CostProvider] = mapped_column(
        Enum(CostProvider, schema="grc", name="cost_provider"),
        nullable=False,
        index=True,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cost_kind: Mapped[CostKind] = mapped_column(
        Enum(CostKind, schema="grc", name="cost_kind"),
        nullable=False,
    )
    subject_kind: Mapped[CostSubjectKind] = mapped_column(
        Enum(CostSubjectKind, schema="grc", name="cost_subject_kind"),
        nullable=False,
        default=CostSubjectKind.other,
    )
    # Never NULL — "" when the vendor gives no subject, so the unique key stays total.
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tokens_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    cost_source: Mapped[CostSource] = mapped_column(
        Enum(CostSource, schema="grc", name="cost_source"),
        nullable=False,
    )
    is_provisional: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
