"""DeviceDirective — one targeted instruction for a specific EnrolledDevice.

Second table of the device-policy Phase 2 workstream. Adds
``grc.device_directives`` (tenant-scoped, RLS-isolated). A directive is
append-only; its ``status`` advances via delivery/ACK. Phase 2 uses
kind="nudge" only.

Directives are always read under a tenant GUC, so no SECURITY DEFINER
resolver is needed here (unlike enrolled_devices' pre-tenant token lookup).

NOTE: the sqlite test DB has no RLS and treats ``set_tenant_guc`` as a no-op,
so tests cannot validate the RLS policy — this must be verified on Postgres.

Revision ID: 039
Revises: 038 (enrolled_device)
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_directives",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.enrolled_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        schema="grc",
    )
    # tenant_id single-column index — matches TenantScopedMixin(index=True).
    op.create_index(
        "ix_grc_device_directives_tenant_id",
        "device_directives",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_device_directives_device_status",
        "device_directives",
        ["device_id", "status"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_device_directives_tenant_created",
        "device_directives",
        ["tenant_id", "created_at"],
        schema="grc",
    )

    # ── RLS ───────────────────────────────────────────────────────────
    op.execute("ALTER TABLE grc.device_directives ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_device_directives ON grc.device_directives
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    # Drop the table; the RLS policy + indexes drop with it.
    op.drop_table("device_directives", schema="grc")
