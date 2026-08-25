"""DirectoryUser.manager_external_user_id (PRO-56).

Stores each Graph user's manager id so manager / direct-report relations are
available; direct reports are derivable (users whose manager id == this user's
external id), so no extra table.

Chains off 024_directory_group_memberships.

Revision ID: 025
Revises: 024
Create Date: 2026-06-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "grc"


def upgrade() -> None:
    op.add_column(
        "directory_users",
        sa.Column("manager_external_user_id", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_grc_directory_users_manager_external_user_id",
        "directory_users",
        ["manager_external_user_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_grc_directory_users_manager_external_user_id",
        table_name="directory_users",
        schema=SCHEMA,
    )
    op.drop_column("directory_users", "manager_external_user_id", schema=SCHEMA)
