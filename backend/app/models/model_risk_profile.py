"""Model Risk Profile — curated risk scores for model families/providers."""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin

# JSONB on PostgreSQL, generic JSON on the sqlite test harness — the
# migrations create these as JSONB, so the model must say so too.
from app.models.developer import JSONColumn


class ModelRiskProfile(GRCBase, OrgScopedMixin, TestDataMixin):
    __tablename__ = "model_risk_profiles"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    parameters: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    overall_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hallucination_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bias_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    toxicity_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    privacy_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compliance_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)

    key_risks: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)
    approved_use_cases: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)
    risk_alerts: Mapped[list[dict] | None] = mapped_column(JSONColumn, nullable=True)
