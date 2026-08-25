"""User SSO foundation (PRO-54) — make hashed_password nullable and add
entra_object_id (unique, indexed) so users can be Entra-ID / SSO-provisioned.

Chains off 021_billing_v0.py.

COORDINATION NOTE: the in-flight AI-cost-ledger migration (PR #78,
022_ai_cost_records) also forks from 021_billing_v0 and claims revision "022".
Whichever of the two merges second must be renumbered (e.g. to 023) with its
down_revision rebased onto the other so there is a single linear head — this is
the same convention used for the 019/020/021 renumbers on prior branches.

Revision ID: 022
Revises: 021
Create Date: 2026-06-17
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "grc"


def upgrade() -> None:
    # SSO-only users have no local password.
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(255),
        nullable=True,
        schema=SCHEMA,
    )
    # Entra ID object id (`oid`); NULL for local users, unique across SSO users.
    op.add_column(
        "users",
        sa.Column("entra_object_id", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_users_entra_object_id",
        "users",
        ["entra_object_id"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_users_entra_object_id", table_name="users", schema=SCHEMA)
    op.drop_column("users", "entra_object_id", schema=SCHEMA)
    # Reverting to NOT NULL assumes no SSO-only (password-less) rows remain.
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(255),
        nullable=False,
        schema=SCHEMA,
    )
