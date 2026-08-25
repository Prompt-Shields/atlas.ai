"""Agent discovery — discovered_agents table + enums + RLS.

Revision ID: 030
Revises: 029 (spm_entities)
Create Date: 2026-08-16

M1 of the AISPM full-scan port (#233). Cloud agent registry scanner: one row
per agent observed in a provider registry (AWS Bedrock AgentCore, Azure AI
Foundry, GCP Knowledge Catalog), upserted on `(tenant_id, provider,
external_id)` by re-running collectors.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: enums are created only by the explicit e.create() loop,
# otherwise create_table() re-emits CREATE TYPE and collides in-transaction.
_PROVIDER = postgresql.ENUM(
    "aws_bedrock_agentcore", "azure_ai_foundry", "gcp_knowledge_catalog",
    name="agent_cloud_provider", schema="grc", create_type=False,
)
_STATUS = postgresql.ENUM(
    "shadow", "pending", "approved", "dismissed",
    name="agent_discovery_status", schema="grc", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for e in (_PROVIDER, _STATUS):
        e.create(bind, checkfirst=True)

    op.create_table(
        "discovered_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", _PROVIDER, nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("registry", sa.String(255), nullable=False),
        sa.Column("runtime", sa.String(255), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", _STATUS, nullable=False, server_default="shadow"),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "external_id", name="uq_discovered_agent_provider_external_id"),
        schema="grc",
    )
    op.create_index("ix_grc_discovered_agents_tenant", "discovered_agents", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_discovered_agents_tenant_status", "discovered_agents", ["tenant_id", "status"], schema="grc")

    op.execute("ALTER TABLE grc.discovered_agents ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON grc.discovered_agents "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.discovered_agents")
    op.drop_table("discovered_agents", schema="grc")
    for e in (_STATUS, _PROVIDER):
        e.drop(op.get_bind(), checkfirst=True)
