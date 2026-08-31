"""Self-hosted AI spend ingestion — cost-ledger slice 2.

Customers running their own AI apps on Azure AI Foundry / AWS Bedrock have no
per-tenant billing API we can pull: that spend sits on *their* cloud bill,
undifferentiated by application. So slice 2 inverts the direction — the app
reports its own per-call token usage and we derive cost into the same daily
ledger slice 1 built (see docs/design/ai-cost-ledger-connectors.md, "Out of
scope → slice 2").

Two changes:

1. Enum values for the push-mode providers. `cost_provider` gains the two the
   design doc names plus a generic `self_hosted` for anything else (GCP Vertex,
   an on-prem vLLM box) so a customer is never blocked on us adding a constant.
   `integrationprovider` gains AZURE_AI_FOUNDRY and SELF_HOSTED_AI; AWS_BEDROCK is
   already there.

2. `grc.ai_cost_usage_batches` — the replay guard. Pushed usage *accumulates*
   into a day's ledger row rather than overwriting it, which is the opposite of
   how the pull connectors behave and the reason this table has to exist: a
   client that retries a batch after a timeout would otherwise double-count its
   own spend silently. Recording each accepted batch id makes a replay a no-op.

Revision ID: 043
Revises: 042
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE is transaction-safe on PG12+ so long as the new
    # value is not *used* in the same transaction. Nothing below inserts one.
    for value in ("azure_ai_foundry", "aws_bedrock", "self_hosted"):
        op.execute(f"ALTER TYPE grc.cost_provider ADD VALUE IF NOT EXISTS '{value}'")
    for value in ("AZURE_AI_FOUNDRY", "SELF_HOSTED_AI"):
        op.execute(f"ALTER TYPE grc.integrationprovider ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "ai_cost_usage_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Client-supplied idempotency key. Unique per tenant, not globally:
        # two customers picking the same uuid must not collide.
        sa.Column("batch_id", sa.String(200), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_touched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default=sa.text("0")),
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
            ["integration_id"],
            ["grc.integrations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "batch_id",
            name="uq_grc_ai_cost_usage_batch_tenant_batch",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_ai_cost_usage_batches_tenant_id",
        "ai_cost_usage_batches",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_ai_cost_usage_batches_integration_id",
        "ai_cost_usage_batches",
        ["integration_id"],
        schema="grc",
    )

    op.execute("ALTER TABLE grc.ai_cost_usage_batches ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.ai_cost_usage_batches
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.ai_cost_usage_batches")
    op.drop_table("ai_cost_usage_batches", schema="grc")
    # Postgres cannot drop an enum value without rebuilding the type; the added
    # values are harmless if unused. Same stance as 037/009a.
