"""Sentinel forwarder state — delivery cursor + dead-letter queue.

Backs the MVP slice of docs/integrations/microsoft-sentinel/spec.md §7:
the forwarder ships prompt telemetry into a customer's Log Analytics
workspace, and these two tables are how its audit guarantee ("every event
lands in Sentinel or is visibly in the dead-letter queue") stays observable.

Both tables are tenant-scoped and carry the standard RLS policy.

Revision ID: 042
Revises: 041
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEAD_LETTER_STATUS = postgresql.ENUM(
    "PENDING", "REPLAYED", "DISCARDED",
    name="sentinel_dead_letter_status", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    DEAD_LETTER_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "sentinel_forward_cursors",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_event_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "events_forwarded", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "events_skipped", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "batches_sent", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "batches_dead_lettered", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["grc.integrations.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "integration_id", name="uq_grc_sentinel_forward_cursors_integration",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_sentinel_forward_cursors_tenant_id", "sentinel_forward_cursors",
        ["tenant_id"], schema="grc",
    )

    op.create_table(
        "sentinel_dead_letters",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", DEAD_LETTER_STATUS, nullable=False,
            server_default=sa.text("'PENDING'::grc.sentinel_dead_letter_status"),
        ),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.String(2000), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "event_count", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("first_event_id", sa.String(100), nullable=True),
        sa.Column("last_event_id", sa.String(100), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("1"),
        ),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["grc.integrations.id"], ondelete="CASCADE",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_sentinel_dead_letters_tenant_id", "sentinel_dead_letters",
        ["tenant_id"], schema="grc",
    )
    op.create_index(
        "ix_grc_sentinel_dead_letters_integration_id", "sentinel_dead_letters",
        ["integration_id"], schema="grc",
    )
    # The triage query: a tenant's PENDING backlog, oldest first.
    op.create_index(
        "ix_grc_sentinel_dead_letters_tenant_status", "sentinel_dead_letters",
        ["tenant_id", "status", "created_at"], schema="grc",
    )

    for table in ("sentinel_forward_cursors", "sentinel_dead_letters"):
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    for table in ("sentinel_dead_letters", "sentinel_forward_cursors"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON grc.{table}")
    op.drop_table("sentinel_dead_letters", schema="grc")
    op.drop_table("sentinel_forward_cursors", schema="grc")
    DEAD_LETTER_STATUS.drop(op.get_bind(), checkfirst=True)
