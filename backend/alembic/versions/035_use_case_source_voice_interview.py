"""Extend grc.usecasesource enum with VOICE_INTERVIEW value.

Adds the VOICE_INTERVIEW source for use-case drafts extracted from the
Discover · Voice-interview intake flow (issue #242) — same pattern as
014_use_case_source_import.

Revision ID: 035
Revises: 034 (mcp_discovery)
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres enum extension — same pattern as 014_use_case_source_import.
    op.execute(
        "ALTER TYPE grc.usecasesource ADD VALUE IF NOT EXISTS 'VOICE_INTERVIEW'"
    )


def downgrade() -> None:
    # Postgres doesn't support removing a value from an enum without
    # rebuilding the type. No-op — see 014_use_case_source_import.
    pass
