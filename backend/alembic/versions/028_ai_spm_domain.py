"""AI-SPM domain schema — assets, use-cases, risks, model-risk, compliance (tables + RLS).

Re-port of the SP-1/M2 AI-SPM inventory domain onto current main. The branch's
parallel policy engine (policies/policy_instances/policy_violations/
policy_approval_requests) is intentionally omitted — main ships its own policy
engine — so this migration creates only the five inventory tables.

Revision ID: 028
Revises: 027
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── AI assets ────────────────────────────────────────────────────
    op.create_table(
        "ai_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("deployment_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("hosting_location", sa.String(50), nullable=True),
        sa.Column("model_type", sa.String(50), nullable=True),
        sa.Column("is_third_party", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("risk_score", sa.Float, nullable=True),
        sa.Column("gdpr_status", sa.String(20), nullable=True),
        sa.Column("eu_ai_act_status", sa.String(20), nullable=True),
        sa.Column("nist_ai_rmf_status", sa.String(20), nullable=True),
        sa.Column("eu_ai_act_risk_tier", sa.String(30), nullable=True),
        sa.Column("accuracy", sa.Float, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("drift_score", sa.Float, nullable=True),
        sa.Column("monthly_calls", sa.Integer, nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index("ix_grc_ai_assets_tenant_id", "ai_assets", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_ai_assets_org_id", "ai_assets", ["org_id"], schema="grc")
    op.create_index("ix_grc_ai_assets_name", "ai_assets", ["name"], schema="grc")
    op.create_index("ix_grc_ai_assets_owner_id", "ai_assets", ["owner_id"], schema="grc")

    # ── AI use cases ────────────────────────────────────────────────
    op.create_table(
        "ai_use_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.ai_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("department", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "business_owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("data_classification", sa.String(50), nullable=True),
        sa.Column("model_ids", postgresql.JSONB, nullable=True),
        sa.Column("risk_ids", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("discovered_via", sa.String(50), nullable=True),
        sa.Column("created_at_in_use", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index("ix_grc_ai_use_cases_tenant_id", "ai_use_cases", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_ai_use_cases_org_id", "ai_use_cases", ["org_id"], schema="grc")
    op.create_index("ix_grc_ai_use_cases_asset_id", "ai_use_cases", ["asset_id"], schema="grc")
    op.create_index("ix_grc_ai_use_cases_name", "ai_use_cases", ["name"], schema="grc")
    op.create_index(
        "ix_grc_ai_use_cases_business_owner_id",
        "ai_use_cases",
        ["business_owner_id"],
        schema="grc",
    )

    # ── AI risks ────────────────────────────────────────────────────
    op.create_table(
        "ai_risks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "use_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.ai_use_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("owasp_ref", sa.String(100), nullable=True),
        sa.Column("nist_ref", sa.String(100), nullable=True),
        sa.Column("eu_ai_act_ref", sa.String(100), nullable=True),
        sa.Column("mitigation_status", sa.String(30), nullable=True),
        sa.Column("mitigations", postgresql.JSONB, nullable=True),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index("ix_grc_ai_risks_tenant_id", "ai_risks", ["tenant_id"], schema="grc")
    op.create_index("ix_grc_ai_risks_org_id", "ai_risks", ["org_id"], schema="grc")
    op.create_index("ix_grc_ai_risks_use_case_id", "ai_risks", ["use_case_id"], schema="grc")
    op.create_index("ix_grc_ai_risks_name", "ai_risks", ["name"], schema="grc")

    # ── Model risk profiles ─────────────────────────────────────────
    op.create_table(
        "model_risk_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("parameters", sa.Text, nullable=True),
        sa.Column("lifecycle_status", sa.String(50), nullable=True),
        sa.Column("overall_risk", sa.Integer, nullable=True),
        sa.Column("hallucination_risk", sa.Integer, nullable=True),
        sa.Column("bias_risk", sa.Integer, nullable=True),
        sa.Column("toxicity_risk", sa.Integer, nullable=True),
        sa.Column("privacy_risk", sa.Integer, nullable=True),
        sa.Column("security_risk", sa.Integer, nullable=True),
        sa.Column("compliance_risk", sa.Integer, nullable=True),
        sa.Column("key_risks", postgresql.JSONB, nullable=True),
        sa.Column("approved_use_cases", postgresql.JSONB, nullable=True),
        sa.Column("risk_alerts", postgresql.JSONB, nullable=True),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index(
        "ix_grc_model_risk_profiles_tenant_id",
        "model_risk_profiles",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_model_risk_profiles_org_id",
        "model_risk_profiles",
        ["org_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_model_risk_profiles_name",
        "model_risk_profiles",
        ["name"],
        schema="grc",
    )

    # ── Compliance assessments ───────────────────────────────────────
    op.create_table(
        "compliance_assessments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.ai_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("framework", sa.String(20), nullable=False),
        sa.Column("risk_level", sa.String(50), nullable=True),
        sa.Column("passed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("review_date", sa.Date, nullable=True),
        sa.Column("live_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_status", sa.String(20), nullable=True),
        sa.Column(
            "approved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_test_data", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="grc",
    )
    op.create_index(
        "ix_grc_compliance_assessments_tenant_id",
        "compliance_assessments",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_compliance_assessments_org_id",
        "compliance_assessments",
        ["org_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_compliance_assessments_asset_id",
        "compliance_assessments",
        ["asset_id"],
        schema="grc",
    )

    # ── RLS policies (org-scoped tables only) ────────────────────────
    for table in [
        "ai_assets",
        "ai_use_cases",
        "ai_risks",
        "model_risk_profiles",
        "compliance_assessments",
    ]:
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    for table in [
        "compliance_assessments",
        "model_risk_profiles",
        "ai_risks",
        "ai_use_cases",
        "ai_assets",
    ]:
        op.drop_table(table, schema="grc")

