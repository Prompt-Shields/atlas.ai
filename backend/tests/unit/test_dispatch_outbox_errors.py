"""The outbox keeps a delivery failure's text; it must not hold database row values."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.dispatch import OutboxMessage
from app.services import dispatch_service
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, ensure_tenant
from tests.db_errors import SECRET_VALUE, unique_violation

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def db(setup_database):
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        yield session


async def test_a_failed_delivery_stores_no_database_row_values(db, monkeypatch):
    message = OutboxMessage(
        tenant_id=TEST_TENANT_ID,
        event_type="seat.updated",
        aggregate_id=uuid.uuid4(),
        aggregate_type="seat",
        payload=json.dumps({"seat": 1}),
        status="pending",
    )
    db.add(message)
    await db.flush()

    async def failing_publish(*_args, **_kwargs):
        raise unique_violation()

    monkeypatch.setattr(dispatch_service.sse_manager, "publish", failing_publish)

    processed = await dispatch_service.process_outbox(db)

    row = (
        await db.execute(select(OutboxMessage).where(OutboxMessage.id == message.id))
    ).scalar_one()
    assert processed == 0
    assert row.retry_count == 1
    assert SECRET_VALUE not in row.last_error
    assert row.last_error.startswith("IntegrityError: [message withheld")
