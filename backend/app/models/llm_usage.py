"""LLM usage tracking model for cost monitoring."""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class LLMUsageRecord(GRCBase, TenantScopedMixin):
    """Tracks every LLM API call with token counts and cost attribution.

    Table: grc.llm_usage_records
    Used for per-tenant/org/user cost monitoring and budget alerts.
    """

    __tablename__ = "llm_usage_records"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Model info
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(50), default="azure_openai", nullable=False)

    # Token counts
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    # Cost
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # Context
    operation_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="risk_analysis, correlation, embedding, etc."
    )
    request_metadata: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON metadata about the request"
    )
