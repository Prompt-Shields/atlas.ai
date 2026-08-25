"""Device-scoped SSE notifier for directive delivery. Reuses the in-memory
SSEManager (asyncio queues) keyed by device_id (NOT tenant_id) so a device only
receives its own pings. The event is content-free — it just tells the device to
poll now; the poll endpoint remains the source of directive data and delivered
marking.

Single-process only (same limitation as the tenant SSE in dispatch_service).
Multi-replica fan-out (Redis pub/sub) is out of scope for Phase 3b.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.services.dispatch_service import SSEManager

device_directive_notifier = SSEManager()  # keyed by device_id


def directive_available_event() -> dict[str, Any]:
    return {"event": "directive_available", "data": {}}


async def device_directive_stream(device_id: uuid.UUID) -> AsyncGenerator[str, None]:
    queue = await device_directive_notifier.subscribe(device_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                data = json.dumps(event.get("data", {}))
                yield f"event: {event.get('event', 'message')}\ndata: {data}\n\n"
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        await device_directive_notifier.unsubscribe(device_id, queue)
