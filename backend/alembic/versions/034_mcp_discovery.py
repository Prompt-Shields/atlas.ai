"""MCP server discovery — discovered_mcp_servers table + enum + RLS.

Revision ID: 034
Revises: 033 (pep_violations)
Create Date: 2026-08-16

M3 of the AISPM full-scan port (#241). Correlated/scored MCP server
candidates: one row per (tenant, canonical name+host key), fused from
collector observations and upserted on re-scan.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the enum is created only by the explicit e.create() call
# below, otherwise create_table() re-emits CREATE TYPE and collides in-transaction.
_STATUS = postgresql.ENUM(
    "candidate", "promoted", "dismissed",
    name="mcp_discovery_status", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _STATUS.create(bind, checkfirst=True)

    op.create_table(
        "discovered_mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_key", sa.String(512), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("transport", sa.String(20), nullable=False, server_default="stdio"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", _STATUS, nullable=False, server_default="candidate"),
        sa.Column("promoted_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.ai_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "canonical_key", name="uq_discovered_mcp_server_canonical_key"),
        schema="grc",
    )
    op.create_index("ix_grc_discovered_mcp_servers_tenant", "discovered_mcp_servers", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_discovered_mcp_servers_tenant_status", "discovered_mcp_servers", ["tenant_id", "status"], schema="grc")

    op.execute("ALTER TABLE grc.discovered_mcp_servers ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON grc.discovered_mcp_servers "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.discovered_mcp_servers")
    op.drop_table("discovered_mcp_servers", schema="grc")
    _STATUS.drop(op.get_bind(), checkfirst=True)
