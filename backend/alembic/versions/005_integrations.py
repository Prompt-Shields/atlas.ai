"""Integration table — per-tenant connections (Microsoft, AWS, GCP, Slack).

PR adds the Integration table consumed by /api/v1/integrations CRUD.
Slack remains in its own table (PR #30); the Integration row for the
Slack provider is created opportunistically by the install flow.

Revision ID: 005
Revises: 004
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────
    integration_provider = postgresql.ENUM(
        "MICROSOFT_ENTRA_ID",
        "MICROSOFT_INTUNE",
        "MICROSOFT_PURVIEW",
        "MICROSOFT_DEFENDER_CASB",
        "SLACK",
        "AWS",
        "AWS_IAM_IDENTITY_CENTER",
        "AWS_BEDROCK",
        "GCP",
        "GCP_WORKSPACE",
        "GCP_VERTEX",
        name="integrationprovider",
        schema="grc",
        create_type=False,
    )
    integration_provider.create(op.get_bind(), checkfirst=True)

    integration_status = postgresql.ENUM(
        "NOT_CONNECTED",
        "CONNECTED",
        "ERROR",
        "DISABLED",
        "COMING_SOON",
        name="integrationstatus",
        schema="grc",
        create_type=False,
    )
    integration_status.create(op.get_bind(), checkfirst=True)

    # ── Table ────────────────────────────────────────────────────────
    op.create_table(
        "integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", integration_provider, nullable=False),
        sa.Column(
            "status",
            integration_status,
            nullable=False,
            server_default="NOT_CONNECTED",
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("external_name", sa.String(255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text, nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text, nullable=True),
        sa.Column(
            "access_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("scopes", sa.String(2000), nullable=True),
        sa.Column("config_json", sa.Text, nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "connected_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
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
        "ix_grc_integrations_tenant_id",
        "integrations",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_integrations_external_id",
        "integrations",
        ["external_id"],
        schema="grc",
    )
    # One row per (tenant, provider) — re-installs upsert.
    op.create_unique_constraint(
        "uq_grc_integrations_tenant_provider",
        "integrations",
        ["tenant_id", "provider"],
        schema="grc",
    )

    op.execute("ALTER TABLE grc.integrations ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.integrations
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.integrations")
    op.drop_table("integrations", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.integrationstatus")
    op.execute("DROP TYPE IF EXISTS grc.integrationprovider")
