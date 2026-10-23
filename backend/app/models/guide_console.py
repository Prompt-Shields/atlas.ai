"""Promptly Guide's admin console settings, held in Atlas.AI (promptly-guide N1, option A).

One document per organisation: the same console document as Guide's file console
(promptly-guide ``deploy/console.example.json``), checked by
``app.services.guide_console.profiles.build`` before it is kept. From it, Atlas makes
the configuration profiles an admin downloads and uploads to the MDM. Atlas never
writes to a Mac: under option A, MDM stays the only channel that changes one.

The document only restricts or names content: exclusions, packs switched off, the
organisation's own pack folders and knowledge sources, groups, sign-in and where
adoption figures go. Nothing in it can turn on anything that reads; the person's own
opt-ins stay in their own ``privacy.json`` on their Mac.

It names the organisation's settings, never a person. Who changed it is in the audit
log, with every changed key.

Tenant-scoped (``tenant_id`` + RLS), like the other Guide tables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class GuideConsoleSettings(GRCBase, TenantScopedMixin):
    """The organisation's console document. One per tenant."""

    __tablename__ = "guide_console_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_guide_console_settings_tenant"),
        {"schema": "grc"},
    )

    # The console document, in the file console's own shape.
    console: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Counts saves that changed something, from 1, so an admin can tell which version
    # of the profiles they uploaded.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The admin who last changed it, as the audit log names them.
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


__all__ = ["GuideConsoleSettings"]
