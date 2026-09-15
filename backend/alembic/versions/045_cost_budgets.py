"""AI spend budgets and threshold alerts — cost-ledger slice 4.

`grc.cost_budgets`: a monthly USD ceiling per tenant, either tenant-wide (NULL
provider) or scoped to one provider, plus the alert state that keeps a single
threshold crossing from mailing every day until someone filters it.

Slices 1-3 made the spend accurate and the ROI honest. Both still require
somebody to open the page. This is the first part of the ledger that speaks
without being asked.

Two constraints below are load-bearing rather than decorative:

  * A partial unique index on `tenant_id WHERE provider IS NULL`. Postgres
    treats NULLs as distinct in a plain unique constraint, so without this a
    tenant could hold two tenant-wide budgets and get two different answers to
    one question.
  * A CHECK that the warn threshold sits in (0, 100]. A threshold of 0 would
    fire `warning` before a cent was spent; above 100 it could never fire at
    all, which is a setting that silently does nothing.

Revision ID: 045
Revises: 044
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    budget_alert_level = postgresql.ENUM(
        "ok",
        "warning",
        "exceeded",
        name="budget_alert_level",
        schema="grc",
        create_type=False,
    )
    budget_alert_level.create(op.get_bind(), checkfirst=True)

    # Reuse the existing cost_provider type rather than minting a second one;
    # a budget scoped to a provider the ledger cannot record would be
    # unreachable by construction.
    cost_provider = postgresql.ENUM(
        name="cost_provider",
        schema="grc",
        create_type=False,
    )

    op.create_table(
        "cost_budgets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL = the tenant-wide ceiling across every provider.
        sa.Column("provider", cost_provider, nullable=True),
        sa.Column("amount_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "warn_threshold_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("80.00"),
        ),
        sa.Column(
            "alerts_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_alerted_period", sa.Date(), nullable=True),
        sa.Column("last_alerted_level", budget_alert_level, nullable=True),
        sa.Column("last_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["updated_by_user_id"],
            ["grc.users.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            name="uq_grc_cost_budgets_tenant_provider",
        ),
        # A ceiling of zero or less is not a budget, it is a mistake that would
        # report every tenant as permanently over.
        sa.CheckConstraint("amount_usd > 0", name="ck_grc_cost_budgets_amount_positive"),
        sa.CheckConstraint(
            "warn_threshold_percent > 0 AND warn_threshold_percent <= 100",
            name="ck_grc_cost_budgets_threshold_range",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_cost_budgets_tenant_id",
        "cost_budgets",
        ["tenant_id"],
        schema="grc",
    )
    # The half of the uniqueness the table constraint cannot express: NULLs
    # compare distinct, so without this a tenant can hold two overall budgets.
    op.create_index(
        "uq_grc_cost_budgets_tenant_overall",
        "cost_budgets",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("provider IS NULL"),
        schema="grc",
    )

    op.execute("ALTER TABLE grc.cost_budgets ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.cost_budgets
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.cost_budgets")
    op.drop_index(
        "uq_grc_cost_budgets_tenant_overall",
        table_name="cost_budgets",
        schema="grc",
    )
    op.drop_table("cost_budgets", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.budget_alert_level")
