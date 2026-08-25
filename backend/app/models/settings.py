"""Tenant settings model."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase


class TenantSetting(GRCBase):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
        Index("ix_tenant_settings_tenant_id", "tenant_id"),
        {"schema": "grc"},
    )

    # Uniqueness and the lookup index are declared in __table_args__ above,
    # matching the objects migration 001 created. Repeating them here as
    # column flags would ask for a third, redundant unique index.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    research_all_data: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="If true, correlation agent analyzes all accumulated data",
    )
    llm_budget_usd: Mapped[float | None] = mapped_column(
        nullable=True, comment="Monthly LLM budget in USD"
    )
    custom_settings: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON blob for additional settings"
    )
