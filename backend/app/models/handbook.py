"""HandbookAcknowledgement — per-user record of reading the AI
governance handbook.

Customer feedback (Øystein Endal, AI Risk Officer):
  "Gi opplæring og få folk til å forstå hvorfor vi har AI governance
   og hvorfor de skal bry seg (burde bruke mer tid på det)"
   → Provide training and make people understand why we have AI
     governance and why they should care.

This table records that a user has read + accepted the current
version of the handbook. Versions bump when the org updates the
content; users get a re-acknowledgement prompt on next dashboard
load. The handbook itself is curated content on the frontend; only
the acceptance record lives in the DB.

The version string is opaque to the backend — it's whatever the
admin sets in tenant settings (defaults to "1.0" for stock atlas).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class HandbookAcknowledgement(GRCBase, TenantScopedMixin):
    """One row per (user, version) acceptance.

    Re-acknowledgement on version bump = a new row, not an update.
    Audit trail preservation.
    """

    __tablename__ = "handbook_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "version",
            name="uq_grc_handbook_user_version",
        ),
        {"schema": "grc"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Handbook version the user acknowledged",
    )
    # Free-form notes the user can optionally leave on acknowledge
    # (e.g. "Read the donor-PII section twice — clear now").
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class HandbookReminderLog(GRCBase, TenantScopedMixin):
    """Audit + dedup record for handbook Slack-DM reminders.

    Powers the `handbook_reminder_dispatcher` worker. One row per
    DM sent; the dispatcher reads the most recent row per (user,
    version) to honour the 24h cooldown and avoid spamming.

    Once a user acknowledges the handbook on /dashboard/handbook,
    the dispatcher skips them via the absence-of-Acknowledgement-
    row check at query time — no need to delete reminder rows.
    Audit trail is preserved.
    """

    __tablename__ = "handbook_reminder_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Handbook version the user was reminded about",
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    # Slack DM channel id for traceability — useful for the IT
    # lead's "show me what you sent" view in v0.2.
    slack_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)


class TenantHandbookOverride(GRCBase, TenantScopedMixin):
    """Per-tenant override for the AI governance handbook content + version.

    The stock atlas handbook copy is curated in the frontend
    (`/dashboard/handbook` Markdown blocks). When a tenant wants to
    publish their own (e.g. acme.atlas has a custom RACI table or
    sector-specific data-class taxonomy), the OrgAdmin can post a
    row here. The router prefers tenant content + tenant version
    when present, otherwise falls back to stock.

    Version bumping = a re-acknowledgement campaign. Every change to
    `version` invalidates existing HandbookAcknowledgement rows
    (because the dispatcher filters by current version), so users
    get re-prompted via dashboard banner + Slack DM. That's the
    expected behaviour — content changed, re-ack required.
    """

    __tablename__ = "tenant_handbook_overrides"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            name="uq_grc_tenant_handbook_override_tenant",
        ),
        {"schema": "grc"},
    )

    # Bumped by the OrgAdmin on each material content change.
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "Tenant-specific handbook version. Bump to trigger re-ack "
            "campaign for all users in this tenant."
        ),
    )

    # Markdown body. The frontend renders this with the same
    # rehype/remark pipeline used for the stock handbook copy.
    content_markdown: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Markdown body of the tenant's custom handbook content.",
    )

    # User who last edited this override — useful for the audit log
    # answer to "who changed this and why?". SET NULL on user delete
    # so deleting a user doesn't cascade-delete the policy content.
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
