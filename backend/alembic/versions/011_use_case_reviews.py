"""UseCaseReview table — periodic re-attestation cadence.

Customer-driven (Øystein Endal): "Holde informasjonen oppdatert"
— keep information up to date.

Revision ID: 011
Revises: 010
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    review_status = postgresql.ENUM(
        "DUE",
        "REVIEWED",
        "OVERDUE",
        "DISMISSED",
        name="reviewstatus",
        schema="grc",
        create_type=False,
    )
    review_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "use_case_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "use_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.use_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            review_status,
            nullable=False,
            server_default="DUE",
        ),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "drift_observed",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "dismiss_reason", sa.String(500), nullable=True
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
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_case_reviews_use_case_id",
        "use_case_reviews",
        ["use_case_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_case_reviews_due_at",
        "use_case_reviews",
        ["due_at"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_use_case_reviews_status",
        "use_case_reviews",
        ["status"],
        schema="grc",
    )

    op.execute(
        "ALTER TABLE grc.use_case_reviews ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.use_case_reviews
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.use_case_reviews"
    )
    op.drop_table("use_case_reviews", schema="grc")
    op.execute("DROP TYPE IF EXISTS grc.reviewstatus")
