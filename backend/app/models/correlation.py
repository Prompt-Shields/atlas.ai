"""Correlation and Action Plan model — output of the correlation engine agent."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin


class CorrelationActionPlan(GRCBase, OrgScopedMixin, TestDataMixin):
    """Stores correlated risks with action plans from the correlation engine.

    Table: grc.correlation_action_plans
    """

    __tablename__ = "correlation_action_plans"

    # Links to risk/mitigation records (JSON array of UUIDs)
    risk_mitigation_ids: Mapped[str] = mapped_column(
        Text, nullable=False, comment="JSON array of risk_mitigation IDs that were correlated"
    )
    batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Correlation result
    correlation_title: Mapped[str] = mapped_column(String(500), nullable=False)
    correlation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="pattern, escalation, dependency, compound"
    )
    overall_risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Action plan
    action_plan_title: Mapped[str] = mapped_column(String(500), nullable=False)
    action_plan_description: Mapped[str] = mapped_column(Text, nullable=False)
    action_steps: Mapped[str] = mapped_column(
        Text, nullable=False, comment="JSON array of action step objects"
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="critical, high, medium, low"
    )
    estimated_effort: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Evidence
    citations: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, comment="LLM reasoning chain")

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default="open", nullable=False, comment="open, in_progress, resolved, dismissed"
    )

    # Vector embedding
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_job: Mapped[str | None] = mapped_column(String(100), nullable=True)
