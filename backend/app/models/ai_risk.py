"""AI Risk — risks linked to AI use cases."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin

# JSONB on PostgreSQL, generic JSON on the sqlite test harness — the
# migrations create these as JSONB, so the model must say so too.
from app.models.developer import JSONColumn


class AIRisk(GRCBase, OrgScopedMixin, TestDataMixin):
    __tablename__ = "ai_risks"

    use_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.ai_use_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owasp_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nist_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    eu_ai_act_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    mitigation_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    mitigations: Mapped[dict | list | None] = mapped_column(JSONColumn, nullable=True)
