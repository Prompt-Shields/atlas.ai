"""PEP violations table — write-through record from evaluate (issue #238).

Revision ID: 033
Revises: 032 (pep_domain)
Create Date: 2026-08-16

Creates `grc.policy_violations`, tenant-scoped with RLS, FK'd to
`grc.policy_instances`. Reuses the `policy_enforcement_mode` enum from 030
for `action_taken` (no new enum type needed).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENFORCEMENT_MODE = postgresql.ENUM(
    "log", "flag", "block", "redact",
    name="policy_enforcement_mode", schema="grc", create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "policy_violations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "policy_instance_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.policy_instances.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("prompt_excerpt", sa.Text(), nullable=False),
        sa.Column("action_taken", _ENFORCEMENT_MODE, nullable=False),
        sa.Column("triggered_detectors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("evaluated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="grc",
    )
    for col in ["tenant_id", "policy_instance_id"]:
        op.create_index(f"ix_grc_policy_violations_{col}", "policy_violations", [col], schema="grc")

    op.execute("ALTER TABLE grc.policy_violations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_policy_violations ON grc.policy_violations "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy_violations ON grc.policy_violations")
    op.drop_table("policy_violations", schema="grc")
