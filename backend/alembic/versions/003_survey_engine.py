"""Survey engine — templates, deliveries, responses, opt-outs.

PR B of the survey-bot backend stack. Adds four tables + 4 enums +
indexes + RLS. Also adds the FK from PR A's
`use_cases.dispatched_from_response_id` → `survey_responses.id` now
that both tables exist.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────
    delivery_channel = postgresql.ENUM(
        "SLACK", "EMAIL",
        name="deliverychannel",
        schema="grc",
        create_type=False,
    )
    delivery_channel.create(op.get_bind(), checkfirst=True)

    delivery_status = postgresql.ENUM(
        "PENDING", "SENDING", "SENT", "FAILED", "CANCELED",
        name="deliverystatus",
        schema="grc",
        create_type=False,
    )
    delivery_status.create(op.get_bind(), checkfirst=True)

    response_status = postgresql.ENUM(
        "NOT_STARTED", "IN_PROGRESS", "COMPLETED", "OPTED_OUT", "EXPIRED",
        name="responsestatus",
        schema="grc",
        create_type=False,
    )
    response_status.create(op.get_bind(), checkfirst=True)

    # ── survey_templates ─────────────────────────────────────────────
    op.create_table(
        "survey_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "questions_json",
            sa.Text,
            nullable=False,
            comment="JSON array of question definitions",
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
        "ix_grc_survey_templates_tenant_id",
        "survey_templates",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_templates_slug",
        "survey_templates",
        ["slug"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_survey_templates_tenant_slug_version",
        "survey_templates",
        ["tenant_id", "slug", "version"],
        schema="grc",
    )

    # ── survey_deliveries ────────────────────────────────────────────
    op.create_table(
        "survey_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.survey_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("audience_filter_json", sa.Text, nullable=False),
        sa.Column("audience_label", sa.String(255), nullable=False),
        sa.Column(
            "channel",
            delivery_channel,
            nullable=False,
            server_default="SLACK",
        ),
        sa.Column(
            "status",
            delivery_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "sent_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recipient_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completed_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "new_registration_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
        "ix_grc_survey_deliveries_tenant_id",
        "survey_deliveries",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_deliveries_template_id",
        "survey_deliveries",
        ["template_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_deliveries_status",
        "survey_deliveries",
        ["status"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_deliveries_sent_by_user_id",
        "survey_deliveries",
        ["sent_by_user_id"],
        schema="grc",
    )

    # ── survey_responses ─────────────────────────────────────────────
    op.create_table(
        "survey_responses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.survey_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slack_user_id", sa.String(20), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column(
            "status",
            response_status,
            nullable=False,
            server_default="NOT_STARTED",
        ),
        sa.Column(
            "answers_json",
            sa.Text,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "registered_as_use_case_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("slack_channel_id", sa.String(20), nullable=True),
        sa.Column("slack_message_ts", sa.String(50), nullable=True),
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
        "ix_grc_survey_responses_tenant_id",
        "survey_responses",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_responses_delivery_id",
        "survey_responses",
        ["delivery_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_responses_user_id",
        "survey_responses",
        ["user_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_responses_slack_user_id",
        "survey_responses",
        ["slack_user_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_responses_status",
        "survey_responses",
        ["status"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_survey_responses_registered_as_use_case_id",
        "survey_responses",
        ["registered_as_use_case_id"],
        schema="grc",
    )

    # ── slack_opt_outs ───────────────────────────────────────────────
    op.create_table(
        "slack_opt_outs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_user_id", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
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
        "ix_grc_slack_opt_outs_tenant_id",
        "slack_opt_outs",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_slack_opt_outs_slack_user_id",
        "slack_opt_outs",
        ["slack_user_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_slack_opt_outs_tenant_user",
        "slack_opt_outs",
        ["tenant_id", "slack_user_id"],
        schema="grc",
    )

    # ── FK fix-up: use_cases.dispatched_from_response_id → survey_responses
    # PR A left this column un-FK'd to avoid a circular migration.
    # Now that survey_responses exists, add the constraint.
    op.create_foreign_key(
        "fk_grc_use_cases_dispatched_from_response_id",
        source_table="use_cases",
        referent_table="survey_responses",
        local_cols=["dispatched_from_response_id"],
        remote_cols=["id"],
        source_schema="grc",
        referent_schema="grc",
        ondelete="SET NULL",
    )

    # ── Row-Level Security ───────────────────────────────────────────
    for table in [
        "survey_templates",
        "survey_deliveries",
        "survey_responses",
        "slack_opt_outs",
    ]:
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_grc_use_cases_dispatched_from_response_id",
        "use_cases",
        schema="grc",
        type_="foreignkey",
    )
    for table in [
        "slack_opt_outs",
        "survey_responses",
        "survey_deliveries",
        "survey_templates",
    ]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON grc.{table}")
        op.drop_table(table, schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.responsestatus")
    op.execute("DROP TYPE IF EXISTS grc.deliverystatus")
    op.execute("DROP TYPE IF EXISTS grc.deliverychannel")
