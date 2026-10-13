"""Stripe module subscriptions per tenant, for module entitlements.

`grc.tenants.stripe_subscriptions`: for each of the tenant's module
subscriptions, its kind, Stripe status, and the price `lookup_key` and
quantity of each item, as of the last webhook event about it.

Promptly sells modules (Atlas.AI, Learn, Adopt, or Suite for all three) as
subscription items. They sit either on the enterprise subscription that starts
with the zero-price Work client, or on a separate invoiced subscription that a
Stripe Quote creates when it is accepted, and one customer can hold both
beside a self-serve Team subscription. The existing columns describe the Team
subscription only, and cannot say which modules a tenant has paid for. Items
are stored raw, not as a module list, so that a subscription whose invoice is
paid after a failed payment gets its modules back from `invoice.paid` alone,
which carries no item list.

No existing JSON column fits: `tenant_settings.custom_settings` is a Text blob
in a table that not every tenant has a row in, and it holds settings a tenant
admin edits, not state that only Stripe may write.

NOTE: numbered 049 to stay clear of open PRs holding 046-048. Renumber on
merge so that it follows whatever is then the head.

Revision ID: 049
Revises: 045
Create Date: 2026-10-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "049"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "stripe_subscriptions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="grc",
    )


def downgrade() -> None:
    op.drop_column("tenants", "stripe_subscriptions", schema="grc")
