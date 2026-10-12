"""purge_test_data: removes test rows, outbox messages included, and nothing else.

Outbox messages carry no test flag of their own. Each one is about a correlation
(`aggregate_id`), so it is test data exactly when that correlation is. The purge used
to filter `OutboxMessage.is_test_data`, which does not exist, and so failed every time.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.correlation import CorrelationActionPlan
from app.models.dispatch import OutboxMessage
from app.services.testing_service import purge_test_data
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000c2")


def _correlation(*, is_test_data: bool) -> CorrelationActionPlan:
    return CorrelationActionPlan(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        org_id=ORG,
        risk_mitigation_ids="[]",
        correlation_title="probe",
        correlation_summary="probe",
        correlation_type="pattern",
        overall_risk_score=0.5,
        confidence_score=0.5,
        action_plan_title="probe",
        action_plan_description="probe",
        action_steps="[]",
        priority="low",
        citations="[]",
        reasoning="probe",
        model_used="test",
        is_test_data=is_test_data,
    )


def _outbox(correlation: CorrelationActionPlan) -> OutboxMessage:
    return OutboxMessage(
        tenant_id=TENANT,
        event_type="correlation_created",
        aggregate_id=correlation.id,
        aggregate_type="correlation",
        payload="{}",
    )


async def test_purge_removes_test_outbox_messages_and_keeps_real_ones() -> None:
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TENANT)
        test_corr = _correlation(is_test_data=True)
        real_corr = _correlation(is_test_data=False)
        session.add_all([test_corr, real_corr])
        await session.flush()
        session.add_all([_outbox(test_corr), _outbox(real_corr)])
        await session.commit()
        real_id = real_corr.id

    async with TestSessionLocal() as session:
        counts = await purge_test_data(session, user_id=uuid.uuid4())
        await session.commit()

    assert counts["outbox_messages"] == 1
    assert counts["correlations"] == 1
    async with TestSessionLocal() as session:
        left = (await session.execute(select(OutboxMessage.aggregate_id))).scalars().all()
        assert left == [real_id]
        correlations = await session.scalar(select(func.count(CorrelationActionPlan.id)))
        assert correlations == 1


async def test_a_tenant_purge_leaves_other_tenants_test_outbox_messages() -> None:
    other = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TENANT)
        await ensure_tenant(session, other)
        mine = _correlation(is_test_data=True)
        theirs = _correlation(is_test_data=True)
        theirs.tenant_id = other
        session.add_all([mine, theirs])
        await session.flush()
        their_message = _outbox(theirs)
        their_message.tenant_id = other
        session.add_all([_outbox(mine), their_message])
        await session.commit()
        their_id = theirs.id

    async with TestSessionLocal() as session:
        counts = await purge_test_data(session, user_id=uuid.uuid4(), tenant_id=TENANT)
        await session.commit()

    assert counts["outbox_messages"] == 1
    async with TestSessionLocal() as session:
        left = (await session.execute(select(OutboxMessage.aggregate_id))).scalars().all()
        assert left == [their_id]
