"""Guided steps: the Work client pool's monthly counter (promptly-guide #62).

promptly-guide `docs/step-metering.md` counts only steps that Promptly's
hosted model answers, at the gateway that serves them, and keeps the Work
client's meter in Atlas. Two tenant-scoped tables, RLS-isolated, and one
tenant column:

  * ``guided_step_counts``: one counter per organisation per month;
  * ``guided_step_receipts``: the gateway's id for each report already
    counted, so a retried report counts once;
  * ``tenants.guided_step_hard_cap``: whether an administrator stops hosted
    steps at the pool instead of paying overage. Off for every tenant.

Nothing per person and no timestamps; see ``app/models/guided_step.py``.

Revision ID: 051
Revises: 050
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("guided_step_counts", "guided_step_receipts")


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("uuid_generate_v4()"),
    )


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "guided_step_counts",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "period", name="uq_guided_step_counts"),
        schema="grc",
    )
    op.create_table(
        "guided_step_receipts",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("report_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "period", "report_id", name="uq_guided_step_receipts"),
        schema="grc",
    )
    for table in _TABLES:
        # tenant_id single-column index — matches TenantScopedMixin(index=True).
        op.create_index(f"ix_grc_{table}_tenant_id", table, ["tenant_id"], schema="grc")
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )

    op.add_column(
        "tenants",
        sa.Column(
            "guided_step_hard_cap",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="grc",
    )


def downgrade() -> None:
    op.drop_column("tenants", "guided_step_hard_cap", schema="grc")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="grc")
