"""Promptly Guide: the admin console's settings (promptly-guide N1, option A).

One tenant-scoped table, RLS-isolated: ``guide_console_settings``, one console
document per organisation, in the shape of Guide's file console. Atlas turns it into
configuration profiles an admin downloads for the MDM; it never writes to a Mac (see
``app/models/guide_console.py``).

Revision ID: 052
Revises: 051
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "guide_console_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
        sa.Column("console", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_guide_console_settings_tenant"),
        schema="grc",
    )
    # tenant_id single-column index — matches TenantScopedMixin(index=True).
    op.create_index(f"ix_grc_{_TABLE}_tenant_id", _TABLE, ["tenant_id"], schema="grc")
    op.execute(f"ALTER TABLE grc.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{_TABLE} ON grc.{_TABLE}
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.drop_table(_TABLE, schema="grc")
