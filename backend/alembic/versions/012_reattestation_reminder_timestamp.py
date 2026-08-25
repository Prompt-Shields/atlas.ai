"""Add UseCaseReview.last_reminder_sent_at for Slack reminder throttling.

Powers the `reattestation_reminder_dispatcher` worker that DMs use-case
owners when their reviews go OVERDUE. The timestamp throttles re-sends
to a 24h cooldown by default.

Revision ID: 012
Revises: 011
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "use_case_reviews",
        sa.Column(
            "last_reminder_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_case_reviews_last_reminder_sent_at",
        "use_case_reviews",
        ["last_reminder_sent_at"],
        schema="grc",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_grc_use_case_reviews_last_reminder_sent_at",
        table_name="use_case_reviews",
        schema="grc",
    )
    op.drop_column(
        "use_case_reviews", "last_reminder_sent_at", schema="grc"
    )
