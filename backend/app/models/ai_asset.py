"""AI Asset — deployed AI systems registered for SPM."""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin


class AIAsset(GRCBase, OrgScopedMixin, TestDataMixin):
    """Catalogue entry for an AI system in production / development."""

    __tablename__ = "ai_assets"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        comment="active, shadow, deprecated, testing",
    )
    hosting_location: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="azure, aws, gcp, on-premise",
    )
    model_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_third_party: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Risk + compliance scores (0–100)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    gdpr_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eu_ai_act_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nist_ai_rmf_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eu_ai_act_risk_tier: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="Unacceptable Risk, High-Risk, Limited Risk, Minimal Risk",
    )

    # Telemetry (populated by adapters/agents)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
