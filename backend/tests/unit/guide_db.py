"""The Guide resolvers, for tests on Postgres; and the clock Guide's routes read.

The test schema comes from ``Base.metadata.create_all``, not the migrations, so the
SECURITY DEFINER resolvers that 047 and 048 create are not there. The Guide code calls
them on Postgres (sqlite reads the tables directly), so a Guide test module uses
``guide_resolvers`` to create them from the migrations' own SQL.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.main import app
from app.routers.guide import guide_now
from tests.conftest import TEST_DATABASE_URL, engine

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _sql(migration: str, name: str) -> str:
    spec = importlib.util.spec_from_file_location(f"_guide_{name}", _VERSIONS / migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


_RESOLVERS = (
    _sql("047_guide_adoption.py", "RESOLVE_GUIDE_CONNECTION"),
    _sql("048_guide_scim.py", "RESOLVE_GUIDE_SCIM_TOKEN"),
)


@pytest_asyncio.fixture(autouse=True)
async def guide_resolvers(setup_database):  # noqa: ANN001, ARG001
    if not TEST_DATABASE_URL.startswith("sqlite"):
        async with engine.begin() as conn:
            for sql in _RESOLVERS:
                await conn.execute(text(sql))
    yield


# Intake for a month is open from its end until 00:00 UTC on the 7th of the month after,
# and its report is shown only from then (promptly-guide #27, E4). Tests send September
# while it is open and read it once it has closed, whatever day they run on.
SEPTEMBER = {"year": 2026, "month": 9}
SEPTEMBER_TEXT = "2026-09"
SENDING = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CLOSED = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)


@dataclass
class GuideClock:
    now: datetime


@pytest.fixture(name="guide_clock")
def guide_clock_fixture():
    """What Guide's routes take the time to be; `SENDING` until a test moves it."""
    clock = GuideClock(SENDING)
    app.dependency_overrides[guide_now] = lambda: clock.now
    yield clock
    app.dependency_overrides.pop(guide_now, None)
