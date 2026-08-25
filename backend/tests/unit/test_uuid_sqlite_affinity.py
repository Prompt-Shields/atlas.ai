"""Regression: all-digit UUIDs must survive the SQLite round-trip.

The ORM declares UUID columns with ``postgresql.UUID``. On SQLite that DDL
renders the column type as ``UUID``, which SQLite assigns NUMERIC affinity.
The character-based UUID bind processor stores ``value.hex`` (no hyphens), so
an all-decimal-digit UUID like ``00000000-0000-0000-0000-000000000010``
(hex ``00000000000000000000000000000010``) gets silently coerced to the
integer ``10``. Reading it back then runs ``uuid.UUID(10)`` and explodes with
``AttributeError: 'int' object has no attribute 'replace'``.

Our deterministic test fixtures (TEST_TENANT_ID, SUPER_ADMIN_ID, …) are exactly
such all-digit UUIDs, so this broke every test that reads a created row back.
conftest forces UUID columns to ``CHAR(32)`` (TEXT affinity) on SQLite to fix
it. Production uses native Postgres UUID and was never affected.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.use_case import UseCase
from tests.conftest import TestSessionLocal

# A UUID whose hex form is entirely decimal digits — the shape that triggers
# SQLite NUMERIC-affinity coercion.
ALL_DIGIT_UUID = uuid.UUID("00000000-0000-0000-0000-000000000010")


@pytest.mark.asyncio
async def test_all_digit_uuid_round_trips_without_int_coercion() -> None:
    assert ALL_DIGIT_UUID.hex == "00000000000000000000000000000010"

    async with TestSessionLocal() as session:
        record = UseCase(
            id=ALL_DIGIT_UUID,
            tenant_id=ALL_DIGIT_UUID,
            title="affinity probe",
            tool="probe",
            department="probe",
            source="FORM",
            data_classes="[]",
        )
        session.add(record)
        await session.commit()

        # Stored as TEXT, not coerced to an integer.
        stored_type = (
            await session.execute(text("SELECT typeof(tenant_id) FROM use_cases"))
        ).scalar_one()
        assert stored_type == "text"

        # Re-reading the row must not raise and must give back a real UUID.
        await session.refresh(record)
        assert record.tenant_id == ALL_DIGIT_UUID
        assert isinstance(record.tenant_id, uuid.UUID)
