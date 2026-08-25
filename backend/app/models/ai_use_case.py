"""AI Use Case — business use cases linked to AI assets."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin

# JSONB on PostgreSQL, generic JSON on the sqlite test harness — the
# migrations create these as JSONB, so the model must say so too.
from app.models.developer import JSONColumn


class AIUseCase(GRCBase, OrgScopedMixin, TestDataMixin):
    __tablename__ = "ai_use_cases"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.ai_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    business_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    data_classification: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # Stored as generic JSON for sqlite test compatibility (migrations use JSONB on Postgres).
    model_ids: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)
    risk_ids: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)

    status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    discovered_via: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    created_at_in_use: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp captured from discovery/registry source",
    )
