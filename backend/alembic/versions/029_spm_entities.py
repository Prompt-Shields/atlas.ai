"""AI-SPM entity graph — people, org units, data stores, tech estate, references.

M1 of epic #134. Migration 028 landed the inventory half of
``lib/entities/types.ts`` (ai_assets ≙ Application, compliance_assessments);
this adds the remaining seven entities plus the polymorphic edge table.

Same shape as 028: UUID PKs, tenant_id + org_id, RLS per table. List-valued
columns are JSONB. ``entity_references`` carries no FKs on source/target —
the pair is polymorphic — so integrity lives in the service layer.

Revision ID: 029
Revises: 028
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES = [
    "organizational_units",
    "people",
    "data_stores",
    "technology_services",
    "technology_products",
    "technical_capabilities",
    "entity_references",
]


def _base_columns() -> list[sa.Column]:
    """id / tenant_id / org_id / timestamps / is_test_data — shared by all seven."""
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    ]


def upgrade() -> None:
    # ── Organizational units (self-nesting; created first so people can FK it) ──
    op.create_table(
        "organizational_units",
        *_base_columns(),
        sa.Column("component_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.organizational_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_organizational_units_tenant_id",
        "organizational_units",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_organizational_units_org_id",
        "organizational_units",
        ["org_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_organizational_units_component_name",
        "organizational_units",
        ["component_name"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_organizational_units_external_id",
        "organizational_units",
        ["external_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_organizational_units_parent_id",
        "organizational_units",
        ["parent_id"],
        schema="grc",
    )

    # ── People ────────────────────────────────────────────────────────
    op.create_table(
        "people",
        *_base_columns(),
        sa.Column("component_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("role", sa.String(255), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column(
            "organizational_unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.organizational_units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    for col in [
        "tenant_id",
        "org_id",
        "component_name",
        "email",
        "external_id",
        "organizational_unit_id",
        "user_id",
    ]:
        op.create_index(f"ix_grc_people_{col}", "people", [col], schema="grc")

    # ── Data stores ───────────────────────────────────────────────────
    op.create_table(
        "data_stores",
        *_base_columns(),
        sa.Column("component_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "store_type",
            sa.String(50),
            server_default="other",
            nullable=False,
            comment=(
                "relational_database, document_store, object_store, data_warehouse, "
                "cache, search_index, other"
            ),
        ),
        sa.Column(
            "data_classification",
            sa.String(20),
            server_default="internal",
            nullable=False,
            comment="public, internal, confidential, restricted",
        ),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("record_count", sa.String(100), nullable=True),
        sa.Column("refresh_frequency", sa.String(100), nullable=True),
        sa.Column("retention_period", sa.String(100), nullable=True),
        sa.Column("encryption", sa.String(255), nullable=True),
        sa.Column("access_controls", sa.Text(), nullable=True),
        sa.Column("gdpr_compliant", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_audit", sa.Date(), nullable=True),
        sa.Column(
            "data_owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    for col in ["tenant_id", "org_id", "component_name", "data_owner_id"]:
        op.create_index(f"ix_grc_data_stores_{col}", "data_stores", [col], schema="grc")

    # ── Technology services ───────────────────────────────────────────
    op.create_table(
        "technology_services",
        *_base_columns(),
        sa.Column("component_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(255), nullable=True),
        sa.Column(
            "service_type",
            sa.String(50),
            server_default="other",
            nullable=False,
            comment=(
                "cloud_infrastructure, ai_platform, api_gateway, identity_provider, "
                "database_service, other"
            ),
        ),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            server_default="active",
            nullable=False,
            comment="active, deprecated",
        ),
        sa.Column("uptime_pct", sa.Float(), nullable=True),
        sa.Column("cost_per_month_nok", sa.Float(), nullable=True),
        sa.Column(
            "security_certifications",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("network_isolation", sa.String(255), nullable=True),
        sa.Column("scaling_policy", sa.String(255), nullable=True),
        sa.Column("backup_strategy", sa.String(255), nullable=True),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    for col in ["tenant_id", "org_id", "component_name", "provider"]:
        op.create_index(
            f"ix_grc_technology_services_{col}", "technology_services", [col], schema="grc"
        )

    # ── Technology products ───────────────────────────────────────────
    op.create_table(
        "technology_products",
        *_base_columns(),
        sa.Column("component_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(255), nullable=True),
        sa.Column("version", sa.String(100), nullable=True),
        sa.Column(
            "model_category",
            sa.String(50),
            server_default="other",
            nullable=False,
            comment=("llm, computer_vision, speech, image_generation, machine_learning, other"),
        ),
        sa.Column("parameters", sa.String(100), nullable=True),
        sa.Column(
            "lifecycle_status",
            sa.String(20),
            server_default="production",
            nullable=False,
            comment="production, evaluation, deprecated",
        ),
        sa.Column("input_cost_per_mtokens", sa.String(50), nullable=True),
        sa.Column("output_cost_per_mtokens", sa.String(50), nullable=True),
        sa.Column("context_window", sa.String(50), nullable=True),
        sa.Column("estimated_monthly_spend_nok", sa.Float(), nullable=True),
        sa.Column(
            "promptly_app_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    for col in ["tenant_id", "org_id", "component_name", "provider"]:
        op.create_index(
            f"ix_grc_technology_products_{col}", "technology_products", [col], schema="grc"
        )

    # ── Technical capabilities (seeded taxonomy) ──────────────────────
    op.create_table(
        "technical_capabilities",
        *_base_columns(),
        sa.Column("level1", sa.String(255), nullable=False),
        sa.Column("level2", sa.String(255), nullable=True),
        sa.Column("level3", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        schema="grc",
    )
    for col in ["tenant_id", "org_id", "level1", "level2"]:
        op.create_index(
            f"ix_grc_technical_capabilities_{col}",
            "technical_capabilities",
            [col],
            schema="grc",
        )

    # ── Entity references (polymorphic edges) ─────────────────────────
    op.create_table(
        "entity_references",
        *_base_columns(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_type",
            sa.String(50),
            nullable=False,
            comment=(
                "person, organizational_unit, ai_asset, data_store, technology_service, "
                "technology_product, technical_capability, compliance_assessment"
            ),
        ),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column(
            "reference_type",
            sa.String(50),
            nullable=False,
            comment=(
                "is_realized_by, deploys, is_owner_of, observed_use, belongs_to, "
                "reads_from, runs_on, has_subject"
            ),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("auto_derived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("observation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="grc",
    )
    for col in ["tenant_id", "org_id", "source_id", "target_id", "reference_type"]:
        op.create_index(f"ix_grc_entity_references_{col}", "entity_references", [col], schema="grc")
    # An edge is unique by (source, target, type) within a tenant — re-observing
    # one bumps observation_count rather than inserting a duplicate.
    op.create_unique_constraint(
        "uq_entity_reference_edge",
        "entity_references",
        ["tenant_id", "source_id", "target_id", "reference_type"],
        schema="grc",
    )

    # ── RLS ───────────────────────────────────────────────────────────
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    # Reverse dependency order: edges → leaves → people → org units.
    for table in [
        "entity_references",
        "technical_capabilities",
        "technology_products",
        "technology_services",
        "data_stores",
        "people",
        "organizational_units",
    ]:
        op.drop_table(table, schema="grc")
