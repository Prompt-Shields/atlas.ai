"""The Guide resolvers, for tests on Postgres.

The test schema comes from ``Base.metadata.create_all``, not the migrations, so the
SECURITY DEFINER resolvers that 046 and 047 create are not there. The Guide code calls
them on Postgres (sqlite reads the tables directly), so a Guide test module uses
``guide_resolvers`` to create them from the migrations' own SQL.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text

from tests.conftest import TEST_DATABASE_URL, engine

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _sql(migration: str, name: str) -> str:
    spec = importlib.util.spec_from_file_location(f"_guide_{name}", _VERSIONS / migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


_RESOLVERS = (
    _sql("046_guide_adoption.py", "RESOLVE_GUIDE_CONNECTION"),
    _sql("047_guide_scim.py", "RESOLVE_GUIDE_SCIM_TOKEN"),
)


@pytest_asyncio.fixture(autouse=True)
async def guide_resolvers(setup_database):  # noqa: ANN001, ARG001
    if not TEST_DATABASE_URL.startswith("sqlite"):
        async with engine.begin() as conn:
            for sql in _RESOLVERS:
                await conn.execute(text(sql))
    yield
