"""DirectiveAck — append-only record of a device acknowledging a directive.
One row per ack; an audit log, not a status column. See device-targeted policy
spec §Data Model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class DirectiveAck(GRCBase, TenantScopedMixin):
    __tablename__ = "directive_acks"
    __table_args__ = (
        Index("ix_grc_directive_acks_directive", "directive_id"),
        {"schema": "grc"},
    )

    directive_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.device_directives.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # shown|accepted|applied|rejected
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
