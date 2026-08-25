"""Integration-test fixtures.

The root conftest.py overrides get_db_session without committing — suitable
for unit tests that run all assertions in a single request. Integration tests
that make multiple HTTP requests and need cross-request persistence require
the session to commit after each request, mirroring the real get_db_session.

This module re-applies the override with a commit so that data inserted by
one request is visible to the next.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.main import app
from tests.conftest import TestSessionLocal


async def _override_get_db_session_with_commit() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db_session] = _override_get_db_session_with_commit
