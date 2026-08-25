"""Unit tests for services/billing/outbox.py — enqueue()."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Provide an async DB session using the shared test engine (schema_translate_map set)."""
    async with TestSessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_enqueue_inserts_row(db: AsyncSession):
    """enqueue() must add a BillingOutbox row with the given fields."""
    from app.models.billing import BillingOutbox
    from app.services.billing.outbox import enqueue

    tenant_id = uuid.uuid4()
    await enqueue(db, operation="seat_sync", tenant_id=tenant_id, payload={"seats": 3})
    await db.commit()

    result = await db.execute(select(BillingOutbox))
    rows = result.scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "seat_sync"
    assert row.tenant_id == tenant_id
    assert row.payload == {"seats": 3}
    assert row.attempt_count == 0


@pytest.mark.asyncio
async def test_enqueue_default_empty_payload(db: AsyncSession):
    """enqueue() with no payload defaults to empty dict."""
    from app.models.billing import BillingOutbox
    from app.services.billing.outbox import enqueue

    tenant_id = uuid.uuid4()
    await enqueue(db, operation="seat_sync", tenant_id=tenant_id)
    await db.commit()

    result = await db.execute(select(BillingOutbox))
    row = result.scalars().first()
    assert row is not None
    assert row.payload == {}
