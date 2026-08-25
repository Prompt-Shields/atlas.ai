"""EnrolledDevice — a registered PEP device (macOS widget today; Windows /
browser later). Distinct from the MDM `ManagedDevice`; supersedes the
fingerprint-keyed `ExtensionDeviceHeartbeat` upsert. Holds the device-bound
credential (hashed) used to authenticate device-scoped calls.

The token is stored ONLY as a sha256 hash (same scheme as APIKey); the raw
token is returned once at registration and never persisted server-side.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class EnrolledDevice(GRCBase, TenantScopedMixin):
    __tablename__ = "enrolled_devices"
    __table_args__ = (
        Index("ix_grc_enrolled_devices_tenant_seen", "tenant_id", "last_seen_at"),
        Index("ix_grc_enrolled_devices_token_hash", "token_hash", unique=True),
        {"schema": "grc"},
    )

    # `id` (uuid PK) from GRCBase IS the device_id.
    user_external_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # macos|windows|browser
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    enrollment_state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
