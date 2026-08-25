"""Extend grc.integrationprovider enum with SENTINEL.

Registers Microsoft Sentinel as a connectable integration provider
(issue #247, M5 of the AISPM port). Same ALTER TYPE pattern as
009a_mdm_provider_enum.py.

NOTE: as of this writing, versions/030_agent_discovery.py and
versions/030_pep_domain.py both declare revision="030" (same collision
at "031" and "032" across agent_control_state/pep_violations and
defender_import/mcp_discovery/use_case_source_voice_interview) — a
pre-existing revision-ID collision from parallel AISPM-port branches
that predates this migration and is out of scope here. This migration
chains onto "032" per the existing convention; once the collision is
resolved with an `alembic merge`, this file's down_revision may need
to be repointed at the merged revision.

Revision ID: 037
Revises: 036 (defender_import)
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE grc.integrationprovider ADD VALUE IF NOT EXISTS 'SENTINEL'"
    )


def downgrade() -> None:
    # Postgres doesn't support removing a value from an enum without
    # rebuilding the type — see 009a_mdm_provider_enum.py for the slow
    # path if a real removal is ever needed. No-op for the v0.1 rollback.
    pass
