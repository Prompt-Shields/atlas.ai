"""ManagedDevice — unified MDM device table.

One row per device pulled from any of the four MDM syncs (Intune,
Jamf, Kandji, JumpCloud). `integration_id` carries the provider
identity; the columns are vendor-neutral.

This is the real model after PR #65's forward-compat alias and the
v0.5 rename migration (018). Older code may still import
`IntuneDevice` from `microsoft_sync_outputs` — that name is now a
backwards-compat alias for `ManagedDevice` and will be removed after
one release.

Column rationale (see docs/design/managed-device-unification.md):
  enrolled_user_email      vendor-neutral name for what Intune calls
                           UPN, Kandji calls user_email, Jamf calls
                           username (when email-shaped), JumpCloud
                           calls email. Stored lowercased on write.
  last_sync_to_mdm_at      when the MDM last saw the device (not
                           when atlas last synced the row — that's
                           last_synced_at).
  compliance_state         vendor-specific values preserved as
                           free-form string; unification is a v0.6
                           design exercise.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin


class ManagedDevice(GRCBase, TenantScopedMixin, TestDataMixin):
    """A managed device, written by any of the 4 MDM syncs."""

    __tablename__ = "managed_devices"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_device_id",
            name="uq_grc_intune_devices_tenant_integration_external",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operating_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    compliance_state: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "Vendor-specific compliance state. Intune: "
            "compliant | noncompliant | inGracePeriod | unknown. "
            "Jamf/Kandji/JumpCloud use their own sets — normalised to "
            "the same string column for downstream filtering."
        ),
    )
    enrolled_user_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    last_sync_to_mdm_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ManagedDevice"]
