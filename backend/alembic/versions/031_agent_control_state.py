"""Agent control tower — agent_control_states table + enums + RLS.

Revision ID: 031
Revises: 030 (agent_discovery)
Create Date: 2026-08-16

M1 of the AISPM full-scan port (#235). Persisted operator overrides
(pause/quarantine/guardrail toggle) layered on top of `discovered_agents`;
derived health/lifecycle stay computed at read time, see
`services/agent_control_service.py`.

NOTE: this stacks on migration 030 from #233 (`discovered_agents`), which
was still an open, unmerged PR (#254) at the time this migration was
written. If #254 lands with a different revision id for its migration,
this file's `down_revision` needs to be updated to match before merge.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIFECYCLE_OVERRIDE = postgresql.ENUM(
    "none", "paused", "quarantined",
    name="agent_lifecycle_override", schema="grc", create_type=False,
)
_ACTION = postgresql.ENUM(
    "pause", "quarantine", "toggle-guardrail",
    name="agent_control_action", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for e in (_LIFECYCLE_OVERRIDE, _ACTION):
        e.create(bind, checkfirst=True)

    op.create_table(
        "agent_control_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.discovered_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lifecycle_override", _LIFECYCLE_OVERRIDE, nullable=False, server_default="none"),
        sa.Column("guardrail_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_action", _ACTION, nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_action_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "agent_id", name="uq_agent_control_state_agent"),
        schema="grc",
    )
    op.create_index("ix_grc_agent_control_states_tenant", "agent_control_states", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_agent_control_states_agent", "agent_control_states", ["agent_id"], schema="grc")

    op.execute("ALTER TABLE grc.agent_control_states ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON grc.agent_control_states "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.agent_control_states")
    op.drop_table("agent_control_states", schema="grc")
    for e in (_ACTION, _LIFECYCLE_OVERRIDE):
        e.drop(op.get_bind(), checkfirst=True)
