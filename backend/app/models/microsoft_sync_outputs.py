"""Output tables for Microsoft Purview + Defender CASB syncs.

Two tables here now:

  PurviewEvent       — DLP / classifier events (audit feed)
  CloudAppDetection  — Defender CASB cloud-app discovery records

Plus a backwards-compat re-export of `IntuneDevice` → `ManagedDevice`.
The device table moved to `app/models/managed_device.py` in v0.5
(see docs/design/managed-device-unification.md + migration 018).
Older code can still `from app.models.microsoft_sync_outputs import
IntuneDevice` for one more release — but please use `ManagedDevice`
from `app.models.managed_device` in new code.

All upserted by the respective worker mode. Bad rows skipped + logged,
never abort the batch.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin
from app.models.managed_device import ManagedDevice as IntuneDevice  # noqa: F401


class PurviewEvent(GRCBase, TenantScopedMixin, TestDataMixin):
    """Microsoft Purview audit / DLP event.

    Source: Graph `/security/alerts_v2` filtered to vendor='Microsoft'
    + product='Microsoft Purview', or the equivalent M365 Management
    Activity API for richer DLP context. v0.1 reads alerts_v2.
    """

    __tablename__ = "purview_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_event_id",
            name="uq_grc_purview_events_tenant_integration_external",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="dlp_match | classification_change | label_applied | …",
    )
    severity: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="low | medium | high | informational",
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_principal_name: Mapped[str | None] = mapped_column(
        String(320), nullable=True, index=True
    )
    classifier_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_payload_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original Graph payload, for audit / debugging",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CloudAppDetection(GRCBase, TenantScopedMixin, TestDataMixin):
    """Defender for Cloud Apps cloud-app discovery record.

    Source: Microsoft Graph `/security/alerts_v2` filtered to
    product='Microsoft Cloud App Security', or MCAS REST API
    `/api/v1/discovery/discovered_apps`.

    One row per (app, user) discovery. Multiple users using the same
    app → multiple rows. The Discover page aggregates.
    """

    __tablename__ = "cloud_app_detections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_detection_id",
            name="uq_grc_cad_tenant_integration_external",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_detection_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # The cloud app itself.
    app_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    app_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    app_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Defender risk score 0-10",
    )
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Who's using it.
    user_principal_name: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    event_count_30d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
