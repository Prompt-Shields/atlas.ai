"""Microsoft sync output tables — Intune devices, Purview events, CASB detections.

Three tables consumed by the new intune_sync / purview_sync /
defender_sync worker modes.

Revision ID: 008
Revises: 007
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common_audit_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "is_test_data",
            sa.Boolean,
            nullable=False,
            server_default="false",
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
    # ── intune_devices ───────────────────────────────────────────────
    op.create_table(
        "intune_devices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_device_id", sa.String(64), nullable=False),
        sa.Column("device_name", sa.String(255), nullable=False),
        sa.Column("operating_system", sa.String(50), nullable=True),
        sa.Column("os_version", sa.String(50), nullable=True),
        sa.Column("compliance_state", sa.String(50), nullable=True),
        sa.Column(
            "enrolled_user_principal_name", sa.String(320), nullable=True
        ),
        sa.Column(
            "last_sync_to_intune_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=False
        ),
        *_common_audit_columns(),
        schema="grc",
    )
    op.create_index(
        "ix_grc_intune_devices_tenant_id",
        "intune_devices",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_intune_devices_integration_id",
        "intune_devices",
        ["integration_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_intune_devices_external_device_id",
        "intune_devices",
        ["external_device_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_intune_devices_enrolled_upn",
        "intune_devices",
        ["enrolled_user_principal_name"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_intune_devices_tenant_integration_external",
        "intune_devices",
        ["tenant_id", "integration_id", "external_device_id"],
        schema="grc",
    )

    # ── purview_events ───────────────────────────────────────────────
    op.create_table(
        "purview_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "actor_user_principal_name", sa.String(320), nullable=True
        ),
        sa.Column("classifier_id", sa.String(128), nullable=True),
        sa.Column("raw_payload_json", sa.Text, nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=False
        ),
        *_common_audit_columns(),
        schema="grc",
    )
    op.create_index(
        "ix_grc_purview_events_tenant_id",
        "purview_events",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_purview_events_integration_id",
        "purview_events",
        ["integration_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_purview_events_actor_upn",
        "purview_events",
        ["actor_user_principal_name"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_purview_events_detected_at",
        "purview_events",
        ["detected_at"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_purview_events_external_event_id",
        "purview_events",
        ["external_event_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_purview_events_tenant_integration_external",
        "purview_events",
        ["tenant_id", "integration_id", "external_event_id"],
        schema="grc",
    )

    # ── cloud_app_detections ─────────────────────────────────────────
    op.create_table(
        "cloud_app_detections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_detection_id", sa.String(128), nullable=False),
        sa.Column("app_name", sa.String(255), nullable=False),
        sa.Column("app_domain", sa.String(255), nullable=True),
        sa.Column("app_category", sa.String(100), nullable=True),
        sa.Column("risk_score", sa.Integer, nullable=True),
        sa.Column(
            "is_sanctioned",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "user_principal_name", sa.String(320), nullable=True
        ),
        sa.Column(
            "event_count_30d",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=False
        ),
        *_common_audit_columns(),
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_tenant_id",
        "cloud_app_detections",
        ["tenant_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_integration_id",
        "cloud_app_detections",
        ["integration_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_app_name",
        "cloud_app_detections",
        ["app_name"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_app_domain",
        "cloud_app_detections",
        ["app_domain"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_user_upn",
        "cloud_app_detections",
        ["user_principal_name"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_last_seen_at",
        "cloud_app_detections",
        ["last_seen_at"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_cad_external_detection_id",
        "cloud_app_detections",
        ["external_detection_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_cad_tenant_integration_external",
        "cloud_app_detections",
        ["tenant_id", "integration_id", "external_detection_id"],
        schema="grc",
    )

    # ── RLS on all three ─────────────────────────────────────────────
    for table in ("intune_devices", "purview_events", "cloud_app_detections"):
        op.execute(f"ALTER TABLE grc.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON grc.{table}
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
            """
        )


def downgrade() -> None:
    for table in ("cloud_app_detections", "purview_events", "intune_devices"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON grc.{table}")
        op.drop_table(table, schema="grc")
