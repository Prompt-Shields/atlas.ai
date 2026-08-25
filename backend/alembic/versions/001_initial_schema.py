"""001 — Initial schema for AI Governance Security platform.

Revision ID: 001
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS grc")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.execute("DO $$ BEGIN CREATE TYPE grc.role AS ENUM ('SUPER_ADMIN','TENANT_ADMIN','ORG_ADMIN','ANALYST','VIEWER'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # ── Tenants ───────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )

    # ── Organisations ─────────────────────────────────────────────────
    op.create_table(
        "organisations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "slug", name="uq_organisations_tenant_slug"),
        schema="grc",
    )
    op.create_index("ix_organisations_tenant_id", "organisations", ["tenant_id"], schema="grc")

    # ── Users ─────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column(
            "is_email_verified",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.organisations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index("ix_users_email", "users", ["email"], schema="grc")
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], schema="grc")
    op.create_index("ix_users_org_id", "users", ["org_id"], schema="grc")

    # ── User Roles ────────────────────────────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM("SUPER_ADMIN", "TENANT_ADMIN", "ORG_ADMIN", "ANALYST", "VIEWER", name="role", schema="grc", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.organisations.id", ondelete="CASCADE"),
            nullable=True,
        ),
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
        sa.UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),
        schema="grc",
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"], schema="grc")

    # ── API Keys ──────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("hashed_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], schema="grc")

    # ── Invites ───────────────────────────────────────────────────────
    op.create_table(
        "invites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_used", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("is_revoked", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("role", sa.String(50), server_default=sa.text("'TENANT_ADMIN'"), nullable=False),
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
        schema="grc",
    )
    op.create_index("ix_invites_email", "invites", ["email"], schema="grc")
    op.create_index("ix_invites_tenant_id", "invites", ["tenant_id"], schema="grc")

    # ── Tenant Settings ───────────────────────────────────────────────
    op.create_table(
        "tenant_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("llm_budget_usd", sa.Float, nullable=True),
        sa.Column("custom_settings", sa.Text, nullable=True),
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
        schema="grc",
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"], schema="grc")

    # ── User Activity Blobs (pgvector) ────────────────────────────────
    op.create_table(
        "user_activity_blobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("adapter_name", sa.String(100), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("word_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index(
        "ix_user_activity_blobs_tenant_id", "user_activity_blobs", ["tenant_id"], schema="grc"
    )
    op.create_index(
        "ix_user_activity_blobs_org_id", "user_activity_blobs", ["org_id"], schema="grc"
    )
    op.create_index(
        "ix_user_activity_blobs_source_type",
        "user_activity_blobs",
        ["source_type"],
        schema="grc",
    )
    op.create_index(
        "ix_user_activity_blobs_content_hash",
        "user_activity_blobs",
        ["content_hash"],
        schema="grc",
    )
    op.create_index(
        "ix_user_activity_blobs_batch_id", "user_activity_blobs", ["batch_id"], schema="grc"
    )
    op.create_index(
        "ix_user_activity_blobs_processing_status",
        "user_activity_blobs",
        ["processing_status"],
        schema="grc",
    )

    # ── Source of Truth Blobs (pgvector) ──────────────────────────────
    op.create_table(
        "source_of_truth_blobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_category", sa.String(100), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "reported_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("word_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index(
        "ix_source_of_truth_blobs_tenant_id",
        "source_of_truth_blobs",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_source_of_truth_blobs_org_id",
        "source_of_truth_blobs",
        ["org_id"],
        schema="grc",
    )
    op.create_index(
        "ix_source_of_truth_blobs_content_hash",
        "source_of_truth_blobs",
        ["content_hash"],
        schema="grc",
    )
    op.create_index(
        "ix_source_of_truth_blobs_batch_id",
        "source_of_truth_blobs",
        ["batch_id"],
        schema="grc",
    )
    op.create_index(
        "ix_source_of_truth_blobs_processing_status",
        "source_of_truth_blobs",
        ["processing_status"],
        schema="grc",
    )

    # ── Risk Analyses (pgvector) ──────────────────────────────────────
    op.create_table(
        "risk_analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_of_truth_blob_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.source_of_truth_blobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column("risk_title", sa.String(500), nullable=False),
        sa.Column("risk_description", sa.Text, nullable=False),
        sa.Column("risk_category", sa.String(100), nullable=False),
        sa.Column("risk_severity", sa.String(20), nullable=False),
        sa.Column("risk_likelihood", sa.String(20), nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False),
        sa.Column("confidence_score", sa.Float, nullable=False),
        sa.Column("mitigation_title", sa.String(500), nullable=False),
        sa.Column("mitigation_description", sa.Text, nullable=False),
        sa.Column("mitigation_steps", sa.Text, nullable=False),
        sa.Column("citations", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column(
            "processing_status",
            sa.String(20),
            server_default=sa.text("'completed'"),
            nullable=False,
        ),
        sa.Column("created_by_job", sa.String(100), nullable=True),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index("ix_risk_analyses_tenant_id", "risk_analyses", ["tenant_id"], schema="grc")
    op.create_index("ix_risk_analyses_org_id", "risk_analyses", ["org_id"], schema="grc")
    op.create_index(
        "ix_risk_analyses_source_of_truth_blob_id",
        "risk_analyses",
        ["source_of_truth_blob_id"],
        schema="grc",
    )
    op.create_index("ix_risk_analyses_batch_id", "risk_analyses", ["batch_id"], schema="grc")

    # ── Governance Correlations (pgvector) ────────────────────────────
    op.create_table(
        "governance_correlations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_activity_blob_ids", sa.Text, nullable=False),
        sa.Column("risk_analysis_ids", sa.Text, nullable=False),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column("correlation_title", sa.String(500), nullable=False),
        sa.Column("correlation_summary", sa.Text, nullable=False),
        sa.Column("correlation_type", sa.String(50), nullable=False),
        sa.Column("overall_risk_score", sa.Float, nullable=False),
        sa.Column("confidence_score", sa.Float, nullable=False),
        sa.Column("score_reliability", sa.Float, nullable=False),
        sa.Column("match_tags", sa.Text, nullable=False),
        sa.Column("action_plan_title", sa.String(500), nullable=False),
        sa.Column("action_plan_description", sa.Text, nullable=False),
        sa.Column("action_steps", sa.Text, nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("estimated_effort", sa.String(50), nullable=True),
        sa.Column("citations", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("is_excluded", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column(
            "excluded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exclusion_reason", sa.Text, nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("created_by_job", sa.String(100), nullable=True),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index(
        "ix_governance_correlations_tenant_id",
        "governance_correlations",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_governance_correlations_org_id",
        "governance_correlations",
        ["org_id"],
        schema="grc",
    )
    op.create_index(
        "ix_governance_correlations_batch_id",
        "governance_correlations",
        ["batch_id"],
        schema="grc",
    )

    # ── Dispatch Events ───────────────────────────────────────────────
    op.create_table(
        "dispatch_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.governance_correlations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.organisations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("delivered", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("is_test_data", sa.Boolean, server_default=sa.text("false"), nullable=False),
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
        schema="grc",
    )
    op.create_index("ix_dispatch_events_tenant_id", "dispatch_events", ["tenant_id"], schema="grc")
    op.create_index(
        "ix_dispatch_events_correlation_id",
        "dispatch_events",
        ["correlation_id"],
        schema="grc",
    )
    op.create_index("ix_dispatch_events_org_id", "dispatch_events", ["org_id"], schema="grc")

    # ── Outbox Messages ───────────────────────────────────────────────
    op.create_table(
        "outbox_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("retry_count", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer, server_default=sa.text("5"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        schema="grc",
    )
    op.create_index("ix_outbox_messages_tenant_id", "outbox_messages", ["tenant_id"], schema="grc")
    op.create_index(
        "ix_outbox_messages_aggregate_id", "outbox_messages", ["aggregate_id"], schema="grc"
    )
    op.create_index("ix_outbox_messages_status", "outbox_messages", ["status"], schema="grc")

    # ── LLM Usage Records ─────────────────────────────────────────────
    op.create_table(
        "llm_usage_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_id", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("model_provider", sa.String(50), server_default=sa.text("'azure_openai'"), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("total_tokens", sa.Integer, nullable=False),
        sa.Column("estimated_cost_usd", sa.Float, nullable=False),
        sa.Column("operation_type", sa.String(50), nullable=False),
        sa.Column("request_metadata", sa.Text, nullable=True),
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
        schema="grc",
    )
    op.create_index(
        "ix_llm_usage_records_tenant_id", "llm_usage_records", ["tenant_id"], schema="grc"
    )
    op.create_index("ix_llm_usage_records_org_id", "llm_usage_records", ["org_id"], schema="grc")
    op.create_index("ix_llm_usage_records_job_id", "llm_usage_records", ["job_id"], schema="grc")

    # ── Audit Log ─────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        schema="audit",
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"], schema="audit")
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"], schema="audit")
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"], schema="audit")
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"], schema="audit")
    op.create_index(
        "ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"], schema="audit"
    )



def downgrade() -> None:
    for index_name, table_name, schema in [
        ("ix_audit_logs_correlation_id", "audit_logs", "audit"),
        ("ix_audit_logs_event_type", "audit_logs", "audit"),
        ("ix_audit_logs_actor_id", "audit_logs", "audit"),
        ("ix_audit_logs_tenant_id", "audit_logs", "audit"),
        ("ix_audit_logs_timestamp", "audit_logs", "audit"),
        ("ix_llm_usage_records_job_id", "llm_usage_records", "grc"),
        ("ix_llm_usage_records_org_id", "llm_usage_records", "grc"),
        ("ix_llm_usage_records_tenant_id", "llm_usage_records", "grc"),
        ("ix_outbox_messages_status", "outbox_messages", "grc"),
        ("ix_outbox_messages_aggregate_id", "outbox_messages", "grc"),
        ("ix_outbox_messages_tenant_id", "outbox_messages", "grc"),
        ("ix_dispatch_events_org_id", "dispatch_events", "grc"),
        ("ix_dispatch_events_correlation_id", "dispatch_events", "grc"),
        ("ix_dispatch_events_tenant_id", "dispatch_events", "grc"),
        ("ix_governance_correlations_batch_id", "governance_correlations", "grc"),
        ("ix_governance_correlations_org_id", "governance_correlations", "grc"),
        ("ix_governance_correlations_tenant_id", "governance_correlations", "grc"),
        ("ix_risk_analyses_batch_id", "risk_analyses", "grc"),
        ("ix_risk_analyses_source_of_truth_blob_id", "risk_analyses", "grc"),
        ("ix_risk_analyses_org_id", "risk_analyses", "grc"),
        ("ix_risk_analyses_tenant_id", "risk_analyses", "grc"),
        ("ix_source_of_truth_blobs_processing_status", "source_of_truth_blobs", "grc"),
        ("ix_source_of_truth_blobs_batch_id", "source_of_truth_blobs", "grc"),
        ("ix_source_of_truth_blobs_content_hash", "source_of_truth_blobs", "grc"),
        ("ix_source_of_truth_blobs_org_id", "source_of_truth_blobs", "grc"),
        ("ix_source_of_truth_blobs_tenant_id", "source_of_truth_blobs", "grc"),
        ("ix_user_activity_blobs_processing_status", "user_activity_blobs", "grc"),
        ("ix_user_activity_blobs_batch_id", "user_activity_blobs", "grc"),
        ("ix_user_activity_blobs_content_hash", "user_activity_blobs", "grc"),
        ("ix_user_activity_blobs_source_type", "user_activity_blobs", "grc"),
        ("ix_user_activity_blobs_org_id", "user_activity_blobs", "grc"),
        ("ix_user_activity_blobs_tenant_id", "user_activity_blobs", "grc"),
        ("ix_tenant_settings_tenant_id", "tenant_settings", "grc"),
        ("ix_invites_tenant_id", "invites", "grc"),
        ("ix_invites_email", "invites", "grc"),
        ("ix_api_keys_user_id", "api_keys", "grc"),
        ("ix_user_roles_user_id", "user_roles", "grc"),
        ("ix_users_org_id", "users", "grc"),
        ("ix_users_tenant_id", "users", "grc"),
        ("ix_users_email", "users", "grc"),
        ("ix_organisations_tenant_id", "organisations", "grc"),
    ]:
        op.drop_index(index_name, table_name=table_name, schema=schema)

    for table in [
        "audit.audit_logs",
        "grc.roles",
        "grc.llm_usage_records",
        "grc.outbox_messages",
        "grc.dispatch_events",
        "grc.governance_correlations",
        "grc.risk_analyses",
        "grc.source_of_truth_blobs",
        "grc.user_activity_blobs",
        "grc.tenant_settings",
        "grc.invites",
        "grc.api_keys",
        "grc.user_roles",
        "grc.users",
        "grc.organisations",
        "grc.tenants",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("DROP TYPE IF EXISTS grc.role")
