"""Rename intune_devices → managed_devices + Microsoft-flavoured columns.

The IntuneDevice table is misleadingly named — Jamf/Kandji/JumpCloud
syncs already write to it. This migration is metadata-only on
Postgres (RENAME TABLE + RENAME COLUMN don't move data). Companion
to docs/design/managed-device-unification.md + PR #65's forward-
compat alias.

What changes:
  intune_devices                       → managed_devices
  enrolled_user_principal_name         → enrolled_user_email
  last_sync_to_intune_at               → last_sync_to_mdm_at

What does NOT change in this migration:
  compliance_state — each MDM has vendor-specific values
                     (compliant / noncompliant / inGracePeriod for
                     Intune; different sets for Jamf/Kandji/JumpCloud).
                     Unifying the enum is a separate v0.6 design
                     exercise that touches downstream charts.
  external_device_id, integration_id, device_name, operating_system,
  os_version, last_synced_at, tenant_id — already vendor-neutral.

Note: the column rename moves the index alongside it (Postgres
preserves indexes on RENAME COLUMN), so we don't recreate.

Revision ID: 018
Revises: 017
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table(
        "intune_devices",
        "managed_devices",
        schema="grc",
    )
    op.alter_column(
        "managed_devices",
        "enrolled_user_principal_name",
        new_column_name="enrolled_user_email",
        schema="grc",
    )
    op.alter_column(
        "managed_devices",
        "last_sync_to_intune_at",
        new_column_name="last_sync_to_mdm_at",
        schema="grc",
    )


def downgrade() -> None:
    op.alter_column(
        "managed_devices",
        "last_sync_to_mdm_at",
        new_column_name="last_sync_to_intune_at",
        schema="grc",
    )
    op.alter_column(
        "managed_devices",
        "enrolled_user_email",
        new_column_name="enrolled_user_principal_name",
        schema="grc",
    )
    op.rename_table(
        "managed_devices",
        "intune_devices",
        schema="grc",
    )
