"""Use-case registry — grc.use_cases table + enums + RLS.

PR A of the survey-bot backend stack. Adds the registry catalogue
table consumed by `/api/v1/use-cases`. See docs/use-case-survey-bot.md
for the full design.

Revision ID: 002
Revises: 001
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────
    use_case_status = postgresql.ENUM(
        "DRAFT",
        "REVIEW",
        "ACTIVE",
        "RETIRED",
        name="usecasestatus",
        schema="grc",
        create_type=False,
    )
    use_case_status.create(op.get_bind(), checkfirst=True)

    use_case_risk_tier = postgresql.ENUM(
        "LOW",
        "MEDIUM",
        "HIGH",
        name="usecaserisktier",
        schema="grc",
        create_type=False,
    )
    use_case_risk_tier.create(op.get_bind(), checkfirst=True)

    use_case_source = postgresql.ENUM(
        "BOT",
        "FORM",
        "SHADOW_PROMOTE",
        name="usecasesource",
        schema="grc",
        create_type=False,
    )
    use_case_source.create(op.get_bind(), checkfirst=True)

    # ── Table ────────────────────────────────────────────────────────
    op.create_table(
        "use_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "tool",
            sa.String(100),
            nullable=False,
            comment="AI tool name as reported, e.g. 'Microsoft Copilot', 'ChatGPT'",
        ),
        sa.Column("department", sa.String(255), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            use_case_status,
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "risk_tier",
            use_case_risk_tier,
            nullable=False,
            server_default="LOW",
        ),
        sa.Column("source", use_case_source, nullable=False),
        sa.Column(
            "data_classes",
            sa.Text,
            nullable=False,
            server_default="[]",
            comment="JSON array of data-class taxonomy strings",
        ),
        sa.Column("frequency", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "dispatched_from_response_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="FK to grc.survey_responses.id — populated in PR B",
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

    # ── Indexes ──────────────────────────────────────────────────────
    op.create_index(
        "ix_grc_use_cases_tenant_id",
        "use_cases",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_cases_owner_user_id",
        "use_cases",
        ["owner_user_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_cases_status",
        "use_cases",
        ["status"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_cases_dispatched_from_response_id",
        "use_cases",
        ["dispatched_from_response_id"],
        schema="grc",
    )
    # Composite for the registry's most common query: list by tenant +
    # status, newest first.
    op.create_index(
        "ix_grc_use_cases_tenant_status_created",
        "use_cases",
        ["tenant_id", "status", "created_at"],
        schema="grc",
    )

    # ── Row-Level Security ───────────────────────────────────────────
    # Matches the pattern used for blob_records / risk_mitigations /
    # correlation_action_plans / dispatch_events / outbox_messages.
    op.execute("ALTER TABLE grc.use_cases ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.use_cases
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.use_cases")
    op.drop_table("use_cases", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.usecasesource")
    op.execute("DROP TYPE IF EXISTS grc.usecaserisktier")
    op.execute("DROP TYPE IF EXISTS grc.usecasestatus")
