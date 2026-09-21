"""AI ROI human-cost model — cost-ledger slice 3.

`grc.roi_assumptions`: one row per tenant holding the inputs an AI ROI figure
rests on — the blended fully-loaded hourly rate, and where the hours-saved
number comes from.

The table exists because these assumptions were previously in three places that
disagreed: a $75/h constant in `adoption_service`, a $95/h default in the AI
Spend page kept in the browser's `localStorage`, and an assumed $15/seat/week
AI cost used as an ROI denominator while the real cost ledger sat beside it. An
organisation could read a different ROI on each page, none of it shared between
colleagues and none of it attributable to whoever set it.

Revision ID: 044
Revises: 043
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    hours_saved_source = postgresql.ENUM(
        "adoption_pipeline",
        "manual",
        name="hours_saved_source",
        schema="grc",
        create_type=False,
    )
    hours_saved_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "roi_assumptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "blended_hourly_rate_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("75.00"),
        ),
        sa.Column(
            "hours_saved_source",
            hours_saved_source,
            nullable=False,
            server_default=sa.text("'adoption_pipeline'::grc.hours_saved_source"),
        ),
        # Nullable on purpose: absent means "not supplied yet". A 0 default
        # would report a real ROI of zero as though someone had measured it.
        sa.Column("manual_hours_saved_per_month", sa.Numeric(12, 2), nullable=True),
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
        sa.UniqueConstraint("tenant_id", name="uq_grc_roi_assumptions_tenant"),
        schema="grc",
    )
    op.create_index(
        "ix_grc_roi_assumptions_tenant_id",
        "roi_assumptions",
        ["tenant_id"],
        schema="grc",
    )

    op.execute("ALTER TABLE grc.roi_assumptions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.roi_assumptions
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.roi_assumptions")
    op.drop_table("roi_assumptions", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.hours_saved_source")
