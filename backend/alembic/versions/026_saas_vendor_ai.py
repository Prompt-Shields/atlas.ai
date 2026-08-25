"""SaaS Vendor AI — vendor profile + assessment-exchange tables + enums + RLS.

Revision ID: 026
Revises: 025 (directory_user_manager)
Create Date: 2026-07-01

DRAFT — SP-1 (SaaS Vendor AI). Chains off 025 (current head). A "SaaS vendor"
is an existing grc.use_cases row that also has a grc.saas_vendor_profiles row —
the profile's PRESENCE is the discriminator, so no column is added to use_cases
and the existing ai_inventory aggregates stay unaffected (they anti-join on the
profile when they want to exclude third-party vendors).

Creates:
- grc.saas_vendor_profiles        (1:1 with use_cases; vendor-only fields)
- grc.vendor_assessment_requests  (outbound "request assessment")
- grc.vendor_assessment_imports   (inbound "import assessment")
plus 5 grc enums and tenant-isolation RLS on all three tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: enums are created only by the explicit e.create() loop,
# otherwise create_table() re-emits CREATE TYPE and collides in-transaction.
_CATEGORY = postgresql.ENUM(
    "llm_provider", "productivity_suite", "developer_tool", "crm_support", "data_analytics",
    name="vendor_category", schema="grc", create_type=False,
)
_DISCOVERY = postgresql.ENUM(
    "endpoint", "browser_extension", "sso", "integration", "manual",
    name="vendor_discovery_method", schema="grc", create_type=False,
)
_CONTRACT = postgresql.ENUM(
    "active", "pending_renewal", "expired",
    name="vendor_contract_status", schema="grc", create_type=False,
)
_ASSESS_STATUS = postgresql.ENUM(
    "draft", "sent", "received", "reviewed",
    name="assessment_status", schema="grc", create_type=False,
)
_IMPORT_SOURCE = postgresql.ENUM(
    "peer_shared", "trust_center", "upload",
    name="assessment_import_source", schema="grc", create_type=False,
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON grc.{table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)"
    )


def upgrade() -> None:
    bind = op.get_bind()
    for e in (_CATEGORY, _DISCOVERY, _CONTRACT, _ASSESS_STATUS, _IMPORT_SOURCE):
        e.create(bind, checkfirst=True)

    # --- vendor profile (1:1 with use_cases) ---------------------------------
    op.create_table(
        "saas_vendor_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("use_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.use_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", _CATEGORY, nullable=False),
        sa.Column("discovered_via", _DISCOVERY, nullable=False, server_default="manual"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("models", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sub_processors", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("data_flows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("certifications", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("compliance_status", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dpa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("training_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("contract_status", _CONTRACT, nullable=False, server_default="active"),
        sa.Column("last_reviewed_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_saas_vendor_profiles_risk_score_range"),
        sa.UniqueConstraint("use_case_id", name="uq_saas_vendor_profile_use_case"),
        schema="grc",
    )
    op.create_index("ix_grc_saas_vendor_profiles_tenant", "saas_vendor_profiles", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_saas_vendor_profiles_tenant_risk", "saas_vendor_profiles", ["tenant_id", "risk_score"], schema="grc")
    _rls("saas_vendor_profiles")

    # --- outbound assessment requests ----------------------------------------
    op.create_table(
        "vendor_assessment_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("use_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.use_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("questionnaire", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", _ASSESS_STATUS, nullable=False, server_default="draft"),
        sa.Column("token", sa.String(128), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index("ix_grc_vendor_assess_req_tenant_vendor", "vendor_assessment_requests", ["tenant_id", "use_case_id"], schema="grc")
    _rls("vendor_assessment_requests")

    # --- inbound assessment imports ------------------------------------------
    op.create_table(
        "vendor_assessment_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("use_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grc.use_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", _IMPORT_SOURCE, nullable=False),
        sa.Column("reference", sa.String(1024), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("imported_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index("ix_grc_vendor_assess_imp_tenant_vendor", "vendor_assessment_imports", ["tenant_id", "use_case_id"], schema="grc")
    _rls("vendor_assessment_imports")


def downgrade() -> None:
    for table in ("vendor_assessment_imports", "vendor_assessment_requests", "saas_vendor_profiles"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON grc.{table}")
        op.drop_table(table, schema="grc")
    for e in (_IMPORT_SOURCE, _ASSESS_STATUS, _CONTRACT, _DISCOVERY, _CATEGORY):
        e.drop(op.get_bind(), checkfirst=True)
