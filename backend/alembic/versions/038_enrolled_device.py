"""EnrolledDevice — device registry for per-device PEP authentication.

First table of the device-policy Phase 1 workstream. Adds
``grc.enrolled_devices`` (tenant-scoped, RLS-isolated) plus the SECURITY
DEFINER resolver ``grc.resolve_device_by_token``.

The resolver is the crux of later per-device auth: the token→identity lookup
must happen BEFORE any tenant is known (pre-tenant bootstrap), so it has to
bypass tenant RLS. SECURITY DEFINER runs it with the definer's rights,
sidestepping the ``tenant_isolation_enrolled_devices`` policy for this one
narrow, hash-keyed read.

NOTE: the sqlite test DB has no RLS and treats ``set_tenant_guc`` as a no-op,
so tests cannot validate the RLS bypass — this must be verified on Postgres.

Revision ID: 038
Revises: 037 (sentinel_provider_enum)
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrolled_devices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("user_external_id", sa.String(320), nullable=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("app_version", sa.String(50), nullable=True),
        sa.Column(
            "enrollment_state",
            sa.String(20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(100), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="grc",
    )
    # tenant_id single-column index — matches TenantScopedMixin(index=True).
    op.create_index(
        "ix_grc_enrolled_devices_tenant_id",
        "enrolled_devices",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_enrolled_devices_tenant_seen",
        "enrolled_devices",
        ["tenant_id", "last_seen_at"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_enrolled_devices_token_hash",
        "enrolled_devices",
        ["token_hash"],
        unique=True,
        schema="grc",
    )

    # ── RLS ───────────────────────────────────────────────────────────
    op.execute("ALTER TABLE grc.enrolled_devices ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_enrolled_devices ON grc.enrolled_devices
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )

    # ── SECURITY DEFINER token resolver (pre-tenant lookup; bypasses RLS) ──
    op.execute(
        """
        CREATE OR REPLACE FUNCTION grc.resolve_device_by_token(p_hash text)
        RETURNS TABLE (device_id uuid, tenant_id uuid, enrollment_state text)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = grc
        AS $$
            SELECT id, tenant_id, enrollment_state
            FROM grc.enrolled_devices
            WHERE token_hash = p_hash
            LIMIT 1
        $$;
        """
    )


def downgrade() -> None:
    # Drop the resolver function first, then the table (RLS policy drops with it).
    op.execute("DROP FUNCTION IF EXISTS grc.resolve_device_by_token(text)")
    op.drop_table("enrolled_devices", schema="grc")
