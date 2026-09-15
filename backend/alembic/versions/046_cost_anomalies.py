"""AI spend anomaly detection — cost-ledger slice 5.

`grc.cost_anomalies`: one row per (tenant, provider, day) whose spend broke
sharply from that scope's own trailing median.

Slice 4's budgets answer "are we overspending this month?". They cannot answer
"did something go wrong yesterday?" — a runaway agent or a leaked key burns a
month's ceiling in a day, and a monthly budget notices once the money is gone.

The row exists rather than the figure being recomputed on read for two reasons,
both about not nagging: alerting needs to remember what it already said, and an
acknowledgement ("we know, it was the backfill") needs somewhere to live so the
next detection run respects it.

The partial unique index mirrors migration 045's: Postgres treats NULLs as
distinct in a plain unique constraint, so without it the tenant-wide anomaly
for a given day could be inserted repeatedly — once per daily cron run, each
one alerting.

Revision ID: 046
Revises: 045
Create Date: 2026-09-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cost_provider = postgresql.ENUM(
        name="cost_provider",
        schema="grc",
        create_type=False,
    )

    op.create_table(
        "cost_anomalies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL = the anomaly is in tenant-wide spend, not one provider's.
        sa.Column("provider", cost_provider, nullable=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("observed_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("baseline_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("ratio", sa.Numeric(10, 2), nullable=False),
        sa.Column("baseline_days", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["grc.users.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "usage_date",
            name="uq_grc_cost_anomalies_scope_day",
        ),
        # A baseline drawn from fewer days than the detector's minimum should
        # never have produced a row; if one appears, the policy was bypassed.
        sa.CheckConstraint(
            "baseline_days >= 7",
            name="ck_grc_cost_anomalies_baseline_days",
        ),
        # A zero or negative baseline makes the ratio meaningless — the service
        # refuses to divide by it, and the table refuses to record it.
        sa.CheckConstraint(
            "baseline_usd > 0",
            name="ck_grc_cost_anomalies_baseline_positive",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_cost_anomalies_tenant_id",
        "cost_anomalies",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cost_anomalies_usage_date",
        "cost_anomalies",
        ["usage_date"],
        schema="grc",
    )
    # The half the table constraint cannot express: NULL providers compare
    # distinct, so the tenant-wide anomaly needs its own partial unique index.
    op.create_index(
        "uq_grc_cost_anomalies_overall_day",
        "cost_anomalies",
        ["tenant_id", "usage_date"],
        unique=True,
        postgresql_where=sa.text("provider IS NULL"),
        schema="grc",
    )

    op.execute("ALTER TABLE grc.cost_anomalies ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.cost_anomalies
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.cost_anomalies")
    op.drop_index(
        "uq_grc_cost_anomalies_overall_day",
        table_name="cost_anomalies",
        schema="grc",
    )
    op.drop_table("cost_anomalies", schema="grc")
