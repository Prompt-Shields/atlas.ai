"""ai_cost_records ledger table + cost enums + RLS.

Revision ID: 023
Revises: 022 (user_sso_fields)
Create Date: 2026-06-17

Chains off 021_billing_v0 (current head). Creates the cost-ledger table,
its four grc enums, RLS tenant-isolation policy, and adds the AI-cost
connector values to the existing grc.integrationprovider enum.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False so the enums are created only by the explicit e.create()
# loop in upgrade() — without it, create_table() would re-emit CREATE TYPE and
# collide with the types already created in the same transaction.
_PROVIDER = postgresql.ENUM("anthropic", "openai", "cursor", "github_copilot", "vercel", name="cost_provider", schema="grc", create_type=False)
_KIND = postgresql.ENUM("metered_usage", "seat_subscription", "infra", name="cost_kind", schema="grc", create_type=False)
_SUBJECT = postgresql.ENUM("model", "member", "sku", "other", name="cost_subject_kind", schema="grc", create_type=False)
_SOURCE = postgresql.ENUM("vendor_reported", "derived_tokens", "derived_seats", name="cost_source", schema="grc", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for e in (_PROVIDER, _KIND, _SUBJECT, _SOURCE):
        e.create(bind, checkfirst=True)
    op.create_table(
        "ai_cost_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", _PROVIDER, nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("cost_kind", _KIND, nullable=False),
        sa.Column("subject_kind", _SUBJECT, nullable=False, server_default="other"),
        sa.Column("subject_ref", sa.String(255), nullable=False, server_default=""),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("cost_source", _SOURCE, nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "integration_id", "usage_date", "cost_kind", "subject_kind", "subject_ref", name="uq_grc_ai_cost_record_grain"),
        schema="grc",
    )
    op.create_index("ix_grc_ai_cost_records_tenant_date", "ai_cost_records", ["tenant_id", "usage_date"], schema="grc")
    op.create_index("ix_grc_ai_cost_records_tenant_provider_date", "ai_cost_records", ["tenant_id", "provider", "usage_date"], schema="grc")
    op.create_index("ix_grc_ai_cost_records_integration", "ai_cost_records", ["integration_id"], schema="grc")
    op.execute("ALTER TABLE grc.ai_cost_records ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON grc.ai_cost_records USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)")
    for v in ("ANTHROPIC", "OPENAI", "CURSOR", "GITHUB_COPILOT", "VERCEL"):
        op.execute(f"ALTER TYPE grc.integrationprovider ADD VALUE IF NOT EXISTS '{v}'")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON grc.ai_cost_records")
    op.drop_table("ai_cost_records", schema="grc")
    for e in (_PROVIDER, _KIND, _SUBJECT, _SOURCE):
        e.drop(op.get_bind(), checkfirst=True)
    # Postgres cannot DROP enum values; the integrationprovider additions remain.
