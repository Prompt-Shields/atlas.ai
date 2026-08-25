"""Billing v0 — Plan/SubscriptionStatus enums, tenant billing columns,
user activity timestamps, trial_events, billing_outbox, stripe_webhook_events.

Chains off 019_developer_api_enablement.py.

COORDINATION NOTE: This migration and the in-flight telemetry migration (019_prompt_events)
both fork from 018_managed_device_rename on some branches. On this branch telemetry
landed first as 019_developer_api_enablement. If another migration merges after this one,
rebase its down_revision onto "020" so there is a single linear head.

Revision ID: 021
Revises: 020
Create Date: 2026-06-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "grc"
PLAN_ENUM = "plan"
SUBSCRIPTION_STATUS_ENUM = "subscription_status"


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    op.execute(
        f"CREATE TYPE {SCHEMA}.{PLAN_ENUM} AS ENUM ('free', 'pro', 'team', 'enterprise')"
    )
    op.execute(
        f"CREATE TYPE {SCHEMA}.{SUBSCRIPTION_STATUS_ENUM} AS ENUM "
        "('trialing', 'active', 'past_due', 'canceled')"
    )

    # ── Tenant billing columns (8 new cols) ───────────────────────────────────
    # plan: NOT NULL with server_default so existing rows get 'free'
    op.add_column(
        "tenants",
        sa.Column(
            "plan",
            postgresql.ENUM(name=PLAN_ENUM, schema=SCHEMA, create_type=False),
            nullable=False,
            server_default="free",
        ),
        schema=SCHEMA,
    )
    # seats_billed: NOT NULL, default 1
    op.add_column(
        "tenants",
        sa.Column(
            "seats_billed",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
        schema=SCHEMA,
    )
    # stripe_customer_id: nullable, unique + index
    op.add_column(
        "tenants",
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenants_stripe_customer_id",
        "tenants",
        ["stripe_customer_id"],
        unique=True,
        schema=SCHEMA,
    )
    # stripe_subscription_id: nullable
    op.add_column(
        "tenants",
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    # stripe_subscription_item_id: nullable
    op.add_column(
        "tenants",
        sa.Column("stripe_subscription_item_id", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    # subscription_status: nullable enum
    op.add_column(
        "tenants",
        sa.Column(
            "subscription_status",
            postgresql.ENUM(
                name=SUBSCRIPTION_STATUS_ENUM, schema=SCHEMA, create_type=False
            ),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    # trial_ends_at: nullable, indexed (used for hard-lock clock comparisons)
    op.add_column(
        "tenants",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenants_trial_ends_at",
        "tenants",
        ["trial_ends_at"],
        schema=SCHEMA,
    )
    # plan_changed_at: nullable, no index
    op.add_column(
        "tenants",
        sa.Column("plan_changed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # ── User activity timestamps (3 new cols) ─────────────────────────────────
    op.add_column(
        "users",
        sa.Column("first_login_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "users",
        sa.Column(
            "first_prompt_observed_at", sa.DateTime(timezone=True), nullable=True
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "users",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_users_last_active_at",
        "users",
        ["last_active_at"],
        schema=SCHEMA,
    )

    # ── trial_events ──────────────────────────────────────────────────────────
    # Tenant-scoped daily snapshot; BigInteger PK (not UUID GRCBase).
    op.create_table(
        "trial_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "users_invited", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "users_activated", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "prompts_observed", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "prompts_redacted", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "extension_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "llm_cost_usd",
            sa.Numeric(8, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("csm_nudge_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "converted_to_paid_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("convert_intent_signal", sa.String(120), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_date",
            name="uq_trial_events_tenant_date",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_trial_events_tenant_date",
        "trial_events",
        ["tenant_id", "snapshot_date"],
        schema=SCHEMA,
    )
    # RLS: tenant-scoped
    op.execute(
        f"ALTER TABLE {SCHEMA}.trial_events ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.trial_events
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )

    # ── billing_outbox ────────────────────────────────────────────────────────
    # Durable retry queue for Stripe seat-sync failures; tenant-scoped.
    # Distinct from OutboxMessage (app/models/dispatch.py).
    op.create_table(
        "billing_outbox",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_billing_outbox_pending",
        "billing_outbox",
        ["succeeded_at", "next_attempt_at"],
        schema=SCHEMA,
    )
    # RLS: tenant-scoped
    op.execute(
        f"ALTER TABLE {SCHEMA}.billing_outbox ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.billing_outbox
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )

    # ── stripe_webhook_events ─────────────────────────────────────────────────
    # Global idempotency log (no tenant_id) — NO RLS.
    op.create_table(
        "stripe_webhook_events",
        sa.Column("stripe_event_id", sa.String(255), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.Text, nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drop tables (children first, FK order)
    op.execute(
        f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.billing_outbox"
    )
    op.execute(
        f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.trial_events"
    )
    op.drop_table("stripe_webhook_events", schema=SCHEMA)
    op.drop_table("billing_outbox", schema=SCHEMA)
    op.drop_table("trial_events", schema=SCHEMA)

    # Drop user columns
    op.drop_index("ix_users_last_active_at", table_name="users", schema=SCHEMA)
    op.drop_column("users", "last_active_at", schema=SCHEMA)
    op.drop_column("users", "first_prompt_observed_at", schema=SCHEMA)
    op.drop_column("users", "first_login_at", schema=SCHEMA)

    # Drop tenant columns + their indexes
    op.drop_column("tenants", "plan_changed_at", schema=SCHEMA)
    op.drop_index("ix_tenants_trial_ends_at", table_name="tenants", schema=SCHEMA)
    op.drop_column("tenants", "trial_ends_at", schema=SCHEMA)
    op.drop_column("tenants", "subscription_status", schema=SCHEMA)
    op.drop_column("tenants", "stripe_subscription_item_id", schema=SCHEMA)
    op.drop_column("tenants", "stripe_subscription_id", schema=SCHEMA)
    op.drop_index(
        "ix_tenants_stripe_customer_id", table_name="tenants", schema=SCHEMA
    )
    op.drop_column("tenants", "stripe_customer_id", schema=SCHEMA)
    op.drop_column("tenants", "seats_billed", schema=SCHEMA)
    op.drop_column("tenants", "plan", schema=SCHEMA)

    # Drop enums last (after all dependent columns are gone)
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.{SUBSCRIPTION_STATUS_ENUM}")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.{PLAN_ENUM}")
