"""Free Work client grace: when it started, and whether an own endpoint is declared.

The business plan's guardrail on the free Work client (promptly-guide#52):
after 90 days without a paid module, the customer must connect its own model
endpoint to keep the client. Working that out needs two facts Atlas did not
hold:

  * `work_client_started_at`: day 0 of the 90. The tenant's `created_at` is
    not it, because an Atlas tenant can exist long before it deploys the Work
    client, and `trial_ends_at` belongs to Atlas's own trial.
  * `own_endpoint_declared_at`: when an admin declared that Guide answers from
    the organisation's own model endpoint. Atlas never sees the endpoint, so
    this records a declaration, not a check.

Both are nullable with no backfill: no tenant has started the Work client
through Atlas yet.

NUMBERING: 051 is a placeholder. Other open branches hold 046 and 047; renumber
this file and its `down_revision` onto whatever head `main` has at merge.

Revision ID: 051
Revises: 045
Create Date: 2026-10-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "051"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("work_client_started_at", sa.DateTime(timezone=True), nullable=True),
        schema="grc",
    )
    op.add_column(
        "tenants",
        sa.Column("own_endpoint_declared_at", sa.DateTime(timezone=True), nullable=True),
        schema="grc",
    )


def downgrade() -> None:
    op.drop_column("tenants", "own_endpoint_declared_at", schema="grc")
    op.drop_column("tenants", "work_client_started_at", schema="grc")
