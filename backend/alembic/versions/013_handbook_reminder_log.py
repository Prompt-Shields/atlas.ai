"""HandbookReminderLog table — audit + dedup for handbook Slack DMs.

Powers the `handbook_reminder_dispatcher` worker. One row per DM
sent; the dispatcher reads the most recent row per (user, version)
to honour the 24h cooldown.

Revision ID: 013
Revises: 012
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "handbook_reminder_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slack_channel_id", sa.String(50), nullable=True),
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
        "ix_grc_handbook_reminder_logs_user_id",
        "handbook_reminder_logs",
        ["user_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_handbook_reminder_logs_tenant_id",
        "handbook_reminder_logs",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_handbook_reminder_logs_sent_at",
        "handbook_reminder_logs",
        ["sent_at"],
        schema="grc",
    )
    op.execute(
        "ALTER TABLE grc.handbook_reminder_logs ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.handbook_reminder_logs
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.handbook_reminder_logs"
    )
    op.drop_table("handbook_reminder_logs", schema="grc")
