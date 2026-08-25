"""Slack workspaces — per-tenant install tokens.

PR C of the survey-bot backend stack. Adds the table that holds the
encrypted bot access token + workspace metadata.

Revision ID: 004
Revises: 003
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.String(20), nullable=False),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("bot_user_id", sa.String(20), nullable=False),
        sa.Column(
            "bot_access_token_encrypted",
            sa.Text,
            nullable=False,
            comment="Fernet ciphertext; decrypt via app.services.crypto",
        ),
        sa.Column(
            "scopes",
            sa.String(500),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "installed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "uninstalled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column(
            "is_test_data", sa.Boolean, nullable=False, server_default="false"
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
        "ix_grc_slack_workspaces_tenant_id",
        "slack_workspaces",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_slack_workspaces_team_id",
        "slack_workspaces",
        ["team_id"],
        schema="grc",
    )
    # One active workspace per (tenant, team). Re-installs upsert.
    op.create_unique_constraint(
        "uq_grc_slack_workspaces_tenant_team",
        "slack_workspaces",
        ["tenant_id", "team_id"],
        schema="grc",
    )

    op.execute("ALTER TABLE grc.slack_workspaces ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.slack_workspaces
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.slack_workspaces"
    )
    op.drop_table("slack_workspaces", schema="grc")
