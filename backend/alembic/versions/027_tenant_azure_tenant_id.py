"""Tenant.azure_tenant_id (SP-Azure) — the Entra `tid` claim a tenant
self-provisioned from, nullable + unique so one Azure directory maps to at
most one Atlas tenant.

Chains off 026_saas_vendor_ai.py (current head).

Revision ID: 027
Revises: 026
Create Date: 2026-07-02
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "grc"


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("azure_tenant_id", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenants_azure_tenant_id",
        "tenants",
        ["azure_tenant_id"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenants_azure_tenant_id", table_name="tenants", schema=SCHEMA
    )
    op.drop_column("tenants", "azure_tenant_id", schema=SCHEMA)
