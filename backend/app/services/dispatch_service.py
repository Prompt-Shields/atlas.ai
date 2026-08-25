"""Dispatch service — SSE channels and outbox processing."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispatch import OutboxMessage

logger = structlog.get_logger()


class SSEManager:
    """Manages per-tenant SSE channels for real-time event dispatch.

    Why SSE over WebSockets:
    - Simpler protocol, works through APIM and proxies without upgrade negotiation
    - Auto-reconnect built into the EventSource browser API
    - Sufficient for server→client streaming (which is our use case)
    - No persistent connection state management needed
    - Easier to scale horizontally
    """

    def __init__(self) -> None:
        self._channels: dict[uuid.UUID, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def subscribe(self, tenant_id: uuid.UUID) -> asyncio.Queue:
        """Subscribe to events for a tenant. Returns a queue to listen on."""
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._channels[tenant_id].append(queue)
        logger.debug("sse_subscribe", tenant_id=str(tenant_id))
        return queue

    async def unsubscribe(self, tenant_id: uuid.UUID, queue: asyncio.Queue) -> None:
        """Unsubscribe from tenant events."""
        async with self._lock:
            if tenant_id in self._channels:
                self._channels[tenant_id] = [q for q in self._channels[tenant_id] if q is not queue]
                if not self._channels[tenant_id]:
                    del self._channels[tenant_id]
        logger.debug("sse_unsubscribe", tenant_id=str(tenant_id))

    async def publish(self, tenant_id: uuid.UUID, event: dict[str, Any]) -> int:
        """Publish an event to all subscribers of a tenant. Returns subscriber count."""
        async with self._lock:
            queues = self._channels.get(tenant_id, [])
            for queue in queues:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("sse_queue_full", tenant_id=str(tenant_id))
        return len(queues)


# Singleton SSE manager
sse_manager = SSEManager()


async def stream_events(
    tenant_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a tenant. Used by the SSE endpoint."""
    queue = await sse_manager.subscribe(tenant_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                event_type = event.get("event", "message")
                data = json.dumps(event.get("data", event))
                event_id = event.get("id", "")
                yield f"event: {event_type}\ndata: {data}\nid: {event_id}\n\n"
            except TimeoutError:
                # Send keepalive
                yield ": keepalive\n\n"
    finally:
        await sse_manager.unsubscribe(tenant_id, queue)


async def process_outbox(db: AsyncSession, batch_size: int = 50) -> int:
    """Process pending outbox messages — poll and dispatch.

    Implements at-least-once delivery with retry and dead-letter handling.
    Returns number of messages processed.
    """
    now = datetime.now(UTC)

    # Fetch pending messages ready for processing
    result = await db.execute(
        select(OutboxMessage)
        .where(
            OutboxMessage.status.in_(["pending", "processing"]),
            (OutboxMessage.next_retry_at.is_(None)) | (OutboxMessage.next_retry_at <= now),
        )
        .order_by(OutboxMessage.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    messages = list(result.scalars().all())

    if not messages:
        return 0

    processed = 0
    for msg in messages:
        try:
            msg.status = "processing"
            await db.flush()

            # Parse and publish to SSE
            payload = json.loads(msg.payload)
            event = {
                "event": msg.event_type,
                "data": payload,
                "id": str(msg.id),
            }
            subscriber_count = await sse_manager.publish(msg.tenant_id, event)

            # Mark as delivered
            msg.status = "delivered"
            msg.processed_at = now
            processed += 1

            logger.info(
                "outbox_delivered",
                message_id=str(msg.id),
                event_type=msg.event_type,
                subscribers=subscriber_count,
            )

        except Exception as exc:
            msg.retry_count += 1
            msg.last_error = str(exc)

            if msg.retry_count >= msg.max_retries:
                msg.status = "dead_letter"
                logger.error(
                    "outbox_dead_letter",
                    message_id=str(msg.id),
                    error=str(exc),
                )
            else:
                # Exponential backoff: 2^retry * 10 seconds
                backoff = timedelta(seconds=10 * (2**msg.retry_count))
                msg.next_retry_at = now + backoff
                msg.status = "pending"
                logger.warning(
                    "outbox_retry",
                    message_id=str(msg.id),
                    retry=msg.retry_count,
                    next_retry=str(msg.next_retry_at),
                )

    await db.flush()
    return processed
