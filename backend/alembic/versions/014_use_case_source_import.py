"""Extend grc.usecasesource enum with IMPORT value.

Adds the IMPORT source for CSV-bulk-loaded use cases via the AI
inventory import endpoint (see app.services.ai_inventory_csv).
Distinguished from FORM so the audit trail records whether the
registration was admin-asserted (IMPORT) or user-asserted (FORM).

Revision ID: 014
Revises: 013
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres enum extension — same pattern as 009_mdm_provider_enum.
    op.execute(
        "ALTER TYPE grc.usecasesource ADD VALUE IF NOT EXISTS 'IMPORT'"
    )


def downgrade() -> None:
    # Postgres doesn't support removing a value from an enum without
    # rebuilding the type. No-op for the v0.1 → v0.2 rollback path.
    # If a real removal is ever needed, take the slow path:
    #   1. CREATE TYPE usecasesource_new AS ENUM (...);
    #   2. ALTER TABLE use_cases ALTER COLUMN source TYPE
    #      usecasesource_new USING source::text::usecasesource_new;
    #   3. DROP TYPE usecasesource; rename _new.
    pass
