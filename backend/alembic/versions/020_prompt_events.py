"""prompt_events table — hash-only prompt telemetry from the
prompt-shields clients (Safari extension, macOS widget, SDK).

Revision ID: 020
Revises: 019
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SOURCE = postgresql.ENUM(
    "safari_extension", "macos_widget", "sdk",
    name="prompt_event_source", schema="grc", create_type=False,
)
KIND = postgresql.ENUM(
    "activity", "violation",
    name="prompt_event_kind", schema="grc", create_type=False,
)
ACTION = postgresql.ENUM(
    "allowed", "logged", "redacted", "flagged", "blocked",
    name="prompt_event_action", schema="grc", create_type=False,
)
SEVERITY = postgresql.ENUM(
    "low", "medium", "high", "critical",
    name="prompt_event_severity", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (SOURCE, KIND, ACTION, SEVERITY):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "prompt_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", SOURCE, nullable=False),
        sa.Column("event_kind", KIND, nullable=False),
        sa.Column("app_id", sa.String(120), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("action", ACTION, nullable=True),
        sa.Column("severity", SEVERITY, nullable=True),
        sa.Column(
            "pii_categories", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("device_fingerprint", sa.String(100), nullable=True),
        sa.Column("user_external_id", sa.String(255), nullable=True),
        sa.Column("session_id", sa.String(120), nullable=True),
        sa.Column("vendor", sa.String(50), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "occurrences", sa.Integer(),
            nullable=False, server_default=sa.text("1"),
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["grc.api_keys.id"], ondelete="SET NULL",
        ),
        schema="grc",
    )

    op.create_index(
        "ix_grc_prompt_events_tenant_id", "prompt_events",
        ["tenant_id"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_device_fingerprint", "prompt_events",
        ["device_fingerprint"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_api_key_id", "prompt_events",
        ["api_key_id"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_tenant_occurred", "prompt_events",
        ["tenant_id", "occurred_at"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_tenant_source_occurred", "prompt_events",
        ["tenant_id", "source", "occurred_at"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_tenant_app_occurred", "prompt_events",
        ["tenant_id", "app_id", "occurred_at"], schema="grc",
    )
    op.create_index(
        "ix_grc_prompt_events_tenant_kind_occurred", "prompt_events",
        ["tenant_id", "event_kind", "occurred_at"], schema="grc",
    )

    op.execute("ALTER TABLE grc.prompt_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.prompt_events
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.prompt_events")
    op.drop_table("prompt_events", schema="grc")
    bind = op.get_bind()
    for enum_type in (SEVERITY, ACTION, KIND, SOURCE):
        enum_type.drop(bind, checkfirst=True)
