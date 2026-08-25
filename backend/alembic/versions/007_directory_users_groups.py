"""DirectoryUser + DirectoryGroup — Microsoft Graph sync output.

PR adds two tables consumed by the `microsoft_sync` worker mode
and the survey-audience resolver (`mode=departments` now resolves
via DirectoryUser when an Entra ID integration has synced).

Revision ID: 007
Revises: 006
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── directory_users ──────────────────────────────────────────────
    op.create_table(
        "directory_users",
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
        sa.Column("external_user_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("department", sa.String(255), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column(
            "linked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
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
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_users_tenant_id",
        "directory_users",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_users_integration_id",
        "directory_users",
        ["integration_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_users_email",
        "directory_users",
        ["email"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_users_department",
        "directory_users",
        ["department"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_users_external_user_id",
        "directory_users",
        ["external_user_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_directory_users_tenant_integration_external",
        "directory_users",
        ["tenant_id", "integration_id", "external_user_id"],
        schema="grc",
    )
    op.execute("ALTER TABLE grc.directory_users ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.directory_users
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )

    # ── directory_groups ─────────────────────────────────────────────
    op.create_table(
        "directory_groups",
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
        sa.Column("external_group_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("group_type", sa.String(50), nullable=True),
        sa.Column(
            "member_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
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
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_groups_tenant_id",
        "directory_groups",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_groups_integration_id",
        "directory_groups",
        ["integration_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_directory_groups_external_group_id",
        "directory_groups",
        ["external_group_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_directory_groups_tenant_integration_external",
        "directory_groups",
        ["tenant_id", "integration_id", "external_group_id"],
        schema="grc",
    )
    op.execute("ALTER TABLE grc.directory_groups ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.directory_groups
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.directory_groups")
    op.drop_table("directory_groups", schema="grc")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.directory_users")
    op.drop_table("directory_users", schema="grc")
