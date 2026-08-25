"""Discover · Defender for Cloud Apps import — screenshot/export intake (#243).

Ports `app/discover/defender-import/` + `lib/defender-import-data.ts` from
the ai-spm-dashboard prototype: a pasted Microsoft Defender for Cloud Apps
export is run through a seeded extractor (no real OCR in v1 — see
`services/defender_import_service.py`'s `KNOWN_APPS` catalog) that turns
matched lines into enriched candidate rows. A human then accepts a
candidate into the AI inventory (as a `saas_vendor` — see
`services.saas_vendor_service.create_vendor`) or dismisses it.

Mirrors `mcp_discovery`'s candidate lifecycle (#241): a re-import upserts
by `(tenant_id, canonical_name)` and a human-set `status` survives
re-imports.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin

# JSONB on PostgreSQL (GIN-indexable), falls back to JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class DefenderImportStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    dismissed = "dismissed"


class DiscoveredDefenderApp(GRCBase, TenantScopedMixin):
    """One imported app candidate, enriched from the seeded extractor."""

    __tablename__ = "discovered_defender_apps"
    __table_args__ = (
        Index("ix_grc_discovered_defender_apps_tenant_status", "tenant_id", "status"),
        # A re-import re-matching the same app name upserts this row
        # instead of duplicating it — see services.defender_import_service.
        UniqueConstraint(
            "tenant_id",
            "canonical_name",
            name="uq_discovered_defender_app_canonical_name",
        ),
        {"schema": "grc"},
    )

    # Normalized app name used to correlate across re-imports — see
    # services.defender_import_service.canonical_name().
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    users_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_volume_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    certifications: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    # {"euAiAct", "nistAiRmf", "owaspLlm", "iso42001"} → covered|partial|gap,
    # matching SaaSVendorProfile.compliance_status.
    compliance_status: Mapped[dict[str, str]] = mapped_column(
        _JSONBColumn,
        nullable=False,
        default=dict,
    )
    status: Mapped[DefenderImportStatus] = mapped_column(
        Enum(DefenderImportStatus, schema="grc", name="defender_import_status"),
        nullable=False,
        default=DefenderImportStatus.pending,
    )
    # Set once accept() promotes this candidate into a SaaS vendor (use_case) row.
    promoted_vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.use_cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
