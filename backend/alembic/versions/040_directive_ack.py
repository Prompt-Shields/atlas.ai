"""DirectiveAck — append-only audit of device acknowledgements.

Third table of the device-policy workstream (Phase 3a, ACK loop). Adds
``grc.directive_acks`` (tenant-scoped, RLS-isolated). One row per ack; an
audit log, not a status column — a directive's status advances separately.

Acks are always read under a tenant GUC, so no SECURITY DEFINER resolver is
needed here (unlike enrolled_devices' pre-tenant token lookup).

NOTE: the sqlite test DB has no RLS and treats ``set_tenant_guc`` as a no-op,
so tests cannot validate the RLS policy — this must be verified on Postgres.

Revision ID: 040
Revises: 039 (device_directive)
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "directive_acks",
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
            "directive_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.device_directives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="grc",
    )
    # tenant_id single-column index — matches TenantScopedMixin(index=True).
    op.create_index(
        "ix_grc_directive_acks_tenant_id",
        "directive_acks",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directive_acks_directive",
        "directive_acks",
        ["directive_id"],
        schema="grc",
    )

    # ── RLS ───────────────────────────────────────────────────────────
    op.execute("ALTER TABLE grc.directive_acks ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_directive_acks ON grc.directive_acks
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    # Drop the table; the RLS policy + indexes drop with it.
    op.drop_table("directive_acks", schema="grc")
