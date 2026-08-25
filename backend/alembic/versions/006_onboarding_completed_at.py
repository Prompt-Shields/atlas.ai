"""Tenant.onboarding_completed_at — onboarding wizard state.

PR 3 of the integrations + onboarding stack. Single column on the
existing tenants table. NULL = wizard not yet completed (or reset).

Revision ID: 006
Revises: 005
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="grc",
    )


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_completed_at", schema="grc")
