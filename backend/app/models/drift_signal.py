"""TenantDriftSignal — per-tenant overrides for the drift rule engine.

The stock atlas drift signals (`app.services.drift_signals.DEPRECATED_MODELS`
and `TOOL_REBRANDS`) are global — they cover the public-cloud
deprecations every tenant needs to know about. But each tenant
also has *internal* signals atlas can't know:

  - "We sunset Notion AI in Q1 2026, anyone still on it is drift"
  - "We renamed our internal tool 'AskGPT' to 'AcmeAssist'"
  - "We're migrating off Claude 1 specifically for this org's reasons"

This table lets OrgAdmins add those without an atlas release.
Combined with the curated globals in `detect_drift()`.

RISK_TIER_MISMATCH is intentionally NOT overridable — it's a
self-consistency check on the registry, not a vendor signal.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class TenantDriftSignalKind(str, enum.Enum):
    MODEL_DEPRECATION = "MODEL_DEPRECATION"
    TOOL_REBRAND = "TOOL_REBRAND"


class TenantDriftSignalSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TenantDriftSignal(GRCBase, TenantScopedMixin):
    """One drift rule, owned by one tenant.

    Match is case-insensitive substring against `UseCase.tool`. The
    deprecation/rebrand details inform the user-facing copy in the
    review notes.
    """

    __tablename__ = "tenant_drift_signals"
    __table_args__ = (
        # (tenant, kind, match_pattern) is unique. An OrgAdmin who
        # tries to add the same deprecation twice gets a 409 — the
        # signal already exists.
        UniqueConstraint(
            "tenant_id",
            "kind",
            "match_pattern",
            name="uq_grc_tenant_drift_signal_match",
        ),
        {"schema": "grc"},
    )

    kind: Mapped[TenantDriftSignalKind] = mapped_column(
        Enum(TenantDriftSignalKind, schema="grc"),
        nullable=False,
        index=True,
    )

    # The case-insensitive substring atlas tests against UseCase.tool.
    # Stored as the OrgAdmin entered it (preserving case for display);
    # the rule engine lowercases when matching.
    match_pattern: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Case-insensitive substring matched against UseCase.tool.",
    )

    # Human-readable label shown in the drift finding (e.g. the user
    # sees "Notion AI is deprecated", not the match pattern).
    label: Mapped[str] = mapped_column(String(150), nullable=False)

    severity: Mapped[TenantDriftSignalSeverity] = mapped_column(
        Enum(TenantDriftSignalSeverity, schema="grc"),
        nullable=False,
        default=TenantDriftSignalSeverity.MEDIUM,
    )

    # For MODEL_DEPRECATION: what to migrate to (free text).
    # For TOOL_REBRAND: the current product name.
    successor_or_current_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=("MODEL_DEPRECATION: successor model name. TOOL_REBRAND: current product name."),
    )

    # YYYY-MM string the OrgAdmin can use for context copy. Stored
    # as plain string (not Date) because admins enter it as a
    # release-month string, not a precise day.
    effective_as_of: Mapped[str | None] = mapped_column(String(20), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # Audit-trail provenance.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
