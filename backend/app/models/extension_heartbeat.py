"""ExtensionDeviceHeartbeat — Promptly browser extension check-ins.

The Promptly Chrome / Safari extension POSTs a heartbeat every
N minutes (~5 by default) to /api/v1/extension/heartbeat. The
endpoint upserts one row per (tenant, device_fingerprint) so the
endpoint-compliance aggregate can report a real "extension-
installed" count instead of the IntuneDevice-fresh-within-3-days
heuristic in app/routers/endpoints.py.

Identity model — defence in depth:
  1. Authentication: X-API-Key (tenant-scoped APIKey row). The
     extension stores this key locally (MDM-pushed config profile
     in v0.2 — see docs/mdm-strategy.md). The api_key.tenant_id
     anchors the heartbeat to the right tenant.
  2. Device identity: a stable client-generated fingerprint
     string (UUID v4 persisted to chrome.storage / Safari
     extension storage). Not a hardware id — the extension
     can't read those — but stable across browser restarts.
  3. User association: the user_email field is optional and
     populated only when the extension can read it (e.g. via
     identity.getProfileUserInfo on Chrome enterprise). NULL is
     valid — we still count the device for compliance purposes.

Heartbeat semantics:
  - First POST for a (tenant, fingerprint) tuple creates a row.
  - Subsequent POSTs upsert: bump last_seen_at, refresh metadata
    (browser, extension_version, os_platform, user_email).
  - last_seen_at older than 7 days = stale (extension uninstalled
    or device decommissioned). The aggregate query filters on
    last_seen_at within the past N days; we never delete rows
    server-side (audit-trail preservation).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class ExtensionDeviceHeartbeat(GRCBase, TenantScopedMixin):
    """One row per (tenant, device_fingerprint). Upserted on each heartbeat."""

    __tablename__ = "extension_device_heartbeats"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "device_fingerprint",
            name="uq_grc_extension_heartbeat_device",
        ),
        {"schema": "grc"},
    )

    # Client-generated stable id (UUID v4 persisted in extension storage).
    # We accept any opaque string up to 100 chars — not enforced to be
    # a UUID because Safari + Firefox extensions may use other shapes.
    device_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Best-effort user association. When NULL we still count the
    # device for tenant-wide compliance metrics.
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)

    # Browser family + version + OS — denormalised from the heartbeat
    # body for use in dashboard breakdowns ("how many devices are on
    # Safari? Edge? Chrome?"). All free-text — sanitised at API edge.
    browser: Mapped[str | None] = mapped_column(String(50), nullable=True)
    browser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extension_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # When the last successful heartbeat landed. Indexed because the
    # aggregate query filters by "within past N days".
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # When the device was first seen — useful for "how long has this
    # extension been installed?" analytics and the IT-lead question
    # "did this device check in before our 90-day audit cycle?".
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
