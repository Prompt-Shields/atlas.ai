"""Risk and Mitigation model — output of the risk analyzer agent."""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin


class RiskMitigation(GRCBase, OrgScopedMixin, TestDataMixin):
    """Stores risk analysis and mitigation recommendations from the SK agent.

    Table: grc.risk_mitigations
    Contains both structured risk data and optional vector embeddings.
    """

    __tablename__ = "risk_mitigations"

    # Link to source blob(s)
    blob_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.blob_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Risk data
    risk_title: Mapped[str] = mapped_column(String(500), nullable=False)
    risk_description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_category: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_severity: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="critical, high, medium, low, informational"
    )
    risk_likelihood: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="very_likely, likely, possible, unlikely, rare"
    )
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(
        Float, nullable=False, comment="Model confidence 0.0-1.0"
    )

    # Mitigation data
    mitigation_title: Mapped[str] = mapped_column(String(500), nullable=False)
    mitigation_description: Mapped[str] = mapped_column(Text, nullable=False)
    mitigation_steps: Mapped[str] = mapped_column(
        Text, nullable=False, comment="JSON array of step strings"
    )

    # Evidence/citations
    citations: Mapped[str] = mapped_column(
        Text, nullable=False, comment="JSON array of {blob_id, snippet} references"
    )

    # Vector embedding for similarity search
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    # Processing metadata
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)

    created_by_job: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Job ID that created this record"
    )
