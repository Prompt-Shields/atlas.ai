"""TenantDriftSignal table — per-tenant overrides for the drift rule engine.

Adds the model + the two new Postgres enums (tenant_drift_signal_kind,
tenant_drift_signal_severity).

Revision ID: 016
Revises: 015
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_type=False so the column references below don't ALSO emit
    # a CREATE TYPE — we create the types explicitly with checkfirst=True.
    # Without this, create_table re-issues CREATE TYPE and Postgres
    # raises DuplicateObjectError. (Matches the pattern in migration 002.)
    kind_enum = postgresql.ENUM(
        "MODEL_DEPRECATION",
        "TOOL_REBRAND",
        name="tenantdriftsignalkind",
        schema="grc",
        create_type=False,
    )
    severity_enum = postgresql.ENUM(
        "LOW",
        "MEDIUM",
        "HIGH",
        name="tenantdriftsignalseverity",
        schema="grc",
        create_type=False,
    )
    kind_enum.create(op.get_bind(), checkfirst=True)
    severity_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tenant_drift_signals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", kind_enum, nullable=False),
        sa.Column("match_pattern", sa.String(100), nullable=False),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column(
            "severity",
            severity_enum,
            nullable=False,
            server_default="MEDIUM",
        ),
        sa.Column(
            "successor_or_current_name", sa.String(255), nullable=True
        ),
        sa.Column("effective_as_of", sa.String(20), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint(
            "tenant_id",
            "kind",
            "match_pattern",
            name="uq_grc_tenant_drift_signal_match",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_tenant_drift_signals_kind",
        "tenant_drift_signals",
        ["kind"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_tenant_drift_signals_is_active",
        "tenant_drift_signals",
        ["is_active"],
        schema="grc",
    )
    op.execute(
        "ALTER TABLE grc.tenant_drift_signals ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.tenant_drift_signals
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.tenant_drift_signals"
    )
    op.drop_table("tenant_drift_signals", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.tenantdriftsignalseverity")
    op.execute("DROP TYPE IF EXISTS grc.tenantdriftsignalkind")
