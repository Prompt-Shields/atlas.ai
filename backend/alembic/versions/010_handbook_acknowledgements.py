"""HandbookAcknowledgement table — per-user record of reading the
AI governance handbook.

Customer-driven: Øystein Endal (AI Risk Officer) flagged training +
awareness as the #1 priority. The handbook itself is curated
frontend content; this table records who has acknowledged the
current version.

Revision ID: 010
Revises: 009
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "handbook_acknowledgements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grc.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
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
        "ix_grc_handbook_user_id",
        "handbook_acknowledgements",
        ["user_id"],
        schema="grc",
    )
    op.create_index(
        "ix_grc_handbook_tenant_id",
        "handbook_acknowledgements",
        ["tenant_id"],
        schema="grc",
    )
    op.create_unique_constraint(
        "uq_grc_handbook_user_version",
        "handbook_acknowledgements",
        ["user_id", "version"],
        schema="grc",
    )

    op.execute(
        "ALTER TABLE grc.handbook_acknowledgements ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON grc.handbook_acknowledgements
        USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON grc.handbook_acknowledgements"
    )
    op.drop_table("handbook_acknowledgements", schema="grc")
