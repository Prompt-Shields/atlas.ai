from __future__ import annotations

import uuid

import pytest

from app.services.device_directive_sse import (
    device_directive_notifier,
    directive_available_event,
)

pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_publish_reaches_a_subscribed_device_queue():
    device_id = uuid.uuid4()
    queue = await device_directive_notifier.subscribe(device_id)
    try:
        n = await device_directive_notifier.publish(device_id, directive_available_event())
        assert n == 1
        event = await queue.get()
        assert event["event"] == "directive_available"
    finally:
        await device_directive_notifier.unsubscribe(device_id, queue)


@pytest.mark.asyncio
async def test_publish_isolated_to_the_target_device():
    dev_a, dev_b = uuid.uuid4(), uuid.uuid4()
    qa = await device_directive_notifier.subscribe(dev_a)
    qb = await device_directive_notifier.subscribe(dev_b)
    try:
        await device_directive_notifier.publish(dev_a, directive_available_event())
        assert not qa.empty()
        assert qb.empty()
    finally:
        await device_directive_notifier.unsubscribe(dev_a, qa)
        await device_directive_notifier.unsubscribe(dev_b, qb)
