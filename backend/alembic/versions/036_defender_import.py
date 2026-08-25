"""Defender for Cloud Apps import — discovered_defender_apps table + enum + RLS.

Revision ID: 036
Revises: 035 (use_case_source_voice_interview)
Create Date: 2026-08-16

M3 of the AISPM full-scan port (#243). Imported app candidates, enriched
by the seeded extractor: one row per (tenant, canonical app name), upserted
on re-import.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the enum is created only by the explicit e.create() call
# below, otherwise create_table() re-emits CREATE TYPE and collides in-transaction.
_STATUS = postgresql.ENUM(
    "pending", "accepted", "dismissed",
    name="defender_import_status", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _STATUS.create(bind, checkfirst=True)

    op.create_table(
        "discovered_defender_apps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("users_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_volume_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("certifications", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("compliance_status", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", _STATUS, nullable=False, server_default="pending"),
        sa.Column("promoted_vendor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.use_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "canonical_name", name="uq_discovered_defender_app_canonical_name"),
        schema="grc",
    )
    op.create_index("ix_grc_discovered_defender_apps_tenant", "discovered_defender_apps", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_discovered_defender_apps_tenant_status", "discovered_defender_apps", ["tenant_id", "status"], schema="grc")

    op.execute("ALTER TABLE grc.discovered_defender_apps ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON grc.discovered_defender_apps "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.discovered_defender_apps")
    op.drop_table("discovered_defender_apps", schema="grc")
    _STATUS.drop(op.get_bind(), checkfirst=True)
