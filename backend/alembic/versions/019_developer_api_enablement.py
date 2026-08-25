"""Developer API enablement — event definitions, events, key scopes.

Adds three tables under the `grc` schema:

- developer_api_key_scopes  : permission scopes attached to existing api_keys
- developer_event_definitions: tenant-defined event types
- developer_events           : ingested events

Also creates two enums: developer_scope and event_severity.

Revision ID: 019
Revises: 018
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "grc"
DEVELOPER_SCOPE_NAME = "developer_scope"
EVENT_SEVERITY_NAME = "event_severity"


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
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
    ]


def upgrade() -> None:
    # ── Enums ────────────────────────────────────────────────────────
    op.execute(
        f"CREATE TYPE {SCHEMA}.{DEVELOPER_SCOPE_NAME} AS ENUM "
        "('events:write', 'events:read', 'event_types:write', 'event_types:read')"
    )
    op.execute(
        f"CREATE TYPE {SCHEMA}.{EVENT_SEVERITY_NAME} AS ENUM "
        "('debug', 'info', 'warning', 'error', 'critical')"
    )

    # ── developer_api_key_scopes ─────────────────────────────────────
    op.create_table(
        "developer_api_key_scopes",
        *_audit_columns(),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scope",
            postgresql.ENUM(
                name=DEVELOPER_SCOPE_NAME,
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.UniqueConstraint("api_key_id", "scope", name="uq_api_key_scope"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_developer_api_key_scopes_api_key_id",
        "developer_api_key_scopes",
        ["api_key_id"],
        schema=SCHEMA,
    )

    # ── developer_event_definitions ──────────────────────────────────
    op.create_table(
        "developer_event_definitions",
        *_audit_columns(),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("schema", postgresql.JSONB, nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "default_severity",
            postgresql.ENUM(
                name=EVENT_SEVERITY_NAME,
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default="info",
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_event_def_tenant_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_event_defs_tenant_id",
        "developer_event_definitions",
        ["tenant_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_event_defs_name",
        "developer_event_definitions",
        ["name"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_event_defs_tenant_enabled",
        "developer_event_definitions",
        ["tenant_id", "is_enabled"],
        schema=SCHEMA,
    )

    # ── developer_events ─────────────────────────────────────────────
    op.create_table(
        "developer_events",
        *_audit_columns(),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM(
                name=EVENT_SEVERITY_NAME,
                schema=SCHEMA,
                create_type=False,
            ),
            nullable=False,
            server_default="info",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("session_id", sa.String(120), nullable=True),
        sa.Column("user_external_id", sa.String(255), nullable=True),
        sa.Column("correlation_id", sa.String(120), nullable=True),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "occurrences",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_tenant_id",
        "developer_events",
        ["tenant_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_event_type",
        "developer_events",
        ["event_type"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_occurred_at",
        "developer_events",
        ["occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_severity",
        "developer_events",
        ["severity"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_source",
        "developer_events",
        ["source"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_session_id",
        "developer_events",
        ["session_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_correlation_id",
        "developer_events",
        ["correlation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_api_key_id",
        "developer_events",
        ["api_key_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_tenant_occurred",
        "developer_events",
        ["tenant_id", "occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_tenant_type_occurred",
        "developer_events",
        ["tenant_id", "event_type", "occurred_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dev_events_tenant_severity",
        "developer_events",
        ["tenant_id", "severity"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("developer_events", schema=SCHEMA)
    op.drop_table("developer_event_definitions", schema=SCHEMA)
    op.drop_table("developer_api_key_scopes", schema=SCHEMA)
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.{EVENT_SEVERITY_NAME}")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.{DEVELOPER_SCOPE_NAME}")
