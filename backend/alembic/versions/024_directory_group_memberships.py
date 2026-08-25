"""directory_group_memberships join table + RLS (PRO-56).

The user↔group membership join the DirectoryGroup docstring flagged as a
follow-up; populated by the microsoft_sync worker from Graph
/groups/{id}/members so the admin portal can list a team's members.

Chains off 023_ai_cost_records.

Revision ID: 024
Revises: 023
Create Date: 2026-06-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "grc"


def upgrade() -> None:
    op.create_table(
        "directory_group_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.directory_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.directory_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "user_id", name="uq_grc_dir_group_membership"),
        schema=SCHEMA,
    )
    for col in ("tenant_id", "integration_id", "group_id", "user_id"):
        op.create_index(
            f"ix_grc_directory_group_memberships_{col}",
            "directory_group_memberships",
            [col],
            schema=SCHEMA,
        )
    op.execute(
        "ALTER TABLE grc.directory_group_memberships ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.directory_group_memberships
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.directory_group_memberships"
    )
    op.drop_table("directory_group_memberships", schema=SCHEMA)
