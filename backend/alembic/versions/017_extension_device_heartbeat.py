"""ExtensionDeviceHeartbeat table — Promptly browser-extension check-ins.

One row per (tenant, device_fingerprint). Upserted on each heartbeat.
Powers the endpoint-compliance aggregate's real "extension-installed"
count (replaces the IntuneDevice-fresh-within-3-days heuristic).

Revision ID: 017
Revises: 016
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extension_device_heartbeats",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_fingerprint", sa.String(100), nullable=False),
        sa.Column("user_email", sa.String(320), nullable=True),
        sa.Column("browser", sa.String(50), nullable=True),
        sa.Column("browser_version", sa.String(50), nullable=True),
        sa.Column("extension_version", sa.String(50), nullable=True),
        sa.Column("os_platform", sa.String(50), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint(
            "tenant_id",
            "device_fingerprint",
            name="uq_grc_extension_heartbeat_device",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_extension_heartbeats_fingerprint",
        "extension_device_heartbeats",
        ["device_fingerprint"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_extension_heartbeats_user_email",
        "extension_device_heartbeats",
        ["user_email"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_extension_heartbeats_last_seen",
        "extension_device_heartbeats",
        ["last_seen_at"],
        schema="grc",
    )
    op.execute(
        "ALTER TABLE grc.extension_device_heartbeats ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.extension_device_heartbeats
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.extension_device_heartbeats"
    )
    op.drop_table("extension_device_heartbeats", schema="grc")
