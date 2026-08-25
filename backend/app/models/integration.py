"""Integration — per-tenant connection to an external system.

One row per (tenant, provider). Stores the OAuth credentials
(encrypted), connection metadata, and per-provider config.

Providers in v0.1 (Microsoft focus):

  MICROSOFT_ENTRA_ID     — Azure AD: SSO + user/group sync
  MICROSOFT_INTUNE       — MDM: policy push (worker-side)
  MICROSOFT_PURVIEW      — DLP events, classification
  MICROSOFT_DEFENDER_CASB — Cloud-app discovery (shadow AI)
  SLACK                  — Survey bot DMs (already wired in PR #30)

Placeholders (status=COMING_SOON):
  AWS, AWS_IAM_IDENTITY_CENTER, AWS_BEDROCK
  GCP, GCP_WORKSPACE, GCP_VERTEX

Tokens use the same Fernet encryption layer as SlackWorkspace —
`app.services.crypto.encrypt_token` / `decrypt_token`. The
provider-specific config that doesn't contain secrets lives in
`config_json` (e.g. Intune device-group filter, Purview classifier
selection).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin


class IntegrationProvider(str, enum.Enum):
    # Microsoft — the v0.1 focus.
    MICROSOFT_ENTRA_ID = "MICROSOFT_ENTRA_ID"
    MICROSOFT_INTUNE = "MICROSOFT_INTUNE"
    MICROSOFT_PURVIEW = "MICROSOFT_PURVIEW"
    MICROSOFT_DEFENDER_CASB = "MICROSOFT_DEFENDER_CASB"

    # Already wired in PR #30; surfaced in the integrations tab so
    # the admin sees a unified status view.
    SLACK = "SLACK"

    # Non-Microsoft MDMs — see docs/mdm-strategy.md. v0.2 adapters
    # follow the same shape as the Intune sync (PR #34/#39).
    JAMF_PRO = "JAMF_PRO"
    KANDJI = "KANDJI"
    JUMPCLOUD = "JUMPCLOUD"

    # AI cost connectors
    ANTHROPIC = "ANTHROPIC"
    OPENAI = "OPENAI"
    CURSOR = "CURSOR"
    GITHUB_COPILOT = "GITHUB_COPILOT"
    VERCEL = "VERCEL"

    # SIEM — see docs/feedbacks/integrations/microsoft-sentinel/spec.md.
    # v1 is catalog registration + connect endpoint storing config +
    # a seeded event stream; no live Azure Monitor call (issue #247).
    SENTINEL = "SENTINEL"

    # Placeholders for v0.2.
    AWS = "AWS"
    AWS_IAM_IDENTITY_CENTER = "AWS_IAM_IDENTITY_CENTER"
    AWS_BEDROCK = "AWS_BEDROCK"
    GCP = "GCP"
    GCP_WORKSPACE = "GCP_WORKSPACE"
    GCP_VERTEX = "GCP_VERTEX"


class IntegrationStatus(str, enum.Enum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"  # Token revoked / expired / scope rejected
    DISABLED = "DISABLED"  # Admin paused; config preserved
    COMING_SOON = "COMING_SOON"  # Placeholder rows for AWS / GCP


class Integration(GRCBase, TenantScopedMixin, TestDataMixin):
    """A per-tenant connection to an external system."""

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_grc_integrations_tenant_provider"),
        {"schema": "grc"},
    )

    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider, schema="grc"),
        nullable=False,
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, schema="grc"),
        nullable=False,
        default=IntegrationStatus.NOT_CONNECTED,
    )

    # Admin-visible label — defaults to the provider's display name
    # but can be customised per tenant ("Acme M365 Tenant").
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Vendor-side identifiers (e.g. Azure AD tenant id, Slack team id).
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Tokens — Fernet ciphertext via app.services.crypto.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Comma-separated granted scopes — audit.
    scopes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Provider-specific config (non-secret JSON). Examples:
    #   Intune: {"device_group_ids": [...], "platform_filter": "windows"}
    #   Purview: {"classifier_ids": [...]}
    #   MCAS:    {"app_filter": ["chatgpt.com", "claude.ai"]}
    config_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON object of provider-specific non-secret config",
    )

    # Last successful sync (worker writes this).
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Who connected this integration (audit).
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
