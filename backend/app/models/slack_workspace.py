"""SlackWorkspace — per-tenant Slack install.

PR C of the survey-bot backend stack. One row per tenant per Slack
workspace; in v0.1 each tenant maps to exactly one workspace
(Enterprise tier unlocks multi-workspace later).

`bot_access_token_encrypted` is encrypted at rest via
`app.services.crypto`. Decrypt-on-use only, never log the plaintext.

`installed_at` lives separately from `created_at` so re-installs
update the install time but preserve audit history. `uninstalled_at`
is set when an admin disconnects the integration — we keep the row
for audit but the `is_active` flag prevents dispatches.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin


class SlackWorkspace(GRCBase, TenantScopedMixin, TestDataMixin):
    __tablename__ = "slack_workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "team_id", name="uq_grc_slack_workspaces_tenant_team"),
        {"schema": "grc"},
    )

    # Slack identifiers
    team_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_user_id: Mapped[str] = mapped_column(String(20), nullable=False)

    # Encrypted bot token — Fernet ciphertext. Never log raw.
    bot_access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Comma-separated OAuth scopes — for audit.
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    installed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
