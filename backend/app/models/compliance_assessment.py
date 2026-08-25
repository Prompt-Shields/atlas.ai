"""Compliance assessment — framework status per AI asset."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin


class ComplianceAssessment(GRCBase, OrgScopedMixin, TestDataMixin):
    __tablename__ = "compliance_assessments"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.ai_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    framework: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="EU_AI_ACT, GDPR, NIST_AI_RMF",
        index=True,
    )
    risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    live_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
