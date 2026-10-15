"""Promptly Guide: SCIM provisioning (promptly-guide #58, E10).

Four tenant-scoped tables, RLS-isolated: the tenant's SCIM token (hashed), and the
users, groups and memberships its identity provider pushes, kept to user names,
external ids, active and group names (see ``app/models/guide_scim.py``). Plus the
SECURITY DEFINER resolver ``grc.resolve_guide_scim_token`` for the pre-tenant token
lookup, built as 038 built ``grc.resolve_device_by_token``.

Revision ID: 047
Revises: 046
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("guide_scim_tokens", "guide_scim_users", "guide_scim_groups", "guide_scim_members")


# Also run by the Guide tests, whose schema comes from ``create_all``.
RESOLVE_GUIDE_SCIM_TOKEN = """
CREATE OR REPLACE FUNCTION grc.resolve_guide_scim_token(p_hash text)
RETURNS TABLE (tenant_id uuid)
LANGUAGE sql SECURITY DEFINER
SET search_path = grc
AS $$
    SELECT tenant_id FROM grc.guide_scim_tokens WHERE token_hash = p_hash LIMIT 1
$$;
"""


def _common() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "guide_scim_tokens",
        *_common(),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_guide_scim_tokens_tenant"),
        schema="grc",
    )
    op.create_index(
        "ix_grc_guide_scim_tokens_hash",
        "guide_scim_tokens",
        ["token_hash"],
        unique=True,
        schema="grc",
    )
    op.create_table(
        "guide_scim_users",
        *_common(),
        sa.Column("user_name", sa.String(320), nullable=False),
        sa.Column("user_name_key", sa.String(320), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_name_key", name="uq_guide_scim_users_name"),
        schema="grc",
    )
    op.create_table(
        "guide_scim_groups",
        *_common(),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.UniqueConstraint("tenant_id", "display_name", name="uq_guide_scim_groups_name"),
        schema="grc",
    )
    op.create_table(
        "guide_scim_members",
        *_common(),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "grc.guide_scim_groups.id",
                ondelete="CASCADE",
                name="fk_guide_scim_members_group_id_guide_scim_groups",
            ),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "grc.guide_scim_users.id",
                ondelete="CASCADE",
                name="fk_guide_scim_members_user_id_guide_scim_users",
            ),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "user_id", name="uq_guide_scim_members"),
        schema="grc",
    )
    op.create_index(
        "ix_grc_guide_scim_members_group_id", "guide_scim_members", ["group_id"], schema="grc"
    )
    op.create_index(
        "ix_grc_guide_scim_members_user_id", "guide_scim_members", ["user_id"], schema="grc"
    )

    for table in _TABLES:
        op.create_index(f"ix_grc_{table}_tenant_id", table, ["tenant_id"], schema="grc")
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )

    op.execute(RESOLVE_GUIDE_SCIM_TOKEN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS grc.resolve_guide_scim_token(text)")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="grc")
