"""Promptly Guide: connection and team adoption counts (promptly-guide #37, #38).

Four tenant-scoped tables, RLS-isolated, plus the SECURITY DEFINER resolver
``grc.resolve_guide_connection`` for the one pre-tenant lookup (a Firebase ID
token names a project, not an Atlas tenant), built the way 038 built
``grc.resolve_device_by_token``.

``guide_adoption_contributors``, ``guide_adoption_counts`` and
``guide_adoption_receipts`` deliberately have no timestamp columns: a receipt
and a count updated in the same second could be lined up, and that would tie a
person's monthly receipt to their team and categories. See
``app/models/guide_adoption.py``.

Revision ID: 046
Revises: 045
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "guide_connections",
    "guide_adoption_contributors",
    "guide_adoption_counts",
    "guide_adoption_receipts",
)


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("uuid_generate_v4()"),
    )


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "guide_connections",
        _id(),
        _tenant(),
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
        sa.Column("firebase_project_id", sa.String(128), nullable=False),
        sa.Column("firebase_tenant_id", sa.String(128), nullable=False),
        sa.Column("offered_kinds", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_guide_connections_tenant"),
        sa.UniqueConstraint(
            "firebase_project_id", "firebase_tenant_id", name="uq_guide_connections_firebase"
        ),
        schema="grc",
    )
    op.create_table(
        "guide_adoption_contributors",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column("people", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "period", "team", name="uq_guide_adoption_contributors"),
        schema="grc",
    )
    op.create_table(
        "guide_adoption_counts",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("team", sa.String(100), nullable=False),
        sa.Column("category_kind", sa.String(20), nullable=False),
        sa.Column("category_id", sa.String(120), nullable=False),
        sa.Column("people", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "period",
            "team",
            "category_kind",
            "category_id",
            name="uq_guide_adoption_counts",
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_guide_adoption_counts_period",
        "guide_adoption_counts",
        ["tenant_id", "period"],
        schema="grc",
    )
    op.create_table(
        "guide_adoption_receipts",
        _id(),
        _tenant(),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("receipt", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "period", "receipt", name="uq_guide_adoption_receipts"),
        schema="grc",
    )

    for table in _TABLES:
        # tenant_id single-column index — matches TenantScopedMixin(index=True).
        op.create_index(f"ix_grc_{table}_tenant_id", table, ["tenant_id"], schema="grc")
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )

    # ── SECURITY DEFINER connection resolver (pre-tenant lookup; bypasses RLS) ──
    op.execute(
        """
        CREATE OR REPLACE FUNCTION grc.resolve_guide_connection(p_project text, p_tenant text)
        RETURNS TABLE (tenant_id uuid, offered_kinds json)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = grc
        AS $$
            SELECT tenant_id, offered_kinds
            FROM grc.guide_connections
            WHERE firebase_project_id = p_project AND firebase_tenant_id = p_tenant
            LIMIT 1
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS grc.resolve_guide_connection(text, text)")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="grc")
