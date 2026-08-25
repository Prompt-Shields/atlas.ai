"""DeviceDirective — one targeted instruction for a specific EnrolledDevice.
Append-only; status advances via delivery/ACK. Phase 2 uses kind="nudge" only.

payload is a JSON blob validated per-kind at the schema layer (free text is
length-limited + sanitized there). See the device-targeted policy spec §2.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin
from app.models.developer import JSONColumn  # same JSON type prompt_event uses


class DeviceDirective(GRCBase, TenantScopedMixin):
    __tablename__ = "device_directives"
    __table_args__ = (
        Index("ix_grc_device_directives_device_status", "device_id", "status"),
        Index("ix_grc_device_directives_tenant_created", "tenant_id", "created_at"),
        {"schema": "grc"},
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.enrolled_devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
