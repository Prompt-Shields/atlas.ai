"""Unit tests for get_tenant_db_session (sqlite-safe; no Postgres required)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_tenant_db_session
from app.errors import ForbiddenError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def test_super_admin_session_unchanged_no_guc() -> None:
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@test.local",
        roles=["SUPER_ADMIN"],
        tenant_id=TENANT_ID,
    )
    db = AsyncMock(spec=AsyncSession)

    with patch("app.auth.dependencies.set_tenant_guc", new_callable=AsyncMock) as mock_set:
        result = await get_tenant_db_session(user=user, db=db)

    assert result is db
    mock_set.assert_not_called()


async def test_tenant_user_sets_guc_on_session() -> None:
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="tenant@test.local",
        roles=["TENANT_ADMIN"],
        tenant_id=TENANT_ID,
    )
    db = AsyncMock(spec=AsyncSession)

    with patch("app.auth.dependencies.set_tenant_guc", new_callable=AsyncMock) as mock_set:
        result = await get_tenant_db_session(user=user, db=db)

    assert result is db
    mock_set.assert_awaited_once_with(db, TENANT_ID)


async def test_non_super_admin_without_tenant_raises() -> None:
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="orphan@test.local",
        roles=["ANALYST"],
        tenant_id=None,
    )
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ForbiddenError, match="Tenant context required"):
        await get_tenant_db_session(user=user, db=db)
