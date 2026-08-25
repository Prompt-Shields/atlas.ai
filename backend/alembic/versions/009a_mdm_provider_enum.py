"""Extend grc.integrationprovider enum with non-Microsoft MDMs.

Adds JAMF_PRO, KANDJI, JUMPCLOUD values. See docs/mdm-strategy.md
for the why. Real adapters ship in v0.2; this migration just lets
the registry render Coming Soon cards.

Revision ID: 009
Revises: 008
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres enums need ALTER TYPE ... ADD VALUE — one statement
    # per new value. The default placement is at the end of the enum
    # values list; we don't enforce ordering.
    for value in ("JAMF_PRO", "KANDJI", "JUMPCLOUD"):
        op.execute(
            f"ALTER TYPE grc.integrationprovider ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # Postgres doesn't support removing a value from an enum without
    # rebuilding the type. For the v0.1 -> v0.2 rollback path this
    # is acceptable as a no-op; if a real removal is needed, take
    # the slow path:
    #   1. CREATE TYPE integrationprovider_new AS ENUM (...);
    #   2. ALTER TABLE integrations ALTER COLUMN provider TYPE
    #      integrationprovider_new USING provider::text::integrationprovider_new;
    #   3. DROP TYPE integrationprovider; rename _new.
    pass
