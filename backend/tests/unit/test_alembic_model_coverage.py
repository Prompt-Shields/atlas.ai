"""Every mapped table must appear somewhere in the migrations.

A cheap smoke test for the failure that hid for three months: commit 6312818
(2026-05-15) renamed three pipeline tables in the models — `blob_records`,
`risk_mitigations`, `correlation_action_plans` — without touching the
migration that creates them, so `alembic upgrade head` built a database whose
tables the application never queried. Nothing caught it, because
`tests/conftest.py` builds its schema from `Base.metadata.create_all` and never
runs a migration.

This is deliberately a substring check rather than a schema comparison: it runs
in the default sqlite suite with no database at all, and it is the one class of
drift that silently breaks every request against a freshly migrated database.
The real gate is `alembic check` against Postgres, which CI runs after
`alembic upgrade head` — see `alembic/MIGRATIONS.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# Finder-style copies ("030_pep_domain 2.py") are gitignored but may sit in a
# working tree; they are not part of the repo's migration set.
_DUPLICATE_COPY_RE = re.compile(r" \d+\.py$")


def _migration_text() -> str:
    parts = [
        p.read_text()
        for p in sorted(VERSIONS_DIR.glob("*.py"))
        if not _DUPLICATE_COPY_RE.search(p.name)
    ]
    assert parts, f"no migrations found in {VERSIONS_DIR}"
    return "\n".join(parts)


def _model_tables() -> dict[str, str]:
    """{table_name: model class name} for every mapped table."""
    import app.models  # noqa: F401 — registers every model on the metadata
    from app.models.base import Base

    out = {}
    for mapper_table in Base.metadata.tables.values():
        out[mapper_table.name] = mapper_table.fullname
    return out


def test_every_mapped_table_is_named_in_a_migration() -> None:
    blob = _migration_text()
    tables = _model_tables()
    assert len(tables) > 50, "model metadata looks empty — did the import fail?"

    missing = sorted(
        full for name, full in tables.items() if f'"{name}"' not in blob and f"'{name}'" not in blob
    )
    assert not missing, (
        "these tables are mapped by a model but named in no migration, so "
        "`alembic upgrade head` builds a database the app cannot query: " + ", ".join(missing)
    )
